# Manhwa — Webtoon Character-Voice-Aware Translator

Translate webtoons (EN/KO to Vietnamese) while preserving each character's unique speaking voice.

## How it works

4-stage pipeline:

1. **Scrape** — Downloads panel images from a webtoon chapter URL using a headless browser
2. **Extract** — Runs EasyOCR on each image (sliced into chunks for tall strips) then uses GLM-5 to attribute each line to a speaker
3. **Profile** — Analyzes all dialogues per character and builds a voice profile (speech style, tone, Vietnamese pronoun rules)
4. **Translate** — Translates new chapters using GLM-5 with character profiles injected into the system prompt

## Setup

```bash
git clone git@github.com:tungnguyentu/manhwa.git
cd manhwa
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
# Edit .env and set ZAI_API_KEY=your_key_here
```

## Usage

```bash
.venv/bin/toon ui
```

Open `http://localhost:7860` in your browser.

### Tabs

| Tab | Purpose |
|-----|---------|
| Learn | Paste Vietnamese webtoon chapter URLs — downloads images, runs OCR, builds character profiles and style guide |
| Translate | Paste EN/KO chapter URLs — translates using learned style and character voices |
| Read | View translated chapters with panel images side by side |
| Export | Download translations as TXT or JSON |
| History | View pipeline status per chapter; delete series or individual chapter data |

### Typical workflow

1. **Learn** — Paste 3–5 chapters of a Vietnamese webtoon to teach the system the translation style
2. **Translate** — Paste the English/Korean version of the same (or similar) series, select the learned style guide
3. **Read** — Open the Read tab to view the translated chapter with images

## Requirements

- Python 3.12+
- [ZAI API key](https://z.ai) — uses GLM-5 for speaker attribution, profiling, and translation
- Playwright — for scraping JS-rendered webtoon sites
- EasyOCR — local OCR, no API key needed

## Project structure

```
toon/
├── scraper/downloader.py     # Playwright-based image scraper
├── extractor/vision.py       # EasyOCR + GLM-5 speaker attribution
├── profiler/builder.py       # Character voice profile builder
├── translator/engine.py      # Batch translation with profile injection
├── learner/style_learner.py  # Vietnamese style guide extractor
├── db.py                     # SQLite database
├── ai_client.py              # ZAI API wrapper
├── config.py                 # Settings via .env
└── webui.py                  # Gradio web UI
```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ZAI_API_KEY` | required | ZAI API key |
| `ZAI_BASE_URL` | `https://api.z.ai/api/coding/paas/v4` | ZAI API base URL |
| `TOON_DATA_DIR` | `data` | Directory for images and database |
| `TOON_TEXT_MODEL` | `glm-5` | Model for text tasks |
| `TOON_SCRAPE_DELAY` | `1.0` | Delay between image downloads (seconds) |
