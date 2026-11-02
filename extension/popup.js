// Runs only when the user clicks the extension button: reads the current page, sends it to the local Fitly.
const ENDPOINT = "http://127.0.0.1:8765/capture";

// Executed inside the page (no network access from here): structured JobPosting data + visible job links.
function extractPage() {
  const postings = [];
  const collect = (node) => {
    if (!node) return;
    if (Array.isArray(node)) { node.forEach(collect); return; }
    if (typeof node !== "object") return;
    const type = node["@type"];
    if (type === "JobPosting" || (Array.isArray(type) && type.includes("JobPosting"))) postings.push(node);
    if (node["@graph"]) collect(node["@graph"]);
  };
  document.querySelectorAll('script[type="application/ld+json"]').forEach((s) => {
    try { collect(JSON.parse(s.textContent)); } catch (e) { /* ignore malformed blocks */ }
  });
  const links = [];
  const seen = new Set();
  for (const a of document.querySelectorAll("a[href]")) {
    const href = a.href;
    if (!href || seen.has(href) || href.startsWith("javascript:")) continue;
    seen.add(href);
    const card = a.closest("li, article, [data-job-id], [data-jk], [class*='card'], [class*='Card']") || a.parentElement;
    links.push({
      href,
      text: (a.innerText || a.getAttribute("aria-label") || "").trim().slice(0, 200),
      card: card ? (card.innerText || "").trim().slice(0, 600) : "",
    });
    if (links.length >= 600) break;
  }
  return { url: location.href, title: document.title, postings, links };
}

function show(html, cls) {
  const s = document.getElementById("status");
  s.className = cls || "";
  s.innerHTML = html;
}

document.getElementById("capture").addEventListener("click", async () => {
  const button = document.getElementById("capture");
  button.disabled = true;
  show("Lecture de la page…");
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const [{ result }] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: extractPage });
    show("Envoi à Fitly…");
    const resp = await fetch(ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(result),
    });
    const data = await resp.json();
    if (!data.ok) throw new Error(data.message || resp.status);
    let html = `<b>${data.message}</b>`;
    if (data.best && data.best.length) {
      html += "<ul>" + data.best.map((j) => `<li>[${Math.round(j.score)}] ${j.title} — ${j.company || "?"}</li>`).join("") + "</ul>";
    }
    show(html, data.saved ? "ok" : "");
  } catch (e) {
    show("Impossible de joindre Fitly. Vérifie que l'interface est ouverte (.\\scripts\\start_ui.ps1).<br><small>" +
         String(e.message || e) + "</small>", "err");
  } finally {
    button.disabled = false;
  }
});

// One-click fill of the form on the current page (same as Alt+Shift+P).
document.getElementById("fill").addEventListener("click", () => {
  const btn = document.getElementById("fill");
  btn.disabled = true;
  chrome.runtime.sendMessage({ type: "manual_fill_active" }, (r) => {
    btn.disabled = false;
    if (r && r.ok) {
      show(r.jobId === "manual" ? "✍️ Pré-remplissage lancé (offre inconnue de Fitly : tes infos et ton CV par défaut)."
                                : "✍️ Pré-remplissage lancé pour l'offre n° " + r.jobId + ".", "ok");
      setTimeout(() => window.close(), 1200);
    } else {
      show((r && r.message) || "Impossible de pré-remplir cette page.", "err");
    }
  });
});
