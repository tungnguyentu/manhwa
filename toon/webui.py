from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Generator

import gradio as gr

from toon.config import get_settings
from toon import db as db_module


# ── helpers ──────────────────────────────────────────────────────────────────

def _init():
    settings = get_settings()
    db_module.set_db_path(settings.data_dir / "toon.db")
    db_module.init_db()
    return settings


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, coro).result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


def _parse_urls(raw: str) -> list[str]:
    """Parse newline/comma-separated URLs, skip blanks."""
    urls = []
    for line in re.split(r"[\n,]+", raw):
        u = line.strip()
        if u:
            urls.append(u)
    return urls


def _chapter_from_url(url: str, fallback: int) -> int:
    """Extract chapter number from URL (e.g. /chapter-3/ → 3)."""
    m = re.search(r"chapter[-_]?(\d+)", url, re.IGNORECASE)
    return int(m.group(1)) if m else fallback


def _learned_series() -> list[str]:
    try:
        _init()
        with db_module.get_conn() as conn:
            rows = conn.execute(
                "SELECT s.slug FROM series s "
                "JOIN series_style_guides g ON g.series_id = s.id "
                "ORDER BY s.slug"
            ).fetchall()
        return [r["slug"] for r in rows] or []
    except Exception:
        return []


# ── Learn pipeline ────────────────────────────────────────────────────────────

def do_learn(urls_raw: str, force_reextract: bool = False, progress=gr.Progress()) -> Generator[str, None, None]:
    urls = _parse_urls(urls_raw)
    if not urls:
        yield "Please enter at least one URL."
        return

    log: list[str] = []

    def out(msg: str):
        log.append(msg)
        return "\n".join(log)

    try:
        settings = _init()
        from toon.scraper.downloader import scrape_chapter, url_to_slug
        from toon.ai_client import AIClient
        from toon.extractor.vision import extract_chapter
        from toon.profiler.builder import build_profiles
        from toon.learner.style_learner import learn_style_from_series

        client = AIClient(settings)
        slug = url_to_slug(urls[0])
        series_id = db_module.upsert_series(slug, url_base=urls[0], source_language="vi")
        total = len(urls)

        yield out(f"Series: **{slug}** — {total} chapter(s) to learn")

        for i, url in enumerate(urls):
            ch_n = _chapter_from_url(url, i + 1)
            yield out(f"\n**── Chapter {ch_n} ({i+1}/{total}) ──**")

            ch_id = db_module.upsert_chapter(series_id, ch_n, url)
            ch_info = db_module.get_chapter(series_id, ch_n)

            if ch_info and ch_info.get("scraped_at"):
                yield out(f"  ⏭ Images already downloaded, skipping")
            else:
                yield out(f"  ⏳ Downloading images…")
                progress(i / total, desc=f"Ch {ch_n}: downloading…")
                images = _run(scrape_chapter(slug, ch_n, url, settings))
                db_module.mark_chapter_scraped(ch_id)
                yield out(f"  ✅ {len(images)} images downloaded")

            if ch_info and ch_info.get("extracted_at") and not force_reextract:
                yield out(f"  ⏭ Text already extracted, skipping")
            else:
                if force_reextract:
                    db_module.clear_extracted(ch_id)
                yield out(f"  ⏳ Running OCR + speaker attribution…")
                progress(i / total + 0.4 / total, desc=f"Ch {ch_n}: OCR…")
                count = _run(extract_chapter(ch_id, slug, ch_n, client, settings, db_module, source_lang="vi"))
                db_module.mark_chapter_extracted(ch_id)
                yield out(f"  ✅ {count} dialogues extracted")

        progress(0.85, desc="Building profiles…")
        yield out("\n⏳ Building character voice profiles…")
        names = _run(build_profiles(series_id, client, db_module, rebuild=False))
        yield out(f"✅ Profiles: {', '.join(names) if names else 'up to date'}")

        progress(0.93, desc="Learning style…")
        yield out("⏳ Analyzing Vietnamese translation style…")
        guide = _run(learn_style_from_series(series_id, series_id, slug, client, db_module))
        n_pairs = len(guide.get("example_pairs", []))
        yield out(f"✅ Style guide saved ({n_pairs} example pairs)")

        progress(1.0, desc="Done")
        yield out(f"\n🎉 Done! Learned from **{total}** chapter(s) of **{slug}**.")

    except Exception as e:
        yield "\n".join(log) + f"\n\n❌ Error: {e}"


