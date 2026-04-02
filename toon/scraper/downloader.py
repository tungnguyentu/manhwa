from __future__ import annotations

import asyncio
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from toon.config import _Settings

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

# JS to extract panel image URLs from a loaded page
_EXTRACT_IMAGES_JS = """
() => {
  const imgs = Array.from(document.querySelectorAll('img'));
  return imgs
    .map(img => img.src || img.dataset.src || img.dataset.lazySrc || '')
    .filter(src => src && !src.startsWith('data:') &&
      (src.includes('.jpg') || src.includes('.png') || src.includes('.webp')))
    .filter((src, i, arr) => arr.indexOf(src) === i);
}
"""


def url_to_slug(url: str) -> str:
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    for i, part in enumerate(parts):
        if part.startswith("truyen") or part.startswith("manga") or part.startswith("comic"):
            if i + 1 < len(parts):
                return parts[i + 1]
            return parts[i]
    return parts[1] if len(parts) > 1 else parts[0]


def _filter_panel_images(urls: list[str], page_url: str) -> list[str]:
    """Keep only panel images (filter out logos, avatars, UI images)."""
    parsed_host = urlparse(page_url).netloc
    result = []
    seen: set[str] = set()
    for url in urls:
        if url in seen:
            continue
        # Skip same-domain non-panel images (logos, thumbnails, UI)
        u_parsed = urlparse(url)
        if u_parsed.netloc == parsed_host:
            path = u_parsed.path.lower()
            if any(x in path for x in ("logo", "avatar", "icon", "thumb", "banner", "button")):
                continue
        seen.add(url)
        result.append(url)
    return result


async def _get_image_urls_via_playwright_mcp(url: str) -> list[str]:
    """
    Get panel image URLs by launching a real browser via Playwright MCP.
    This is called from the CLI/app — the actual MCP navigation happens via Claude's tools.
    Returns image URLs extracted from the live page.
    """
    # This function is the bridge — in normal usage it's called from the CLI
    # The MCP-based approach runs through Claude's browser session
    # For programmatic use, fall back to Playwright directly
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        for browser_type in [p.firefox, p.chromium]:
            try:
                browser = await browser_type.launch(headless=True)
                ctx = await browser.new_context(
                    user_agent=HEADERS["User-Agent"],
                    viewport={"width": 1280, "height": 900},
                )
                await ctx.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
                )
                page = await ctx.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(2000)
                # Scroll down gradually to trigger lazy-loading of all panel images
                await page.evaluate("""
                    async () => {
                        await new Promise(resolve => {
                            let total = document.body.scrollHeight;
                            let current = 0;
                            const step = 800;
                            const timer = setInterval(() => {
                                window.scrollBy(0, step);
                                current += step;
                                if (current >= total) {
                                    clearInterval(timer);
                                    resolve();
                                }
                            }, 150);
                        });
                    }
                """)
                await page.wait_for_timeout(2000)
                urls = await page.evaluate(_EXTRACT_IMAGES_JS)
                await browser.close()
                if urls:
                    return urls
            except Exception:
                try:
                    await browser.close()
                except Exception:
                    pass
    return []


async def _download_images(
    image_urls: list[str],
    output_dir: Path,
    referer: str,
    settings: "_Settings",
    progress_cb=None,
) -> list[Path]:
    """Download images from CDN using httpx (CDN is less restrictive than main site)."""
    saved: list[Path] = []
    total = len(image_urls)

    async with httpx.AsyncClient(
        headers={**HEADERS, "Referer": referer},
        follow_redirects=True,
        timeout=30,
        verify=False,
    ) as client:
        for i, img_url in enumerate(image_urls):
            ext = Path(urlparse(img_url).path).suffix.lower() or ".jpg"
            if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
                ext = ".jpg"
            dest = output_dir / f"{i + 1:03d}{ext}"

            if dest.exists():
                saved.append(dest)
            else:
                try:
                    r = await client.get(img_url)
                    r.raise_for_status()
                    dest.write_bytes(r.content)
                    saved.append(dest)
                except Exception:
                    pass

            if progress_cb:
                progress_cb(i + 1, total)
            await asyncio.sleep(settings.scrape_delay * 0.2)

    return saved


