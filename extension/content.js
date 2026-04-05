// Toon Translator — content script
// Sticky sidebar that updates as you scroll through webtoon panels.

(function () {
  "use strict";

  // ── Character colour palette ───────────────────────────────────────────────
  const PALETTE = [
    "#6c8ef5", // blue-indigo
    "#e86c6c", // coral red
    "#5cc98a", // mint green
    "#f0a84a", // amber
    "#c47de0", // violet
    "#4ac9d4", // teal
    "#f06090", // pink
    "#a0c060", // lime
    "#e0804a", // orange
    "#60a8e0", // sky blue
  ];
  const _charColors = {};
  let _colorIdx = 0;

  function speakerColor(name) {
    if (!name || name === "NARRATOR") return "#607080";
    if (name === "SFX") return "#f0c040";
    if (name === "UNKNOWN") return "#404060";
    if (!_charColors[name]) _charColors[name] = PALETTE[_colorIdx++ % PALETTE.length];
    return _charColors[name];
  }

  // ── State ──────────────────────────────────────────────────────────────────
  let chapterId = null;
  let pollTimer = null;
  let sentImageUrls = [];
  let allPanels = {};      // { "0": [{speaker, translated, original}], ... }
  let panelImgEls = [];
  let currentPanelIdx = -1;
  let sidebarOpen = true;

  // ── Boot ───────────────────────────────────────────────────────────────────
  init();

  async function init() {
    const health = await sendMsg({ type: "CHECK_HEALTH" });
    if (!health || !health.ok) return;

    const isChapterPage = /chapter[-_]?\d+/i.test(location.href);
    if (!isChapterPage) { setTimeout(prefetchNextChapter, 2000); return; }

    buildSidebar();
    showBanner("🌐 Toon Translator — detecting panels…", "info");
    await waitForPanelImages();

    sentImageUrls = collectPanelImageUrls();
    if (!sentImageUrls.length) { showBanner("❌ No panel images found", "error"); return; }

    showBanner(`📡 Sending ${sentImageUrls.length} panels to API…`, "info");
    const result = await sendMsg({
      type: "PROCESS_CHAPTER",
      payload: { url: location.href, image_urls: sentImageUrls, source_lang: detectSourceLang() },
    });

    if (result.error) { showBanner(`❌ ${result.error}`, "error"); return; }
    chapterId = result.chapter_id;

    if (result.status === "done") await loadTranslations();
    else { showBanner("⏳ Processing…", "info"); startPolling(); }
  }

  // ── Image helpers ──────────────────────────────────────────────────────────

  function collectPanelImageUrls() {
    const seen = new Set(), result = [];
    // Attributes to check in priority order (lazy-load patterns across sites)
    const LAZY_ATTRS = [
      "data-src", "data-lazy", "data-lazy-src", "data-original",
      "data-wpfc-original", "data-url", "data-image", "data-full-url",
    ];

    for (const img of document.querySelectorAll("img")) {
      let src = "";
      for (const attr of LAZY_ATTRS) {
        if (img.dataset[attr.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())]) {
          src = img.getAttribute(attr) || "";
          if (src) break;
        }
      }
      // Also check raw attributes in case dataset conversion misses hyphens
      if (!src) {
        for (const attr of LAZY_ATTRS) {
          src = img.getAttribute(attr) || "";
          if (src) break;
        }
      }
      if (!src) src = img.src || "";

      if (!src || src.startsWith("data:") || seen.has(src)) continue;

      // Accept image URLs: either has image extension OR is from a path that looks like panel content
      const hasImgExt = /\.(jpe?g|png|webp|gif)(\?|$)/i.test(src);
      const looksLikePanel = /\/(chapter|panel|page|images?|uploads?|content|scan)\//i.test(src);
      if (!hasImgExt && !looksLikePanel) continue;

      // Only use naturalWidth/Height (CSS width/height attribute can be thumbnail placeholders)
      const w = img.naturalWidth || 0;
      const h = img.naturalHeight || 0;
      if (w > 0 && w < 200) continue;
      if (h > 0 && h < 400) continue;

      seen.add(src); result.push(src);
    }
    return result;
  }

  async function waitForPanelImages(maxWait = 15000) {
    const t = Date.now();
    let scrolled = false;

    while (Date.now() - t < maxWait) {
      const found = collectPanelImageUrls();
      if (found.length >= 2) return;

      const elapsed = Date.now() - t;

      // After 2s with no results, scroll through the page to trigger lazy-load
      if (!scrolled && elapsed > 2000) {
        scrolled = true;
        _triggerLazyLoad();
      }

      // Log every 3s to help debug
      if (elapsed % 3000 < 400) {
        _logImgState(found.length);
      }

      await sleep(400);
    }
    _logImgState(0);  // final log on timeout
  }

  function _triggerLazyLoad() {
    // Scroll down in steps to trigger IntersectionObserver-based lazy loading
    const total = document.body.scrollHeight;
    const step = Math.min(window.innerHeight, 800);
    let pos = 0;
    const scroll = () => {
      window.scrollTo(0, pos);
      pos += step;
      if (pos < total) setTimeout(scroll, 80);
      else setTimeout(() => window.scrollTo(0, 0), 100);
    };
    scroll();
  }

  function _logImgState(found) {
    const allImgs = Array.from(document.querySelectorAll("img"));
    console.debug(`[Toon] ${found} panel imgs found of ${allImgs.length} total`);
    allImgs.slice(0, 25).forEach((img, i) => {
      const allAttrs = Array.from(img.attributes)
        .filter(a => a.name.startsWith("data-") || a.name === "src")
        .map(a => `${a.name}="${a.value.slice(0,80)}"`)
        .join(" ");
      console.debug(`[Toon] [${i}] ${img.naturalWidth}×${img.naturalHeight} class="${img.className.slice(0,40)}" ${allAttrs}`);
    });
  }

  function detectSourceLang() {
    if (/manhwa|toon/i.test(location.hostname)) return "ko";
    if (/hentai|vnx|truyenhentai/i.test(location.hostname)) return "ja";
    return "en";
  }

  // ── Polling ────────────────────────────────────────────────────────────────

  function startPolling() {
    let attempts = 0;
    pollTimer = setInterval(async () => {
      if (++attempts > 180) { clearInterval(pollTimer); showBanner("⏱️ Timed out", "error"); return; }
      const s = await sendMsg({ type: "POLL_STATUS", chapterId });
      if (!s || s.error) return;
      const labels = {
        scraping: "⬇️ Downloading images…",
        extracting: `🔍 ${s.message || "Running OCR…"}`,
        translating: `⚙️ ${s.message || "Translating…"}`,
      };
      showBanner(labels[s.status] || `⏳ ${s.status}`, "info");
      if (s.status === "done") { clearInterval(pollTimer); await loadTranslations(); }
      else if (s.status === "error") { clearInterval(pollTimer); showBanner(`❌ ${s.message}`, "error"); }
    }, 2000);
  }

  // ── Load & attach ──────────────────────────────────────────────────────────

  async function loadTranslations() {
    const data = await sendMsg({ type: "GET_TRANSLATIONS", chapterId });
    if (!data || data.error) { showBanner(`❌ ${data?.error || "Failed"}`, "error"); return; }
    if (!data.translated_count) { showBanner("⚠️ No translations yet", "warn"); return; }

    allPanels = data.panels || {};

    // Pre-compute character colours from all speakers
    Object.values(allPanels).flat().forEach((d) => speakerColor(d.speaker));

    // Build panel dot indicators
    const total = Object.keys(allPanels).length;
    const dotsEl = document.getElementById("toon-panel-dots");
    if (dotsEl) {
      dotsEl.innerHTML = Object.keys(allPanels)
        .sort((a, b) => parseInt(a) - parseInt(b))
        .map((k, i) => `<div class="toon-panel-dot" data-panel="${k}" id="toon-dot-${k}"></div>`)
        .join("");
    }

    // Map sent URLs → DOM img elements
    panelImgEls = sentImageUrls.map((url) =>
      document.querySelector(`img[src="${url}"]`) ||
      document.querySelector(`img[data-src="${url}"]`) ||
      document.querySelector(`img[data-original="${url}"]`)
    );

    setupScrollObserver();

    const count = data.translated_count;
    showBanner(`✅ ${count} dialogues ready — scroll to read`, "success");
    setTimeout(() => {
      const b = document.getElementById("toon-ext-banner");
      if (b) b.style.display = "none";
    }, 3500);
  }

  // ── Scroll → sidebar sync ──────────────────────────────────────────────────

  function setupScrollObserver() {
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }

  function onScroll() {
    if (!panelImgEls.length) return;
    const vh = window.innerHeight;
    let best = -1, bestScore = -Infinity;

    panelImgEls.forEach((img, i) => {
      if (!img) return;
      const r = img.getBoundingClientRect();
      // Prefer the image that covers most of the viewport
      const visTop = Math.max(r.top, 0);
      const visBot = Math.min(r.bottom, vh);
      const coverage = visBot - visTop;
      if (coverage > 0 && coverage > bestScore) { bestScore = coverage; best = i; }
    });

    if (best !== -1 && best !== currentPanelIdx) {
      currentPanelIdx = best;
      renderSidebar(best);
      updateDots(best);
    }
  }

  function updateDots(activeIdx) {
    document.querySelectorAll(".toon-panel-dot").forEach((dot) => {
      dot.classList.toggle("active", parseInt(dot.dataset.panel) === activeIdx);
    });
  }

  // ── Sidebar render ─────────────────────────────────────────────────────────

  function buildSidebar() {
    if (document.getElementById("toon-sidebar")) return;

    const sidebar = document.createElement("div");
    sidebar.id = "toon-sidebar";
    sidebar.innerHTML = `
      <div id="toon-sidebar-header">
        <span id="toon-sidebar-title">🎌 Toon Translator</span>
        <div style="display:flex;gap:5px;align-items:center">
          <button id="toon-learn-btn" title="Learn style from this Vietnamese chapter">📚 Learn</button>
          <button id="toon-sidebar-toggle" title="Hide (Alt+T)">✕</button>
        </div>
      </div>
      <div id="toon-panel-bar">
        <span id="toon-panel-dots"></span>
      </div>
      <div id="toon-sidebar-body">
        <div id="toon-sidebar-empty">Waiting for translations…</div>
      </div>`;
    document.body.appendChild(sidebar);
    document.body.classList.add("toon-sidebar-open");

    const tab = document.createElement("div");
    tab.id = "toon-sidebar-tab";
    tab.textContent = "翻 TOON";
    document.body.appendChild(tab);

    document.getElementById("toon-sidebar-toggle").addEventListener("click", () => toggleSidebar(false));
    tab.addEventListener("click", () => toggleSidebar(true));
    document.getElementById("toon-learn-btn").addEventListener("click", triggerLearn);
  }

  function toggleSidebar(open) {
    sidebarOpen = open;
    const sidebar = document.getElementById("toon-sidebar");
    const tab = document.getElementById("toon-sidebar-tab");
    if (sidebar) sidebar.style.display = open ? "flex" : "none";
    if (tab) tab.style.display = open ? "none" : "block";
    document.body.classList.toggle("toon-sidebar-open", open);
    document.body.classList.toggle("toon-sidebar-closed", !open);
  }

  function renderSidebar(panelIdx) {
    const body = document.getElementById("toon-sidebar-body");
    if (!body) return;

    // Find dialogues for this panel; fall back to nearest panel with content
    let dialogues = null;
    for (let offset = 0; offset <= 3; offset++) {
      const d = allPanels[String(panelIdx - offset)] || allPanels[panelIdx - offset];
      if (d && d.some((x) => x.translated)) { dialogues = d; break; }
    }

    const panelKeys = Object.keys(allPanels).sort((a, b) => parseInt(a) - parseInt(b));
    const total = panelKeys.length;
    const displayNum = panelIdx + 1;

    if (!dialogues) {
      body.innerHTML = `<div id="toon-sidebar-empty">Panel ${displayNum} / ${total}<br><br>No dialogue here</div>`;
      return;
    }

    const translated = dialogues.filter((d) => d.translated);
    if (!translated.length) {
      body.innerHTML = `<div id="toon-sidebar-empty">Panel ${displayNum} / ${total}<br><br>No dialogue here</div>`;
      return;
    }

    let html = "";
    for (const d of translated) {
      const color = speakerColor(d.speaker);
      const typeClass = d.speaker === "SFX" ? " sfx" : d.speaker === "NARRATOR" ? " narrator" : "";

      html += `<div class="toon-dlg${typeClass}">
        <div class="toon-dlg-head">
          <div class="toon-dlg-dot" style="background:${color}"></div>
          <div class="toon-dlg-speaker" style="color:${color}">${esc(d.speaker)}</div>
        </div>
        <div class="toon-dlg-body">
          <div class="toon-dlg-vi">${esc(d.translated)}</div>
          ${d.original ? `<div class="toon-dlg-orig">${esc(d.original)}</div>` : ""}
        </div>
      </div>`;
    }

    body.innerHTML = html;
    body.scrollTop = 0;
  }

  // ── Pre-fetch ──────────────────────────────────────────────────────────────

  async function prefetchNextChapter() {
    const links = Array.from(document.querySelectorAll("a[href]"))
      .map((a) => a.href)
      .filter((h) => /chapter[-_]?\d+/i.test(h) && h !== location.href);
    if (!links.length) return;
    const cur = location.href.match(/chapter[-_]?(\d+)/i);
    const curNum = cur ? parseInt(cur[1]) : -1;
    const next = links.find((h) => {
      const m = h.match(/chapter[-_]?(\d+)/i); return m && parseInt(m[1]) === curNum + 1;
    }) || links[0];
    const r = await sendMsg({ type: "PROCESS_CHAPTER", payload: { url: next, source_lang: detectSourceLang() } });
    if (r && r.status === "queued") console.debug("[Toon] Pre-fetching", next);
  }

  setTimeout(prefetchNextChapter, 5000);

  // ── Learn ──────────────────────────────────────────────────────────────────

  async function triggerLearn() {
    const btn = document.getElementById("toon-learn-btn");
    if (btn) { btn.disabled = true; btn.textContent = "⏳ Learning…"; }

    // Collect image URLs — reuse already-sent ones if available, else collect fresh
    const urls = sentImageUrls.length ? sentImageUrls : collectPanelImageUrls();
    if (!urls.length) {
      showBanner("❌ No panel images to learn from", "error");
      if (btn) { btn.disabled = false; btn.textContent = "📚 Learn"; }
      return;
    }

    showBanner("📚 Sending chapter for learning…", "info");
    const result = await sendMsg({
      type: "LEARN_CHAPTER",
      payload: { url: location.href, image_urls: urls },
    });

    if (result.error) {
      showBanner(`❌ Learn failed: ${result.error}`, "error");
      if (btn) { btn.disabled = false; btn.textContent = "📚 Learn"; }
      return;
    }

    const learnChapterId = result.chapter_id;
    showBanner("⏳ Learning in background…", "info");

    // Poll until done
    let attempts = 0;
    const learnPoll = setInterval(async () => {
      if (++attempts > 120) {
        clearInterval(learnPoll);
        showBanner("⏱️ Learn timed out", "error");
        if (btn) { btn.disabled = false; btn.textContent = "📚 Learn"; }
        return;
      }
      const s = await sendMsg({ type: "POLL_STATUS", chapterId: learnChapterId });
      if (!s || s.error) return;
      const labels = {
        scraping: "⬇️ Downloading images…",
        extracting: "🔍 Running OCR…",
        profiling: "👤 Building profiles…",
        learning: "🧠 Analyzing style…",
      };
      showBanner(labels[s.status] || `⏳ ${s.status}`, "info");
      if (s.status === "done") {
        clearInterval(learnPoll);
        showBanner("✅ Style learned! Future translations will use this style.", "success");
        if (btn) { btn.disabled = false; btn.textContent = "📚 Learn"; }
      } else if (s.status === "error") {
        clearInterval(learnPoll);
        showBanner(`❌ Learn error: ${s.message}`, "error");
        if (btn) { btn.disabled = false; btn.textContent = "📚 Learn"; }
      }
    }, 3000);
  }

  // ── Banner ─────────────────────────────────────────────────────────────────

  function showBanner(text, type = "info") {
    let b = document.getElementById("toon-ext-banner");
    if (!b) { b = document.createElement("div"); b.id = "toon-ext-banner"; document.body.prepend(b); }
    b.textContent = text;
    b.className = `toon-ext-banner toon-ext-banner--${type}`;
    b.style.display = "";
  }

  // ── Keyboard ───────────────────────────────────────────────────────────────
  document.addEventListener("keydown", (e) => {
    if (e.altKey && e.key === "t") toggleSidebar(!sidebarOpen);
  });

  // ── Helpers ────────────────────────────────────────────────────────────────
  function sendMsg(msg) {
    return new Promise((resolve) => {
      chrome.runtime.sendMessage(msg, (r) =>
        resolve(chrome.runtime.lastError ? { error: chrome.runtime.lastError.message } : r)
      );
    });
  }
  function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }
  function esc(s) {
    return (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

})();
