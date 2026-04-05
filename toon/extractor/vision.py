from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from toon.ai_client import AIClient, parse_json_response
from toon.extractor.prompts import (
    ATTRIBUTION_SYSTEM_PROMPT,
    ATTRIBUTION_PROMPT,
    build_known_characters_hint,
)
from toon.models import Dialogue, PanelExtraction

if TYPE_CHECKING:
    from toon.config import _Settings
    import toon.db as db_module
else:
    _Settings = Any


WINDOW_SIZE = 3       # Consecutive panels per attribution call
OCR_CONCURRENCY = 8   # Parallel Apple Vision threads (Neural Engine handles this well)


def _apple_ocr_file(file_path: str) -> list[dict[str, Any]]:
    """
    OCR a single image file using Apple Vision framework (Neural Engine).
    Thread-safe when called with initWithURL (no Quartz CGImage).
    Returns [{text, confidence, cy}] sorted top-to-bottom.
    """
    import Vision  # type: ignore
    import Foundation  # type: ignore

    url = Foundation.NSURL.fileURLWithPath_(file_path)
    results: list[dict[str, Any]] = []

    def _handler(request, error):  # noqa: ANN001
        for obs in (request.results() or []):
            cands = obs.topCandidates_(1)
            if cands:
                results.append({
                    "text": str(cands[0].string()),
                    "confidence": float(cands[0].confidence()),
                    # Vision y=0 is bottom; invert for top-down ordering
                    "cy": 1.0 - float(obs.boundingBox().origin.y),
                })

    req = Vision.VNRecognizeTextRequest.alloc().initWithCompletionHandler_(_handler)
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(False)
    req.setRecognitionLanguages_(["en-US", "ko-KR"])
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, {})
    handler.performRequests_error_([req], None)
    return sorted(results, key=lambda r: r["cy"])


def _ocr_image_apple(image_path: Path) -> list[dict[str, Any]]:
    """
    Slice a tall webtoon strip into 2000px chunks, OCR each with Apple Vision,
    and return merged results with absolute y-coordinates.
    """
    from PIL import Image as _PIL

    img = _PIL.open(image_path).convert("RGB")
    w, h = img.size
    chunk_h, overlap = 2000, 100
    results_all: list[dict[str, Any]] = []
    y, i = 0, 0

    while y < h:
        y_end = min(y + chunk_h, h)
        chunk = img.crop((0, y, w, y_end))
        tmp = str(image_path.parent / f".ocr_tmp_{image_path.stem}_{i}.jpg")
        chunk.save(tmp, "JPEG", quality=85)
        try:
            chunk_results = _apple_ocr_file(tmp)
            ch = y_end - y
            for r in chunk_results:
                if r["confidence"] < 0.45 or len(r["text"].strip()) < 2:
                    continue
                # cy is normalised [0,1] within the chunk; map to absolute pixels
                abs_cy = y + r["cy"] * ch
                results_all.append({
                    "text": r["text"].strip(),
                    "confidence": round(r["confidence"], 2),
                    "cx": float(w / 2),
                    "cy": abs_cy,
                    "x0": 0, "y0": int(abs_cy - 20),
                    "x1": w, "y1": int(abs_cy + 20),
                })
        finally:
            try:
                Path(tmp).unlink(missing_ok=True)
            except Exception:
                pass
        y += chunk_h - overlap
        i += 1

    return sorted(results_all, key=lambda r: r["cy"])


async def _ocr_panel(
    image_path: Path,
    sem: asyncio.Semaphore,
) -> list[dict[str, Any]]:
    """Run Apple Vision OCR in a thread pool, up to OCR_CONCURRENCY in parallel."""
    async with sem:
        return await asyncio.to_thread(_ocr_image_apple, image_path)


def _estimate_position(cx: float, cy: float, img_w: float = 1000, img_h: float = 1400) -> str:
    """Convert (cx, cy) to a named position within the panel."""
    col = "left" if cx < img_w / 3 else ("right" if cx > 2 * img_w / 3 else "center")
    row = "top" if cy < img_h / 3 else ("bottom" if cy > 2 * img_h / 3 else "middle")
    if row == "middle" and col == "center":
        return "center"
    return f"{row}-{col}"