async def scrape_chapter(
    series_slug: str,
    chapter_num: int,
    url: str,
    settings: "_Settings",
    progress_cb=None,
    image_urls: list[str] | None = None,
) -> list[Path]:
    """
    Download all panel images for a chapter.

    If image_urls is provided (from MCP browser extraction), download directly.
    Otherwise, attempt browser-based extraction.
    """
    output_dir = settings.data_dir / "images" / series_slug / f"{chapter_num:03d}"
    output_dir.mkdir(parents=True, exist_ok=True)

    if not image_urls:
        image_urls = await _get_image_urls_via_playwright_mcp(url)

    panel_urls = _filter_panel_images(image_urls, url)
    if not panel_urls:
        raise ValueError(f"No panel images found for {url}")

    return await _download_images(panel_urls, output_dir, url, settings, progress_cb)


async def scrape_from_html(
    series_slug: str,
    chapter_num: int,
    html_source: str,
    page_url: str,
    settings: "_Settings",
    progress_cb=None,
) -> list[Path]:
    """Download panel images using HTML source pasted from browser."""
    output_dir = settings.data_dir / "images" / series_slug / f"{chapter_num:03d}"
    output_dir.mkdir(parents=True, exist_ok=True)

    soup = BeautifulSoup(html_source, "html.parser")
    imgs: list[str] = []
    for img in soup.find_all("img"):
        src = img.get("data-src") or img.get("data-lazy-src") or img.get("src") or ""
        src = src.strip()
        if src and not src.startswith("data:"):
            imgs.append(urljoin(page_url, src))

    panel_urls = _filter_panel_images(imgs, page_url)
    if not panel_urls:
        raise ValueError("No images found in the HTML.")

    return await _download_images(panel_urls, output_dir, page_url, settings, progress_cb)


async def scrape_chapter_with_urls(
    series_slug: str,
    chapter_num: int,
    image_urls: list[str],
    page_url: str,
    settings: "_Settings",
    progress_cb=None,
) -> list[Path]:
    """Download panel images given explicit URLs (from MCP browser extraction)."""
    output_dir = settings.data_dir / "images" / series_slug / f"{chapter_num:03d}"
    output_dir.mkdir(parents=True, exist_ok=True)
    panel_urls = _filter_panel_images(image_urls, page_url)
    return await _download_images(panel_urls, output_dir, page_url, settings, progress_cb)


async def discover_chapters(series_url: str, settings: "_Settings") -> list[dict]:
    """Discover chapter list from a series page using Playwright."""
    from playwright.async_api import async_playwright

    parsed = urlparse(series_url)
    parts = [p for p in parsed.path.split("/") if p]
    series_root = series_url
    for i, part in enumerate(parts):
        if re.match(r"chapter[-_]?\d+", part, re.IGNORECASE):
            root_path = "/" + "/".join(parts[:i]) + "/"
            series_root = f"{parsed.scheme}://{parsed.netloc}{root_path}"
            break

    html = ""
    async with async_playwright() as p:
        for browser_type in [p.firefox, p.chromium]:
            try:
                browser = await browser_type.launch(headless=True)
                page = await browser.new_page()
                await page.goto(series_root, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(2000)
                html = await page.content()
                await browser.close()
                if html:
                    break
            except Exception:
                pass

    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    chapters: list[dict] = []
    for sel in [".wp-manga-chapter a", ".chapter-list a", ".chapters a", ".listing-chapters_wrap a"]:
        links = soup.select(sel)
        if links:
            for link in links:
                href = link.get("href", "")
                match = re.search(r"chapter[-_]?(\d+(?:\.\d+)?)", href, re.IGNORECASE)
                if match:
                    chapters.append({
                        "chapter_num": int(float(match.group(1))),
                        "url": urljoin(series_url, href),
                    })
            break

    seen: set[int] = set()
    result = []
    for ch in sorted(chapters, key=lambda x: x["chapter_num"]):
        if ch["chapter_num"] not in seen:
            seen.add(ch["chapter_num"])
            result.append(ch)
    return result