# ── Translate pipeline ────────────────────────────────────────────────────────

def do_translate(
    urls_raw: str,
    source_lang: str,
    style_source: str,
    progress=gr.Progress(),
) -> Generator[str, None, None]:
    urls = _parse_urls(urls_raw)
    if not urls:
        yield "Please enter at least one URL.", ""
        return

    log: list[str] = []

    def out(msg: str):
        log.append(msg)

    try:
        settings = _init()
        from toon.scraper.downloader import scrape_chapter, url_to_slug
        from toon.ai_client import AIClient
        from toon.extractor.vision import extract_chapter
        from toon.translator.engine import translate_chapter

        client = AIClient(settings)
        slug = url_to_slug(urls[0])
        series_id = db_module.upsert_series(slug, url_base=urls[0], source_language=source_lang)
        total = len(urls)

        # Resolve style guide source
        style_sid = None
        if style_source and style_source.strip():
            style_sid = db_module.get_series_id(style_source.strip()) or series_id

        out(f"Series: **{slug}** — {total} chapter(s)")
        if style_source:
            out(f"Style: **{style_source}**")
        yield "\n".join(log), ""

        all_previews: list[str] = []

        for i, url in enumerate(urls):
            ch_n = _chapter_from_url(url, i + 1)
            out(f"\n**── Chapter {ch_n} ({i+1}/{total}) ──**")
            yield "\n".join(log), ""

            ch_id = db_module.upsert_chapter(series_id, ch_n, url)
            ch_info = db_module.get_chapter(series_id, ch_n)

            if ch_info and ch_info.get("scraped_at"):
                out(f"  ⏭ Images already downloaded, skipping")
            else:
                out(f"  ⏳ Downloading images…")
                yield "\n".join(log), ""
                progress(i / total, desc=f"Ch {ch_n}: downloading…")
                images = _run(scrape_chapter(slug, ch_n, url, settings))
                db_module.mark_chapter_scraped(ch_id)
                out(f"  ✅ {len(images)} images downloaded")
            yield "\n".join(log), ""

            if ch_info and ch_info.get("extracted_at"):
                out(f"  ⏭ Text already extracted, skipping")
            else:
                out(f"  ⏳ Running OCR + speaker attribution…")
                yield "\n".join(log), ""
                progress(i / total + 0.3 / total, desc=f"Ch {ch_n}: OCR…")
                count = _run(extract_chapter(ch_id, slug, ch_n, client, settings, db_module, source_lang=source_lang))
                db_module.mark_chapter_extracted(ch_id)
                out(f"  ✅ {count} dialogues extracted")
            yield "\n".join(log), ""

            if ch_info and ch_info.get("translated_at"):
                out(f"  ⏭ Already translated, skipping")
                yield "\n".join(log), ""
                continue

            out(f"  ⏳ Translating dialogues…")
            yield "\n".join(log), ""
            progress(i / total + 0.6 / total, desc=f"Ch {ch_n}: translating…")
            profiles_raw = db_module.get_character_profiles(series_id)
            n_translated = _run(translate_chapter(
                ch_id, client, profiles_raw, db_module,
                source_lang=source_lang, target_lang="vi",
                series_id=style_sid,
            ))
            db_module.mark_chapter_translated(ch_id)
            out(f"  ✅ {n_translated} dialogues translated")
            yield "\n".join(log), ""

            # Collect preview for this chapter
            rows = db_module.get_translations_for_chapter(ch_id)
            all_previews.append(f"### Chapter {ch_n}")
            cur_panel = -1
            for r in rows:
                if not r.get("translated_text"):
                    continue
                if r["panel_index"] != cur_panel:
                    cur_panel = r["panel_index"]
                    all_previews.append(f"\n**── Panel {cur_panel + 1} ──**")
                all_previews.append(f"**[{r['speaker']}]** {r['translated_text']}")

        progress(1.0, desc="Done")
        out(f"\n🎉 Done! {total} chapter(s) translated.")
        yield "\n".join(log), "\n".join(all_previews)

    except Exception as e:
        yield "\n".join(log) + f"\n\n❌ Error: {e}", ""


