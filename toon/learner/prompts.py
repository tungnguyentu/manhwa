STYLE_ANALYSIS_SYSTEM_PROMPT = """\
You are a Vietnamese linguistics expert specializing in webtoon/manga localization.
You analyze translated Vietnamese webtoon text to extract a reusable translation style guide.
"""

STYLE_ANALYSIS_PROMPT = """\
Analyze the following Vietnamese webtoon dialogue and extract a comprehensive translation style guide.

This text is FROM an already-translated Vietnamese webtoon. Your job is to understand HOW \
it was translated — the conventions, choices, and patterns used — so we can replicate \
the same style for new translations.

VIETNAMESE TEXT SAMPLES (organized by speaker):
{dialogue_samples}

SAMPLE TRANSLATION PAIRS (original → Vietnamese, if available):
{translation_pairs}

Extract a detailed style guide. Return ONLY valid JSON in this format:
{{
  "pronoun_rules": "Detailed rules for Vietnamese pronouns used in this series. Which pronouns \
appear (anh/em/tôi/tao/mày/bạn/mình/ta/ông/bà/con etc.), in what contexts, and between which \
character types. Include examples from the text.",

  "sentence_particles": "Which sentence-ending particles are used (à/ư/nhé/nha/đi/thôi/chứ/nhỉ/\
đó/đấy/ấy/vậy/thế/kìa etc.), by what type of character, and in what emotional context. \
Give specific examples.",

  "formality_scale": "Description of formality levels used. How do formal vs casual speakers \
differ in vocabulary, grammar structure, and word choice? Any regional dialect markers?",

  "phrasing_patterns": "Common Vietnamese sentence patterns, structures, or idioms used. \
Are sentences short/punchy or long/flowing? Any characteristic interjections (ôi/chà/này/ê/ừ etc.)? \
How is emphasis shown? Any patterns in how emotions are expressed?",

  "narrator_style": "How is narration written (if any)? Formal/literary prose, casual, \
third-person vs second-person? Tense conventions?",

  "taboo_choices": "What translation choices to AVOID based on this style. What would feel out \
of place — e.g. 'avoid overly formal tôi in casual scenes', 'avoid direct literal translation of \
English idioms', etc.",

  "raw_guide": "A 200-word human-readable summary of the overall translation style, suitable \
for briefing a translator.",

  "example_pairs": [
    {{"original": "...", "vietnamese": "...", "note": "why this is a good example of the style"}}
  ]
}}

Include at least 5 example_pairs that best illustrate the style.
"""
