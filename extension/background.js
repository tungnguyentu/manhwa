// Background service worker — handles API calls to the local Toon backend.
// Content scripts message this worker; it makes fetch() calls to localhost.

const DEFAULT_API = "http://127.0.0.1:7861";

async function getApiBase() {
  return new Promise((resolve) => {
    chrome.storage.local.get(["apiBase"], (r) => {
      resolve(r.apiBase || DEFAULT_API);
    });
  });
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "PROCESS_CHAPTER") {
    handleProcessChapter(msg.payload).then(sendResponse).catch((e) => {
      sendResponse({ error: e.message });
    });
    return true; // async
  }

  if (msg.type === "POLL_STATUS") {
    handlePollStatus(msg.chapterId).then(sendResponse).catch((e) => {
      sendResponse({ error: e.message });
    });
    return true;
  }

  if (msg.type === "GET_TRANSLATIONS") {
    handleGetTranslations(msg.chapterId).then(sendResponse).catch((e) => {
      sendResponse({ error: e.message });
    });
    return true;
  }

  if (msg.type === "CHECK_HEALTH") {
    checkHealth().then(sendResponse).catch(() => sendResponse({ ok: false }));
    return true;
  }

  if (msg.type === "LEARN_CHAPTER") {
    handleLearnChapter(msg.payload).then(sendResponse).catch((e) => {
      sendResponse({ error: e.message });
    });
    return true;
  }
});

async function checkHealth() {
  const base = await getApiBase();
  const res = await fetch(`${base}/health`, { signal: AbortSignal.timeout(3000) });
  return { ok: res.ok };
}

async function handleProcessChapter(payload) {
  const base = await getApiBase();
  const res = await fetch(`${base}/api/process`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API error ${res.status}: ${text}`);
  }
  return await res.json();
}

async function handlePollStatus(chapterId) {
  const base = await getApiBase();
  const res = await fetch(`${base}/api/chapter/${chapterId}/status`);
  if (!res.ok) throw new Error(`Status check failed: ${res.status}`);
  return await res.json();
}

async function handleLearnChapter(payload) {
  const base = await getApiBase();
  const res = await fetch(`${base}/api/learn`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API error ${res.status}: ${text}`);
  }
  return await res.json();
}

async function handleGetTranslations(chapterId) {
  const base = await getApiBase();
  const res = await fetch(`${base}/api/chapter/${chapterId}/translations`);
  if (!res.ok) throw new Error(`Translations fetch failed: ${res.status}`);
  return await res.json();
}
