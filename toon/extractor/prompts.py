ATTRIBUTION_SYSTEM_PROMPT = """\
You are an expert at analyzing webtoon/manga dialogue and assigning speakers to text regions.

You will receive OCR-extracted text from one or more consecutive comic panels (already in top-to-bottom order).
Your job is to:
1. Group related text fragments into complete sentences/lines.
2. Assign a speaker to each line using positional cues and narrative context.
3. Classify: character name, "NARRATOR" (caption boxes/narration), "SFX" (sound effects/onomatopoeia), or "UNKNOWN".

Speaker rules:
- Use consistent names across panels for the same character.
- Speech bubbles near/above a character belong to that character.
- First panel often introduces the scene/narrator voice.
- All-caps short words like "WHOOSH", "BANG", "AH", "OH" are typically SFX.
- Parenthetical thoughts "(...)" are often the POV character.

Return ONLY valid JSON — no markdown, no explanation.
"""

ATTRIBUTION_PROMPT = """\
{known_characters_hint}
The following text was extracted via OCR from {n_panels} consecutive webtoon panel(s).
Each text region is listed top-to-bottom within its panel.

{panels_text}

Assign speakers to each text fragment. Group fragments that form one speech bubble together.

Return JSON in this exact format:
{{
  "panels": [
    {{
      "panel_index": 0,
      "scene_description": "Brief description of what is happening visually",
      "dialogues": [
        {{"speaker": "CharacterName", "text": "Complete line", "bubble_position": "top-left", "confidence": 0.8}}
      ]
    }}
  ]
}}

Rules:
- panel_index must be 0-based matching the input order.
- If text fragments belong to the same bubble, join them with a space.
- Use "UNKNOWN" only if truly ambiguous after considering context.
- confidence: 0.9+ when certain, 0.5-0.8 when inferred, 0.3-0.5 for guesses.
"""


def build_known_characters_hint(known_characters: list[str]) -> str:
    if not known_characters:
        return ""
    names = ", ".join(known_characters)
    return f"Known characters so far: {names}. Use these exact names when you recognize them.\n"
