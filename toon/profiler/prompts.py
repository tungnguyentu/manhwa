PROFILE_SYSTEM_PROMPT = """\
You are an expert translator and linguist specializing in Vietnamese localization of comics and webtoons. \
You analyze character dialogue to build voice profiles that will be used for consistent, \
natural-sounding translation into Vietnamese.
"""

PROFILE_BUILD_PROMPT = """\
Analyze these dialogue lines spoken by the character "{name}" in a webtoon.

Dialogue lines:
{dialogue_lines}

Create a character voice profile. Return ONLY valid JSON in this format:
{{
  "name": "{name}",
  "speech_style": "How they talk (formal/casual, sentence structure, politeness level, habits)",
  "vocabulary_notes": "Signature words, phrases, verbal tics, exclamations, slang they use",
  "personality": "Core personality traits visible through dialogue (2-3 sentences)",
  "tone": "Dominant emotional tone (e.g.: sarcastic, gentle, energetic, cold, aggressive, playful)",
  "example_lines": ["Up to 8 most representative lines from the input"],
  "vietnamese_voice_guide": "CRITICAL: Detailed guidance for translating this character into Vietnamese. Include:\\n- Which pronouns to use when speaking to other characters (anh/chi/em/con/bạn/mày/tao/tôi/ta/mình etc.)\\n- Sentence-ending particles typical for this character (nhé/nha/đi/đó/à/ư/nhỉ/chứ/thôi etc.)\\n- Formality level and how it affects word choice\\n- Any specific Vietnamese expressions that match their personality\\n- Regional dialect considerations if relevant"
}}
"""

PROFILE_UPDATE_PROMPT = """\
You are updating an existing character voice profile for "{name}" with new dialogue data.

EXISTING PROFILE:
{existing_profile_json}

NEW DIALOGUE LINES (from later chapters):
{new_dialogue_lines}

Update the profile to incorporate new observations. If the new lines confirm existing observations, \
keep them. If they reveal new patterns, update accordingly. \
Return the complete updated profile as ONLY valid JSON in the same format as the existing profile.
"""