# ── History / export ──────────────────────────────────────────────────────────

def list_all_series():
    try:
        _init()
        with db_module.get_conn() as conn:
            rows = conn.execute("SELECT slug FROM series ORDER BY slug").fetchall()
        return [r["slug"] for r in rows] or ["(none)"]
    except Exception:
        return ["(none)"]


def list_translated_chapters(slug: str):
    try:
        _init()
        sid = db_module.get_series_id(slug)
        if not sid:
            return []
        chs = db_module.list_chapters(sid)
        return [str(c["chapter_num"]) for c in chs if c["translated_at"]]
    except Exception:
        return []


def do_export(slug: str, chapter_num: str, fmt: str):
    if not slug or slug == "(none)":
        return "Select a series.", None
    try:
        settings = _init()
        sid = db_module.get_series_id(slug)
        ch = db_module.get_chapter(sid, int(chapter_num))
        if not ch:
            return "Chapter not found.", None

        rows = db_module.get_translations_for_chapter(ch["id"])
        export_dir = settings.data_dir / "exports" / slug
        export_dir.mkdir(parents=True, exist_ok=True)
        ch_n = int(chapter_num)

        if fmt == "JSON":
            out_path = export_dir / f"chapter_{ch_n:03d}.json"
            out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            out_path = export_dir / f"chapter_{ch_n:03d}.txt"
            lines = [f"[{r['speaker']}] {r['translated_text']}" for r in rows if r.get("translated_text")]
            out_path.write_text("\n".join(lines), encoding="utf-8")

        return f"Saved to {out_path}", str(out_path)
    except Exception as e:
        return f"Error: {e}", None


def do_history(slug: str):
    if not slug or slug == "(none)":
        return []
    try:
        _init()
        sid = db_module.get_series_id(slug)
        chs = db_module.list_chapters(sid)
        return [
            [ch["chapter_num"],
             "✓" if ch["scraped_at"] else "–",
             "✓" if ch["extracted_at"] else "–",
             "✓" if ch["translated_at"] else "–"]
            for ch in chs
        ]
    except Exception:
        return []


# ── Reader ────────────────────────────────────────────────────────────────────

def do_read(slug: str, chapter_num: str) -> str:
    if not slug or slug == "(none)" or not chapter_num:
        return ""
    try:
        _init()
        settings = get_settings()
        sid = db_module.get_series_id(slug)
        ch = db_module.get_chapter(sid, int(chapter_num))
        if not ch:
            return "Chapter not found."

        rows = db_module.get_translations_for_chapter(ch["id"])
        # Group by panel
        panels: dict[int, list[dict]] = {}
        for r in rows:
            panels.setdefault(r["panel_index"], []).append(r)

        images_dir = settings.data_dir / "images" / slug / f"{int(chapter_num):03d}"

        html_parts = [f"<h2>{slug} — Chapter {chapter_num}</h2>"]
        for panel_idx in sorted(panels.keys()):
            dialogues = panels[panel_idx]
            # Find matching image
            img_path = None
            for ext in (".jpg", ".jpeg", ".png", ".webp"):
                candidate = images_dir / f"{panel_idx + 1:03d}{ext}"
                if candidate.exists():
                    img_path = candidate
                    break

            html_parts.append('<div style="margin-bottom:24px;border-bottom:1px solid #333;padding-bottom:16px">')

            if img_path:
                import base64
                data = base64.b64encode(img_path.read_bytes()).decode()
                mime = "image/jpeg" if img_path.suffix in (".jpg", ".jpeg") else f"image/{img_path.suffix[1:]}"
                html_parts.append(f'<img src="data:{mime};base64,{data}" style="max-width:100%;display:block;margin:0 auto"/>')

            if dialogues:
                html_parts.append('<div style="padding:8px 0">')
                for d in dialogues:
                    if d.get("translated_text"):
                        speaker = d["speaker"]
                        text = d["translated_text"]
                        html_parts.append(
                            f'<p style="margin:4px 0"><strong style="color:#4a9eff">[{speaker}]</strong> {text}</p>'
                        )
                html_parts.append('</div>')

            html_parts.append('</div>')

        return "\n".join(html_parts)
    except Exception as e:
        return f"Error: {e}"


