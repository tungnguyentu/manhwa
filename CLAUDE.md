# Toon — Claude Code Instructions

## Project
Webtoon character-voice-aware translation pipeline. Downloads webtoon chapter images, OCRs text, builds character voice profiles, and translates EN/KO → Vietnamese while preserving each character's speaking style.

## Setup
```bash
source .venv/bin/activate       # Python 3.12 venv
cp .env.example .env            # add ZAI_API_KEY
toon ui                         # Gradio UI on :7860
toon api                        # FastAPI on :7861
```

## Architecture
4-stage pipeline: **Scrape → Extract → Profile → Translate**

| Stage | Module | Notes |
|-------|--------|-------|
| Scrape | `toon/scraper/downloader.py` | Playwright (non-headless for Cloudflare sites) |
| Extract | `toon/extractor/vision.py` | Apple Vision OCR + optional GLM-5 attribution |
| Profile | `toon/profiler/builder.py` | GLM-5 character voice profiles |
| Translate | `toon/translator/engine.py` | GLM-5 with profile injection |

## AI Models (ZAI / z.ai)
- **OCR**: Apple Vision framework (local, free) — `pyobjc-framework-Vision`
- **Attribution / Profile / Translation**: `glm-5` (text)
- `glm-5v-turbo` and `glm-ocr` are **blocked** on the current coding plan — do not use

## Key Conventions
- Images: `data/images/{series_slug}/{chapter_num:03d}/`
- DB: `data/toon.db` (SQLite)
- Local images → ZAI via base64 data URI (`ai_client._path_to_data_uri`)
- Config via env prefix `TOON_`, API key `ZAI_API_KEY`
- `skip_attribution=True` in Learn flow (no GLM-5 during OCR — saves ~60 min per 6-chapter learn)
- OCR concurrency: 8 parallel Apple Vision threads

## Chrome Extension (`extension/`)
MV3 extension, background service worker + content script.
- Supported sites: `omegascans.org`, `asuratoon.com`, `manhwaclan.com`, `hentaivnx.com`
- API base default: `http://127.0.0.1:7861`
- Sidebar: 380px sticky panel, Alt+T toggle, character colour palette
- After editing: reload at `chrome://extensions`

## hentaivnx.com Notes
- Cloudflare blocks headless Playwright — scraper visits homepage first to get session cookies, then navigates to chapter
- Images served from CDN `sv4.2tcdn.cfd`, lazy-loaded via `data-src`
- Pages are ~950×19000px tall strips (~11 OCR chunks each)

## Session Logs
- [`SESSION_2026-04-05.md`](SESSION_2026-04-05.md) — hentaivnx support, Cloudflare scraper fix, `/api/learn` endpoint, OCR speed fix (55 min → 2 min)