def _format_ocr_for_prompt(panel_texts: list[dict], panel_index: int) -> str:
    """Format OCR results for the LLM attribution prompt."""
    if not panel_texts:
        return f"Panel {panel_index}: (no text detected)\n"
    lines = [f"Panel {panel_index} text regions (top-to-bottom order):"]
    for i, t in enumerate(sorted(panel_texts, key=lambda x: x["cy"])):
        lines.append(f"  [{i}] \"{t['text']}\" (conf={t['confidence']:.2f})")
    return "\n".join(lines)


async def _attribute_window(
    ocr_results: list[list[dict]],
    panel_indices: list[int],
    known_characters: list[str],
    client: AIClient,
    semaphore: asyncio.Semaphore,
) -> list[PanelExtraction]:
    """Use GLM-5 to assign speakers to OCR-extracted text regions."""
    async with semaphore:
        hint = build_known_characters_hint(known_characters)
        panels_text = "\n\n".join(
            _format_ocr_for_prompt(ocr_results[i], i) for i in range(len(ocr_results))
        )
        prompt = ATTRIBUTION_PROMPT.format(
            panels_text=panels_text,
            n_panels=len(ocr_results),
            known_characters_hint=hint,
        )

        raw = ""
        for attempt in range(3):
            raw = await asyncio.to_thread(
                client.chat,
                [{"role": "user", "content": prompt}],
                ATTRIBUTION_SYSTEM_PROMPT,
            )
            if raw:
                break
            await asyncio.sleep(2 ** attempt)

        try:
            data = parse_json_response(raw) if raw else {"panels": []}
        except Exception:
            data = {"panels": []}

        panels_data: list[dict] = data.get("panels", [])
        results: list[PanelExtraction] = []

        for win_i, ocr_texts in enumerate(ocr_results):
            actual_idx = panel_indices[win_i]
            panel_data = panels_data[win_i] if win_i < len(panels_data) else {}
            attributed = panel_data.get("dialogues", [])

            # Build dialogues: prefer LLM attribution; fall back to UNKNOWN
            dialogues: list[Dialogue] = []
            if attributed:
                for item in attributed:
                    dialogues.append(Dialogue(
                        speaker=item.get("speaker", "UNKNOWN"),
                        text=item.get("text", ""),
                        bubble_position=item.get("bubble_position", ""),
                        confidence=item.get("confidence", 0.7),
                    ))
            else:
                # No attribution — create UNKNOWN entries from raw OCR
                for t in sorted(ocr_texts, key=lambda x: x["cy"]):
                    dialogues.append(Dialogue(
                        speaker="UNKNOWN",
                        text=t["text"],
                        bubble_position="",
                        confidence=t["confidence"],
                    ))

            results.append(PanelExtraction(
                panel_index=actual_idx,
                image_file="",  # set by caller
                dialogues=dialogues,
                scene_description=panel_data.get("scene_description", ""),
            ))
        return results


