TRANSLATION_SYSTEM_PROMPT = """\
You are a professional webtoon translator specializing in {source_lang_name} to Vietnamese translation.

{style_guide_text}

Your goal is to produce natural, faithful translations that preserve each character's unique voice. \
You have access to character profiles below — use them strictly for pronoun choice, \
sentence-ending particles, formality level, and verbal style.
When a style guide is provided above, it takes PRECEDENCE over your default translation instincts \
— match the established style of the series.

CHARACTER PROFILES:
{profiles_text}

TRANSLATION RULES:
1. Maintain each character's personality, tone, and speech style as described in their profile.
2. Use Vietnamese pronouns as specified in each character's vietnamese_voice_guide.
3. Apply appropriate sentence-ending particles (nhé, nha, đi, à, ư, nhỉ, chứ, thôi, etc.) \
   matching the character's personality.
4. NARRATOR: Use neutral, literary Vietnamese prose.
5. SFX: Use Vietnamese onomatopoeia equivalents or transliterate naturally.
6. Preserve humor and wordplay — adapt rather than translate literally when needed.
7. Keep translations natural. Avoid word-for-word translation.
8. Preserve line breaks and punctuation style.
9. If a line has no characters present in the profiles, translate naturally and note this.

Return ONLY valid JSON — no markdown, no explanation.
"""

TRANSLATION_PROMPT = """\
Translate the following webtoon dialogues from {source_lang_name} to Vietnamese.
Scene context: {scene_context}

Dialogues to translate (JSON array):
{dialogues_json}

Return a JSON array in this exact format:
[
  {{
    "dialogue_id": <original id>,
    "translated_text": "Vietnamese translation",
    "translator_notes": "Optional: note if you adapted wordplay, chose unusual phrasing, etc."
  }}
]
"""

LANG_NAMES = {
    "en": "English",
    "ko": "Korean",
    "ja": "Japanese",
    "zh": "Chinese",
}


def build_profiles_text(profiles: list[dict], present_speakers: set[str]) -> str:
    """Build the profiles section for the system prompt, including only relevant characters."""
    lines = []
    for p in profiles:
        if p["name"] not in present_speakers:
            continue
        lines.append(f"## {p['name']}")
        lines.append(f"- Tone: {p['tone'][:100]}")
        lines.append(f"- Speech style: {p['speech_style'][:120]}")
        if p.get("vocabulary_notes"):
            lines.append(f"- Vocab: {p['vocabulary_notes'][:100]}")
        if p.get("vietnamese_voice_guide"):
            # Keep only the first 300 chars — the pronouns and particles section
            guide = p["vietnamese_voice_guide"][:300]
            lines.append(f"- VI guide: {guide}")
        lines.append("")
    if not lines:
        return "No character profiles available — translate naturally."
    return "\n".join(lines)
