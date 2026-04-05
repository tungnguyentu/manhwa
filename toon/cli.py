from __future__ import annotations

import typer
from rich.console import Console

app = typer.Typer(name="toon", help="Webtoon character-voice-aware translation tool")
console = Console()


@app.command()
def scrape(
    url: str = typer.Argument(..., help="Chapter or series URL to scrape"),
    chapter: int | None = typer.Option(None, "--chapter", "-c", help="Specific chapter number"),
    all_chapters: bool = typer.Option(False, "--all", "-a", help="Scrape all discovered chapters"),
    series: str | None = typer.Option(None, "--series", "-s", help="Series slug (auto-detected if omitted)"),
    lang: str = typer.Option("vi", "--lang", help="Source language: vi, en, or ko"),
    image_urls: str | None = typer.Option(None, "--image-urls", help="Comma-separated image URLs (skip browser, download directly)"),
) -> None:
    """Download chapter images from a webtoon URL."""
    from toon.config import get_settings
    from toon.scraper.downloader import scrape_chapter, scrape_chapter_with_urls, discover_chapters, url_to_slug
    import asyncio
    from toon import db

    settings = get_settings()
    db.set_db_path(settings.data_dir / "toon.db")
    db.init_db()

    slug = series or url_to_slug(url)
    series_id = db.upsert_series(slug, url_base=url, source_language=lang)

    console.print(f"[bold]Series:[/bold] {slug} (id={series_id})")

    async def run() -> None:
        if image_urls:
            # Direct download mode — URLs already extracted by MCP browser
            urls = [u.strip() for u in image_urls.split(",") if u.strip()]
            ch_num = chapter or 1
            ch_id = db.upsert_chapter(series_id, ch_num, url)
            images = await scrape_chapter_with_urls(slug, ch_num, urls, url, settings)
            db.mark_chapter_scraped(ch_id)
            console.print(f"Chapter {ch_num}: [green]{len(images)} images downloaded[/green]")
        elif all_chapters:
            chapters = await discover_chapters(url, settings)
            console.print(f"Found [bold]{len(chapters)}[/bold] chapters")
            for ch in chapters:
                ch_id = db.upsert_chapter(series_id, ch["chapter_num"], ch["url"])
                images = await scrape_chapter(slug, ch["chapter_num"], ch["url"], settings)
                db.mark_chapter_scraped(ch_id)
                console.print(f"  Chapter {ch['chapter_num']}: {len(images)} images")
        else:
            ch_num = chapter or 1
            ch_id = db.upsert_chapter(series_id, ch_num, url)
            images = await scrape_chapter(slug, ch_num, url, settings)
            db.mark_chapter_scraped(ch_id)
            console.print(f"Chapter {ch_num}: [green]{len(images)} images downloaded[/green]")

    asyncio.run(run())


@app.command()
def extract(
    series: str = typer.Argument(..., help="Series slug"),
    chapter: int | None = typer.Option(None, "--chapter", "-c", help="Specific chapter number"),
    all_chapters: bool = typer.Option(False, "--all", "-a", help="Extract all scraped chapters"),
    rerun: bool = typer.Option(False, "--rerun", help="Re-extract even if already done"),
) -> None:
    """Extract text from panel images using GLM-5V-Turbo vision."""
    from toon.config import get_settings
    from toon.extractor.vision import extract_chapter
    from toon import db
    import asyncio

    settings = get_settings()
    db.set_db_path(settings.data_dir / "toon.db")
    db.init_db()

    series_id = db.get_series_id(series)
    if not series_id:
        console.print(f"[red]Series '{series}' not found. Run scrape first.[/red]")
        raise typer.Exit(1)

    chapters = db.list_chapters(series_id)
    if chapter is not None:
        chapters = [c for c in chapters if c["chapter_num"] == chapter]
    if not all_chapters and chapter is None:
        console.print("[red]Specify --chapter N or --all[/red]")
        raise typer.Exit(1)

    from toon.ai_client import AIClient
    client = AIClient(settings)

    async def run() -> None:
        for ch in chapters:
            if ch["scraped_at"] is None:
                console.print(f"  Skipping chapter {ch['chapter_num']} (not scraped yet)")
                continue
            if ch["extracted_at"] and not rerun:
                console.print(f"  Skipping chapter {ch['chapter_num']} (already extracted)")
                continue
            console.print(f"  Extracting chapter {ch['chapter_num']}...")
            count = await extract_chapter(ch["id"], series, ch["chapter_num"], client, settings, db)
            db.mark_chapter_extracted(ch["id"])
            console.print(f"    [green]{count} dialogues extracted[/green]")

    asyncio.run(run())


