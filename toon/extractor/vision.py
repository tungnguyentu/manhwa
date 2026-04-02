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


WINDOW_SIZE = 3  # Number of consecutive panels processed together for context


_OCR_READERS: dict[str, Any] = {}


def _get_ocr_reader(langs: tuple[str, ...]) -> Any:
    """Lazy-load EasyOCR reader for the given language tuple."""
    import warnings
    import easyocr
    from pathlib import Path as _Path
    # Ensure EasyOCR model directory exists (prevents temp.zip error on first download)
    model_dir = _Path.home() / ".EasyOCR" / "model"
    model_dir.mkdir(parents=True, exist_ok=True)
    key = ",".join(langs)
    if key not in _OCR_READERS:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=".*pin_memory.*", category=UserWarning)
            _OCR_READERS[key] = easyocr.Reader(list(langs), gpu=False, verbose=False)
    return _OCR_READERS[key]


def _ocr_image(image_path: Path, langs: tuple[str, ...] = ("en", "vi")) -> list[dict[str, Any]]:
    """Run EasyOCR on a single image. Returns list of {text, confidence, bbox}.
    Slices tall webtoon strips into chunks to avoid EasyOCR memory/accuracy issues.
    """
    import warnings
    from PIL import Image

    img = Image.open(image_path).convert("RGB")
    w, h = img.size

    # Resize width if too wide
    max_width = 1000
    if w > max_width:
        scale = max_width / w
        img = img.resize((max_width, int(h * scale)), Image.LANCZOS)
        w, h = img.size

    reader = _get_ocr_reader(langs)
    results_all: list[dict[str, Any]] = []

    # Slice tall images into ~2000px chunks with 100px overlap
    chunk_h = 2000
    overlap = 100

    y = 0
    while y < h:
        y_end = min(y + chunk_h, h)
        chunk = img.crop((0, y, w, y_end))
        tmp = image_path.with_suffix(f".ocr_tmp_{y}.jpg")
        chunk.save(tmp, "JPEG", quality=85)
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message=".*pin_memory.*", category=UserWarning)
                chunk_results = reader.readtext(str(tmp), detail=1)
            for bbox, text, conf in chunk_results:
                if conf >= 0.3 and text.strip():
                    # Adjust bbox y-coordinates back to full image space
                    adjusted_bbox = [[pt[0], pt[1] + y] for pt in bbox]
                    xs = [pt[0] for pt in adjusted_bbox]
                    ys = [pt[1] for pt in adjusted_bbox]
                    cx, cy = sum(xs) / 4, sum(ys) / 4
                    results_all.append({"text": text.strip(), "confidence": round(conf, 2), "cx": cx, "cy": cy})
        finally:
            tmp.unlink(missing_ok=True)
        y += chunk_h - overlap
    return results_all


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
    client: AIClient,
    settings: "_Settings",
    db: "db_module",
    source_lang: str = "vi",
) -> int:
    """Extract all panels for a chapter using EasyOCR + GLM-5. Returns total dialogue count."""
    # Choose OCR languages — Korean can't be mixed with Vietnamese in EasyOCR
    ocr_langs: tuple[str, ...] = ("en", "ko") if source_lang == "ko" else ("en", "vi")

    images_dir = settings.data_dir / "images" / series_slug / f"{chapter_num:03d}"
    image_paths = sorted(
        [p for p in images_dir.glob("*.*") if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} and p.stat().st_size > 10_000],
        key=lambda p: p.name,
    )

    if not image_paths:
        return 0

    db.delete_panels_for_chapter(chapter_id)

    # Step 1: OCR images sequentially — EasyOCR already uses all CPU cores internally
    ocr_sem = asyncio.Semaphore(1)

    async def _ocr_one(path: Path) -> list[dict]:
        async with ocr_sem:
            return await asyncio.to_thread(_ocr_image, path, ocr_langs)

    all_ocr: list[list[dict]] = await asyncio.gather(*[_ocr_one(p) for p in image_paths])

    # Step 2: Speaker attribution in sliding windows via GLM-5
    semaphore = asyncio.Semaphore(settings.max_concurrent)
    known_characters: list[str] = []
    all_extractions: list[PanelExtraction] = []

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

    # Step 4: Save human-readable text file for inspection
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
