from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from toon.ai_client import AIClient, parse_json_response
from toon.translator.prompts import (
    TRANSLATION_SYSTEM_PROMPT,
    TRANSLATION_PROMPT,
    LANG_NAMES,
    build_profiles_text,
)
from toon.learner.style_learner import format_style_guide_for_prompt

if TYPE_CHECKING:
    import toon.db as db_module

# Max dialogues per translation API call
BATCH_SIZE = 10
# Max concurrent translation API calls
TRANSLATE_CONCURRENCY = 2


async def translate_chapter(
    chapter_id: int,
    client: AIClient,
    profiles_raw: list[dict],
    db: "db_module",
    source_lang: str = "en",
    target_lang: str = "vi",
    series_id: int | None = None,
) -> int:
    """Translate all dialogues in a chapter. Returns number of translated dialogues."""
    dialogues = db.get_dialogues_for_chapter(chapter_id)
    if not dialogues:
        return 0

    source_lang_name = LANG_NAMES.get(source_lang, source_lang.upper())

    present_speakers = {d["speaker"] for d in dialogues if d["speaker"] not in ("SFX", "UNKNOWN")}
    profiles_text = build_profiles_text(profiles_raw, present_speakers)

    style_guide_text = ""
    if series_id is not None:
        guide = db.get_style_guide(series_id)
        if guide:
            style_guide_text = format_style_guide_for_prompt(guide)

    profile_names = sorted(p["name"] for p in profiles_raw)
    profile_version = ",".join(profile_names)

    system_prompt = TRANSLATION_SYSTEM_PROMPT.format(
        source_lang_name=source_lang_name,
        style_guide_text=style_guide_text,
        profiles_text=profiles_text,
    )

    # Group dialogues by panel for scene context
    panels: dict[int, list[dict]] = {}
    for d in dialogues:
        panels.setdefault(d["panel_index"], []).append(d)

    # Build all batches up front
    batches: list[tuple[list[dict], list[str]]] = []
    batch: list[dict] = []
    scene_contexts: list[str] = []

    for panel_idx in sorted(panels.keys()):
        panel_dialogues = panels[panel_idx]
        scene_desc = panel_dialogues[0].get("scene_description", "")
        for d in panel_dialogues:
            batch.append(d)
            scene_contexts.append(scene_desc)
            if len(batch) >= BATCH_SIZE:
                batches.append((batch, scene_contexts))
                batch = []
                scene_contexts = []

    if batch:
        batches.append((batch, scene_contexts))

    # Run all batches concurrently (limited by semaphore)
    sem = asyncio.Semaphore(TRANSLATE_CONCURRENCY)

    async def _run_batch(b, ctx):
        async with sem:
            return await _translate_batch(b, ctx, system_prompt, source_lang_name, profile_version, target_lang, client, db)

    results = await asyncio.gather(*[_run_batch(b, ctx) for b, ctx in batches])
    return sum(results)


async def _translate_batch(
    dialogues: list[dict],
    scene_contexts: list[str],
    system_prompt: str,
    source_lang_name: str,
    profile_version: str,
    target_lang: str,
    client: AIClient,
    db: "db_module",
) -> int:
    # Build a single representative scene context (truncated to avoid bloating prompt)
    context_parts = list(dict.fromkeys(c for c in scene_contexts if c))
    scene_context = (" | ".join(context_parts[:2]) if context_parts else "No scene description")[:300]

    dialogues_for_prompt = [
        {
            "dialogue_id": d["id"],
            "speaker": d["speaker"],
            "text": d["text"],
        }
        for d in dialogues
    ]

    prompt = TRANSLATION_PROMPT.format(
        source_lang_name=source_lang_name,
        scene_context=scene_context,
        dialogues_json=json.dumps(dialogues_for_prompt, ensure_ascii=False, indent=2),
    )

    # Retry up to 3 times with backoff on empty/invalid responses
    raw = ""
    for attempt in range(3):
        raw = await asyncio.to_thread(
            client.chat,
            [{"role": "user", "content": prompt}],
            system_prompt,
            max_tokens=4096,
        )
        if raw:
            break
        await asyncio.sleep(2 ** attempt)  # 1s, 2s, 4s

    if not raw:
        return 0

    try:
        results = parse_json_response(raw)
    except Exception:
        return 0
    if not isinstance(results, list):
        return 0

    # Map results back by dialogue_id
    result_map = {r["dialogue_id"]: r for r in results if "dialogue_id" in r}

    count = 0
    for d in dialogues:
        result = result_map.get(d["id"])
        if result and result.get("translated_text"):
            db.save_translation(
                dialogue_id=d["id"],
                translated_text=result["translated_text"],
                translator_notes=result.get("translator_notes", ""),
                profile_version=profile_version,
                target_lang=target_lang,
            )
            count += 1

    return count
