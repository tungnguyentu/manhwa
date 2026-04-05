const apiInput = document.getElementById("api-base");
const dot = document.getElementById("status-dot");
const statusText = document.getElementById("status-text");

// Load saved API base URL
chrome.storage.local.get(["apiBase"], (r) => {
  apiInput.value = r.apiBase || "http://127.0.0.1:7861";
  checkBackend(apiInput.value);
});

// Save on change
apiInput.addEventListener("change", () => {
  const val = apiInput.value.trim().replace(/\/$/, "");
  chrome.storage.local.set({ apiBase: val });
  checkBackend(val);
});

async function checkBackend(base) {
  dot.className = "dot checking";
  statusText.textContent = "Checking backend…";
  try {
    const res = await fetch(`${base}/health`, { signal: AbortSignal.timeout(3000) });
    if (res.ok) {
      dot.className = "dot ok";
      statusText.textContent = "Backend connected ✓";
    } else {
      throw new Error(`HTTP ${res.status}`);
    }
  } catch (e) {
    dot.className = "dot err";
    statusText.textContent = `Backend offline — run: toon api`;
  }
}