def list_chapters_for_read(slug: str) -> list[str]:
    try:
        _init()
        sid = db_module.get_series_id(slug)
        if not sid:
            return []
        chs = db_module.list_chapters(sid)
        return [str(c["chapter_num"]) for c in chs if c["translated_at"]]
    except Exception:
        return []


def do_delete_translations(slug: str, chapter_num: str) -> str:
    if not slug or slug == "(none)":
        return "Select a series."
    try:
        _init()
        sid = db_module.get_series_id(slug)
        if not sid:
            return "Series not found."

        delete_all = not chapter_num or chapter_num == "(all chapters)"

        if delete_all:
            db_module.delete_series(sid)
            return f"✅ Deleted series '{slug}' and all data."

        chs = db_module.list_chapters(sid)
        targets = [ch for ch in chs if str(ch["chapter_num"]) == chapter_num]
        if not targets:
            return "Chapter not found."
        for ch in targets:
            db_module.clear_translated(ch["id"])
            db_module.delete_panels_for_chapter(ch["id"])
            db_module.clear_extracted(ch["id"])
        nums = ", ".join(str(c["chapter_num"]) for c in targets)
        return f"✅ Deleted translations + panels for chapter(s): {nums}"
    except Exception as e:
        return f"Error: {e}"


# ── build UI ──────────────────────────────────────────────────────────────────