async def extract_chapter(
    chapter_id: int,
    series_slug: str,
    chapter_num: int,
    client: "AIClient",
    settings: "_Settings",
    db: "db_module",
    source_lang: str = "vi",
    progress_cb: Any = None,  # async callable(stage: str, detail: str) | None
    skip_attribution: bool = False,  # Skip GLM-5 speaker attribution (faster, use for Learn flow)
) -> int:
    """Extract all panels using parallel Apple Vision OCR + optional GLM-5 attribution. Returns total dialogue count."""
    images_dir = settings.data_dir / "images" / series_slug / f"{chapter_num:03d}"
    image_paths = sorted(
        [p for p in images_dir.glob("*.*") if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} and p.stat().st_size > 10_000],
        key=lambda p: p.name,
    )

    if not image_paths:
        return 0

    db.delete_panels_for_chapter(chapter_id)

    async def _notify(stage: str, detail: str = "") -> None:
        if progress_cb:
            try:
                await progress_cb(stage, detail)
            except Exception:
                pass

    # Step 1: OCR all panels in parallel via Apple Vision (Neural Engine)
    await _notify("ocr_start", f"{len(image_paths)} panels")
    ocr_sem = asyncio.Semaphore(OCR_CONCURRENCY)
    completed = 0

    async def _ocr_one(path: Path) -> list[dict]:
        nonlocal completed
        result = await _ocr_panel(path, ocr_sem)
        completed += 1
        await _notify("ocr_panel", f"{completed}/{len(image_paths)}")
        return result

    all_ocr: list[list[dict]] = await asyncio.gather(
        *[_ocr_one(p) for p in image_paths]
    )

    # Step 2: Speaker attribution — skip for Learn flow to save API calls
    all_extractions: list[PanelExtraction] = []

    if skip_attribution:
        # Save all OCR results as UNKNOWN speaker — fast, no API calls
        for i, (path, ocr_texts) in enumerate(zip(image_paths, all_ocr)):
            dialogues = [
                Dialogue(speaker="UNKNOWN", text=t["text"], bubble_position="", confidence=t["confidence"])
                for t in sorted(ocr_texts, key=lambda x: x["cy"])
            ]
            all_extractions.append(PanelExtraction(
                panel_index=i,
                image_file=str(path),
                dialogues=dialogues,
                scene_description="",
            ))
        await _notify("attribution_panel", f"{len(image_paths)}/{len(image_paths)}")
    else:
        await _notify("attribution_start", f"{len(image_paths)} panels")
        semaphore = asyncio.Semaphore(settings.max_concurrent)
        known_characters: list[str] = []

        attr_done = 0
        tasks = []
        for i in range(0, len(image_paths), WINDOW_SIZE):
            window_ocr = all_ocr[i:i + WINDOW_SIZE]
            window_indices = list(range(i, i + len(window_ocr)))
            tasks.append(_attribute_window(window_ocr, window_indices, list(known_characters), client, semaphore))

        windows = await asyncio.gather(*tasks)
        for win_i, window_results in enumerate(windows):
            base = win_i * WINDOW_SIZE
            for j, extraction in enumerate(window_results):
                extraction.image_file = str(image_paths[base + j])
                all_extractions.append(extraction)
                for d in extraction.dialogues:
                    if d.speaker not in ("NARRATOR", "SFX", "UNKNOWN") and d.speaker not in known_characters:
                        known_characters.append(d.speaker)
            attr_done += len(window_results)
            await _notify("attribution_panel", f"{attr_done}/{len(image_paths)}")

    # Step 3: Save to DB
    all_extractions.sort(key=lambda e: e.panel_index)
    total_dialogues = 0
    for extraction in all_extractions:
        panel_id = db.save_panel(
            chapter_id,
            extraction.panel_index,
            extraction.image_file,
            extraction.scene_description,
        )
        for dialogue in extraction.dialogues:
            db.save_dialogue(
                panel_id,
                dialogue.speaker,
                dialogue.text,
                dialogue.bubble_position,
                dialogue.confidence,
            )
            total_dialogues += 1

    # Step 4: Save OCR bbox data for reader (text replacement)
    import json as _json
    ocr_data = {i: all_ocr[i] for i in range(len(all_ocr))}
    (images_dir / "_ocr_boxes.json").write_text(_json.dumps(ocr_data), encoding="utf-8")

    # Step 5: Save human-readable text file for inspection
    txt_path = images_dir / "_dialogues.txt"
    lines = []
    for extraction in all_extractions:
        lines.append(f"=== Panel {extraction.panel_index} ===")
        if extraction.scene_description:
            lines.append(f"[Scene: {extraction.scene_description}]")
        for d in extraction.dialogues:
            lines.append(f"[{d.speaker}] {d.text}")
        lines.append("")
    txt_path.write_text("\n".join(lines), encoding="utf-8")

    return total_dialogues
