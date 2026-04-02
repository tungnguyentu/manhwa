from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import TYPE_CHECKING

from toon.ai_client import AIClient, parse_json_response
from toon.learner.prompts import STYLE_ANALYSIS_SYSTEM_PROMPT, STYLE_ANALYSIS_PROMPT

if TYPE_CHECKING:
    import toon.db as db_module

MAX_SAMPLES_PER_SPEAKER = 5
MAX_PAIRS = 20
MAX_SPEAKERS = 8


async def learn_style_from_series(
    source_series_id: int,
    target_series_id: int,
    source_series_slug: str,
    client: AIClient,
    db: "db_module",
) -> dict:
    """
    Analyze dialogues from a Vietnamese webtoon series and extract a style guide.
    Saves the guide to series_style_guides for target_series_id.
    """
    all_dialogues = db.get_all_dialogues_for_series(source_series_id)
    if not all_dialogues:
        raise ValueError("No extracted dialogues found in source series. Run extract first.")

    # Group Vietnamese text by speaker (skip noise labels)
    by_speaker: dict[str, list[str]] = defaultdict(list)
    for row in all_dialogues:
        if row["text"] and row["speaker"] not in ("SFX", "UNKNOWN", "WATERMARK"):
            by_speaker[row["speaker"]].append(row["text"])

    # Keep only the most talkative speakers to avoid bloating the prompt
    top_speakers = sorted(by_speaker.items(), key=lambda x: len(x[1]), reverse=True)[:MAX_SPEAKERS]

    guide: dict = {}
    for max_samples in [MAX_SAMPLES_PER_SPEAKER, 3]:
        samples_lines: list[str] = []
        for speaker, lines in top_speakers:
            samples_lines.append(f"\n[{speaker}]:")
            for line in lines[:max_samples]:
                samples_lines.append(f"  - {line[:100]}")

        pairs_lines: list[str] = []
        for row in all_dialogues[:MAX_PAIRS]:
            if row.get("text") and row["speaker"] not in ("SFX", "UNKNOWN", "WATERMARK"):
                pairs_lines.append(f"  [{row['speaker']}]: {row['text'][:100]}")

        prompt = STYLE_ANALYSIS_PROMPT.format(
            dialogue_samples="\n".join(samples_lines) or "(no dialogue samples)",
            translation_pairs="\n".join(pairs_lines) or "(no pairs available)",
        )

        raw = await asyncio.to_thread(
            client.chat,
            [{"role": "user", "content": prompt}],
            STYLE_ANALYSIS_SYSTEM_PROMPT,
            max_tokens=4096,
        )
        if not raw:
            continue
        try:
            guide = parse_json_response(raw)
            break
        except Exception:
            continue

    if not guide:
        guide = {"pronoun_rules": "", "sentence_particles": "", "formality_scale": "",
                 "phrasing_patterns": "", "narrator_style": "", "taboo_choices": "",
                 "example_pairs": [], "raw_guide": ""}

    db.upsert_style_guide(
        series_id=target_series_id,
        pronoun_rules=guide.get("pronoun_rules", ""),
        sentence_particles=guide.get("sentence_particles", ""),
        formality_scale=guide.get("formality_scale", ""),
        phrasing_patterns=guide.get("phrasing_patterns", ""),
        narrator_style=guide.get("narrator_style", ""),
        taboo_choices=guide.get("taboo_choices", ""),
        example_pairs=guide.get("example_pairs", []),
        raw_guide=guide.get("raw_guide", ""),
        source_series_slug=source_series_slug,
    )

    return guide


def format_style_guide_for_prompt(guide: dict) -> str:
    """Format a style guide dict into a string suitable for injection into translation prompts."""
    if not guide:
        return ""
    sections = [
        "## SERIES TRANSLATION STYLE GUIDE",
        f"(Learned from: {guide.get('source_series_slug', 'example series')})\n",
    ]
    if guide.get("pronoun_rules"):
        sections.append(f"**Pronoun Rules:**\n{guide['pronoun_rules'][:300]}\n")
    if guide.get("sentence_particles"):
        sections.append(f"**Particles:**\n{guide['sentence_particles'][:200]}\n")
    if guide.get("phrasing_patterns"):
        sections.append(f"**Phrasing:**\n{guide['phrasing_patterns'][:200]}\n")
    if guide.get("example_pairs"):
        sections.append("**Examples:**")
        for pair in guide["example_pairs"][:3]:
            orig = pair.get("original", "")
            vi = pair.get("vietnamese", "")
            if vi:
                sections.append(f"  • {orig} → {vi}")
    return "\n".join(sections)