def build_ui() -> gr.Blocks:
    with gr.Blocks(title="Toon Translator") as demo:
        gr.Markdown(
            "# 🎌 Toon — Webtoon Translator\n"
            "Translate webtoons to Vietnamese while preserving each character's unique voice."
        )

        with gr.Tabs():

            # ── Tab 1: Learn ──────────────────────────────────────────────────
            with gr.Tab("📚 Learn (Vietnamese)"):
                gr.Markdown(
                    "Paste **one or more URLs** of Vietnamese webtoon chapters (one per line). "
                    "The system downloads images, reads all text via OCR, and learns the "
                    "translation style — pronouns, particles, phrasing, tone."
                )
                l_urls = gr.Textbox(
                    label="Chapter URL(s) — one per line",
                    placeholder=(
                        "https://hentaivnx.com/…/chapter-1/…\n"
                        "https://hentaivnx.com/…/chapter-2/…\n"
                        "https://hentaivnx.com/…/chapter-3/…"
                    ),
                    lines=5,
                )
                l_reextract = gr.Checkbox(label="Force re-extract (redo OCR even if already done)", value=False)
                l_btn = gr.Button("📥 Learn from these chapters", variant="primary", size="lg")
                l_log = gr.Textbox(label="Progress", lines=14, interactive=False)
                l_btn.click(do_learn, [l_urls, l_reextract], l_log)

            # ── Tab 2: Translate ──────────────────────────────────────────────
            with gr.Tab("🌐 Translate (EN/KO → Vietnamese)"):
                gr.Markdown(
                    "Paste **one or more URLs** of English or Korean webtoon chapters (one per line). "
                    "Choose a Vietnamese series you already learned as the style guide."
                )
                t_urls = gr.Textbox(
                    label="Chapter URL(s) — one per line",
                    placeholder=(
                        "https://example.com/manga/title/chapter-1/\n"
                        "https://example.com/manga/title/chapter-2/"
                    ),
                    lines=5,
                )
                with gr.Row():
                    t_lang = gr.Radio(choices=["en", "ko"], value="en", label="Source language", scale=1)
                    t_style = gr.Dropdown(
                        choices=_learned_series(),
                        label="Use style from (optional)",
                        allow_custom_value=False,
                        scale=3,
                    )
                t_refresh = gr.Button("🔄 Refresh style list", size="sm")
                t_btn = gr.Button("🚀 Translate", variant="primary", size="lg")
                t_log = gr.Textbox(label="Progress", lines=12, interactive=False)
                with gr.Accordion("Translation preview", open=False):
                    t_preview = gr.Markdown()

                t_refresh.click(lambda: gr.update(choices=_learned_series()), [], t_style)
                t_btn.click(do_translate, [t_urls, t_lang, t_style], [t_log, t_preview])

            # ── Tab 3: Export ─────────────────────────────────────────────────
            with gr.Tab("💾 Export"):
                gr.Markdown("Download a translated chapter as TXT or JSON.")
                with gr.Row():
                    x_series = gr.Dropdown(choices=list_all_series(), label="Series", scale=3)
                    x_ch = gr.Dropdown(choices=[], label="Chapter", scale=1)
                x_fmt = gr.Radio(["TXT", "JSON"], value="TXT", label="Format")
                with gr.Row():
                    x_refresh = gr.Button("🔄 Refresh", size="sm")
                    x_btn = gr.Button("⬇️ Export", variant="primary")
                x_out = gr.Textbox(label="Result", lines=2, interactive=False)
                x_file = gr.File(label="Download")

                x_refresh.click(
                    lambda: (gr.update(choices=list_all_series()), gr.update(choices=[])),
                    [], [x_series, x_ch],
                )
                x_series.change(
                    lambda s: gr.update(choices=list_translated_chapters(s)),
                    x_series, x_ch,
                )
                x_btn.click(do_export, [x_series, x_ch, x_fmt], [x_out, x_file])

            # ── Tab 4: Read ───────────────────────────────────────────────────
            with gr.Tab("📖 Read"):
                with gr.Row():
                    r_series = gr.Dropdown(choices=list_all_series(), label="Series", scale=3)
                    r_ch = gr.Dropdown(choices=[], label="Chapter", scale=1)
                with gr.Row():
                    r_refresh = gr.Button("🔄 Refresh", size="sm")
                    r_btn = gr.Button("📖 Load", variant="primary")
                r_view = gr.HTML()

                r_refresh.click(
                    lambda: (gr.update(choices=list_all_series()), gr.update(choices=[])),
                    [], [r_series, r_ch],
                )
                r_series.change(
                    lambda s: gr.update(choices=list_chapters_for_read(s)),
                    r_series, r_ch,
                )
                r_btn.click(do_read, [r_series, r_ch], r_view)

            # ── Tab 5: History ────────────────────────────────────────────────
            with gr.Tab("📋 History"):
                h_series = gr.Dropdown(choices=list_all_series(), label="Series")
                h_refresh = gr.Button("🔄 Refresh")
                h_table = gr.Dataframe(
                    headers=["Chapter", "Scraped", "Extracted", "Translated"],
                    datatype=["number", "str", "str", "str"],
                    interactive=False,
                )
                h_refresh.click(lambda: gr.update(choices=list_all_series()), [], h_series)
                h_series.change(do_history, h_series, h_table)

                gr.Markdown("---\n### Delete translations")
                with gr.Row():
                    del_series = gr.Dropdown(choices=list_all_series(), label="Series", scale=3)
                    del_ch = gr.Dropdown(choices=[], label="Chapter (leave blank = all)", scale=1, allow_custom_value=False)
                with gr.Row():
                    del_refresh = gr.Button("🔄 Refresh", size="sm")
                    del_btn = gr.Button("🗑️ Delete translations", variant="stop")
                del_out = gr.Textbox(label="Result", lines=2, interactive=False)

                del_refresh.click(
                    lambda: (gr.update(choices=list_all_series()), gr.update(choices=[])),
                    [], [del_series, del_ch],
                )
                del_series.change(
                    lambda s: gr.update(choices=["(all chapters)"] + list_translated_chapters(s)),
                    del_series, del_ch,
                )
                del_btn.click(do_delete_translations, [del_series, del_ch], del_out)

    return demo


def main():
    demo = build_ui()
    demo.launch(server_name="0.0.0.0", server_port=7860, share=False, theme=gr.themes.Soft())


if __name__ == "__main__":
    main()