@app.command()
def profile(
    series: str = typer.Argument(..., help="Series slug"),
    rebuild: bool = typer.Option(False, "--rebuild", help="Rebuild profiles from scratch"),
) -> None:
    """Build or update character voice profiles from extracted dialogues."""
    from toon.config import get_settings
    from toon.profiler.builder import build_profiles
    from toon import db
    import asyncio

    settings = get_settings()
    db.set_db_path(settings.data_dir / "toon.db")
    db.init_db()

    series_id = db.get_series_id(series)
    if not series_id:
        console.print(f"[red]Series '{series}' not found.[/red]")
        raise typer.Exit(1)

    from toon.ai_client import AIClient
    client = AIClient(settings)

    async def run() -> None:
        profiles = await build_profiles(series_id, client, db, rebuild=rebuild)
        console.print(f"[green]{len(profiles)} character profiles built/updated[/green]")
        for p in profiles:
            console.print(f"  [bold]{p}[/bold]")

    asyncio.run(run())


@app.command()
def translate(
    series: str = typer.Argument(..., help="Series slug"),
    chapter: int | None = typer.Option(None, "--chapter", "-c", help="Specific chapter number"),
    all_chapters: bool = typer.Option(False, "--all", "-a", help="Translate all extracted chapters"),
    lang: str = typer.Option("vi", "--lang", help="Target language (vi)"),
    source_lang: str = typer.Option("en", "--source", help="Source language: en or ko"),
    rerun: bool = typer.Option(False, "--rerun", help="Re-translate even if already done"),
) -> None:
    """Translate chapters using character voice profiles."""
    from toon.config import get_settings
    from toon.translator.engine import translate_chapter
    from toon import db
    import asyncio

    settings = get_settings()
    db.set_db_path(settings.data_dir / "toon.db")
    db.init_db()

    series_id = db.get_series_id(series)
    if not series_id:
        console.print(f"[red]Series '{series}' not found.[/red]")
        raise typer.Exit(1)

    chapters = db.list_chapters(series_id)
    if chapter is not None:
        chapters = [c for c in chapters if c["chapter_num"] == chapter]
    if not all_chapters and chapter is None:
        console.print("[red]Specify --chapter N or --all[/red]")
        raise typer.Exit(1)

    from toon.ai_client import AIClient
    client = AIClient(settings)

    async def run() -> None:
        profiles_raw = db.get_character_profiles(series_id)
        for ch in chapters:
            if ch["extracted_at"] is None:
                console.print(f"  Skipping chapter {ch['chapter_num']} (not extracted yet)")
                continue
            if ch["translated_at"] and not rerun:
                console.print(f"  Skipping chapter {ch['chapter_num']} (already translated)")
                continue
            console.print(f"  Translating chapter {ch['chapter_num']}...")
            count = await translate_chapter(
                ch["id"], client, profiles_raw, db, source_lang=source_lang, target_lang=lang,
                series_id=series_id,
            )
            db.mark_chapter_translated(ch["id"])
            console.print(f"    [green]{count} dialogues translated[/green]")

    asyncio.run(run())


@app.command()
def learn(
    source_series: str = typer.Argument(..., help="Slug of the Vietnamese series to learn style from"),
    target_series: str | None = typer.Option(None, "--for", help="Apply style guide to this series (default: same as source)"),
) -> None:
    """Learn Vietnamese translation style from an example webtoon series."""
    from toon.config import get_settings
    from toon.ai_client import AIClient
    from toon.learner.style_learner import learn_style_from_series
    from toon import db
    import asyncio

    settings = get_settings()
    db.set_db_path(settings.data_dir / "toon.db")
    db.init_db()

    source_id = db.get_series_id(source_series)
    if not source_id:
        console.print(f"[red]Source series '{source_series}' not found. Run scrape + extract first.[/red]")
        raise typer.Exit(1)

    target_slug = target_series or source_series
    target_id = db.get_series_id(target_slug)
    if not target_id:
        target_id = db.upsert_series(target_slug)

    console.print(f"Analyzing style from [bold]{source_series}[/bold]...")
    client = AIClient(settings)

    async def run() -> None:
        guide = await learn_style_from_series(source_id, target_id, source_series, client, db)
        console.print("[green]Style guide saved.[/green]")
        console.print(f"  Pronoun rules: {guide.get('pronoun_rules', '')[:80]}...")
        console.print(f"  Particles: {guide.get('sentence_particles', '')[:80]}...")
        console.print(f"  Example pairs: {len(guide.get('example_pairs', []))}")

    asyncio.run(run())


