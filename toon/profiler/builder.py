from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import TYPE_CHECKING

from toon.ai_client import AIClient, parse_json_response
from toon.profiler.prompts import (
    PROFILE_SYSTEM_PROMPT,
    PROFILE_BUILD_PROMPT,
    PROFILE_UPDATE_PROMPT,
)

if TYPE_CHECKING:
    import toon.db as db_module

# Minimum number of lines required to build a profile for a character
MIN_LINES = 3
# Max example lines to include in profile
MAX_EXAMPLE_LINES = 8


PROFILE_CONCURRENCY = 2


async def build_profiles(
    series_id: int,
    client: AIClient,
    db: "db_module",
    rebuild: bool = False,
) -> list[str]:
    """Build or update character voice profiles. Returns list of character names processed."""
    all_dialogues = db.get_all_dialogues_for_series(series_id)
    existing_profiles = {p["name"]: p for p in db.get_character_profiles(series_id)}

    # Group dialogues by speaker
    by_speaker: dict[str, list[str]] = defaultdict(list)
    for row in all_dialogues:
        speaker = row["speaker"]
        if speaker in ("NARRATOR", "SFX", "UNKNOWN"):
            continue
        by_speaker[speaker].append(row["text"])

    # Filter to characters that need processing
    to_process: list[tuple[str, list[str], dict | None]] = []
    for name, lines in by_speaker.items():
        if len(lines) < MIN_LINES:
            continue
        existing = existing_profiles.get(name)
        if existing and not rebuild:
            existing_examples = set(existing.get("example_lines", []))
            new_lines = [l for l in lines if l not in existing_examples]
            if not new_lines:
                continue
            to_process.append((name, new_lines, existing))
        else:
            to_process.append((name, lines, None))

    sem = asyncio.Semaphore(PROFILE_CONCURRENCY)

    async def _process_one(name: str, lines: list[str], existing: dict | None) -> dict:
        async with sem:
            if existing is not None:
                return await _update_profile(name, lines, existing, client)
            return await _build_profile(name, lines, client)

    profiles = await asyncio.gather(*[_process_one(n, l, e) for n, l, e in to_process])

    processed: list[str] = []
    for profile in profiles:
        db.upsert_character_profile(
            series_id=series_id,
            name=profile["name"],
            aliases=profile.get("aliases", []),
            speech_style=profile.get("speech_style", ""),
            vocabulary_notes=profile.get("vocabulary_notes", ""),
            personality=profile.get("personality", ""),
            tone=profile.get("tone", ""),
            example_lines=profile.get("example_lines", [])[:MAX_EXAMPLE_LINES],
            vietnamese_voice_guide=profile.get("vietnamese_voice_guide", ""),
        )
        processed.append(profile["name"])

    return processed


async def _build_profile(name: str, lines: list[str], client: AIClient) -> dict:
    # Try with decreasing number of lines if response gets truncated
    for max_lines in [20, 12, 6]:
        sample = lines[:max_lines]
        # Also cap each line length to avoid bloated prompts
        dialogue_lines = "\n".join(f"- {line[:120]}" for line in sample)
        prompt = PROFILE_BUILD_PROMPT.format(name=name, dialogue_lines=dialogue_lines)
        raw = await asyncio.to_thread(
            client.chat,
            [{"role": "user", "content": prompt}],
            PROFILE_SYSTEM_PROMPT,
            max_tokens=4096,
        )
        if not raw:
            continue
        try:
            return parse_json_response(raw)
        except Exception:
            continue  # truncated — retry with fewer lines
    # Absolute fallback: minimal profile
    return {
        "name": name,
        "speech_style": "Unknown",
        "vocabulary_notes": "",
        "personality": "Unknown",
        "tone": "neutral",
        "example_lines": lines[:3],
        "vietnamese_voice_guide": "",
    }


async def _update_profile(name: str, new_lines: list[str], existing: dict, client: AIClient) -> dict:
    dialogue_lines = "\n".join(f"- {line[:120]}" for line in new_lines[:15])
    existing_json = json.dumps({
        k: existing[k]
        for k in ("speech_style", "vocabulary_notes", "personality", "tone",
                  "example_lines", "vietnamese_voice_guide")
        if k in existing
    }, ensure_ascii=False, indent=2)
    prompt = PROFILE_UPDATE_PROMPT.format(
        name=name,
        existing_profile_json=existing_json,
        new_dialogue_lines=dialogue_lines,
    )
    raw = await asyncio.to_thread(
        client.chat,
        [{"role": "user", "content": prompt}],
        PROFILE_SYSTEM_PROMPT,
        max_tokens=4096,
    )
    if not raw:
        return dict(existing, name=name)
    try:
        result = parse_json_response(raw)
        result.setdefault("name", name)
        return result
    except Exception:
        return dict(existing, name=name)