@app.command()
def export(
    series: str = typer.Argument(..., help="Series slug"),
    chapter: int | None = typer.Option(None, "--chapter", "-c"),
    fmt: str = typer.Option("txt", "--format", "-f", help="Output format: txt or json"),
    target_lang: str = typer.Option("vi", "--lang"),
) -> None:
    """Export translations to files."""
    from toon.config import get_settings
    from toon import db
    import json as json_mod

    settings = get_settings()
    db.set_db_path(settings.data_dir / "toon.db")
    db.init_db()

    series_id = db.get_series_id(series)
    if not series_id:
        console.print(f"[red]Series '{series}' not found.[/red]")
        raise typer.Exit(1)

    chapters = db.list_chapters(series_id)
    if chapter is not None:
        chapters = [c for c in chapters if c["chapter_num"] == chapter]

    export_dir = settings.data_dir / "exports" / series
    export_dir.mkdir(parents=True, exist_ok=True)

    for ch in chapters:
        if not ch["translated_at"]:
            continue
        rows = db.get_translations_for_chapter(ch["id"], target_lang=target_lang)
        ch_num = ch["chapter_num"]
        if fmt == "json":
            out_path = export_dir / f"chapter_{ch_num:03d}.json"
            out_path.write_text(json_mod.dumps(rows, ensure_ascii=False, indent=2))
        else:
            out_path = export_dir / f"chapter_{ch_num:03d}.txt"
            lines = []
            for r in rows:
                if r["translated_text"]:
                    lines.append(f"[{r['speaker']}] {r['translated_text']}")
            out_path.write_text("\n".join(lines), encoding="utf-8")
        console.print(f"  Exported chapter {ch_num} → {out_path}")


@app.command()
def status(
    series: str = typer.Argument(..., help="Series slug"),
) -> None:
    """Show pipeline status for all chapters."""
    from toon.config import get_settings
    from toon import db
    from rich.table import Table

    settings = get_settings()
    db.set_db_path(settings.data_dir / "toon.db")
    db.init_db()

    series_id = db.get_series_id(series)
    if not series_id:
        console.print(f"[red]Series '{series}' not found.[/red]")
        raise typer.Exit(1)

    chapters = db.list_chapters(series_id)
    table = Table(title=f"Series: {series}")
    table.add_column("Chapter", style="cyan")
    table.add_column("Scraped")
    table.add_column("Extracted")
    table.add_column("Translated")

    for ch in chapters:
        table.add_row(
            str(ch["chapter_num"]),
            "[green]✓[/green]" if ch["scraped_at"] else "[red]✗[/red]",
            "[green]✓[/green]" if ch["extracted_at"] else "[red]✗[/red]",
            "[green]✓[/green]" if ch["translated_at"] else "[red]✗[/red]",
        )
    console.print(table)


@app.command()
def characters(
    series: str = typer.Argument(..., help="Series slug"),
) -> None:
    """List character profiles."""
    from toon.config import get_settings
    from toon import db
    from rich.table import Table

    settings = get_settings()
    db.set_db_path(settings.data_dir / "toon.db")
    db.init_db()

    series_id = db.get_series_id(series)
    if not series_id:
        console.print(f"[red]Series '{series}' not found.[/red]")
        raise typer.Exit(1)

    profiles = db.get_character_profiles(series_id)
    if not profiles:
        console.print("[yellow]No character profiles yet. Run: toon profile <series>[/yellow]")
        return

    table = Table(title=f"Characters: {series}")
    table.add_column("Name", style="bold cyan")
    table.add_column("Tone")
    table.add_column("Speech style")
    table.add_column("Vietnamese guide (excerpt)")

    for p in profiles:
        guide = p["vietnamese_voice_guide"][:60] + "..." if len(p["vietnamese_voice_guide"]) > 60 else p["vietnamese_voice_guide"]
        table.add_row(p["name"], p["tone"], p["speech_style"][:40], guide)
    console.print(table)


@app.command()
def api(
    port: int = typer.Option(7861, "--port", "-p", help="Port for the API server"),
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes"),
) -> None:
    """Start the REST API server for the Chrome extension."""
    try:
        import uvicorn
    except ImportError:
        console.print("[red]uvicorn not installed. Run: pip install uvicorn fastapi[/red]")
        raise typer.Exit(1)

    console.print(f"[green]Starting Toon API on http://{host}:{port}[/green]")
    uvicorn.run("toon.api:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
