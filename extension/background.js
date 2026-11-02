// Bridge between pages and the local Fitly (http://127.0.0.1:8765). Only tabs opened by Fitly
// (URL marker #fitly-fill=<id>) -- and tabs they open (redirect to the employer's site) -- are pre-filled.
const API = "http://127.0.0.1:8765";
const LINK_WINDOW_MS = 10 * 60 * 1000;   // a tab opened from a marked tab within 10 min inherits its offer

const key = (tabId) => `tab-${tabId}`;
async function getJob(tabId) {
  const s = await chrome.storage.session.get(key(tabId));
  return s[key(tabId)] || null;
}
async function setJob(tabId, job) { await chrome.storage.session.set({ [key(tabId)]: job }); }

// Self-update: Fitly's files changed on disk (new version) -> reload the extension, and the page.
async function diskVersion() {
  try { return (await (await fetch(`${API}/ping`)).json()).extension || null; } catch (e) { return null; }
}
chrome.runtime.onInstalled.addListener(async () => {
  await chrome.storage.local.set({ loaded: await diskVersion() });
});
async function outdated() {
  const [{ loaded }, disk] = [await chrome.storage.local.get("loaded"), await diskVersion()];
  if (!disk) return false;
  if (!loaded) { await chrome.storage.local.set({ loaded: disk }); return false; }
  return loaded !== disk;
}

// Preparation: Chrome throttles background tabs (timers slowed, many sites only draw their form when
// visible), so tabs opened by Fitly are shown one after another until each has been filled (or 25 s),
// then the first one is brought back: forms are ready before the user looks at them.
const PREP_TIMEOUT_MS = 25000;
const TERMINAL = new Set(["prefilled", "closed", "no_form", "platform", "login", "captcha", "stopped", "audit"]);
let prepLock = Promise.resolve();               // serialise: 15 tabs announce themselves at once
function serial(fn) { const run = prepLock.then(fn, fn); prepLock = run.catch(() => {}); return run; }
async function prepState() { return (await chrome.storage.session.get("prep")).prep || { queue: [], done: [] }; }
async function savePrep(p) { await chrome.storage.session.set({ prep: p }); }
function prepEnqueue(tabId) { return serial(() => _prepEnqueue(tabId)); }
async function _prepEnqueue(tabId) {
  const p = await prepState();
  if (!p.queue.includes(tabId) && p.current !== tabId) p.queue.push(tabId);
  if (p.first == null) p.first = tabId;
  await savePrep(p);
  await _prepAdvance();
}
function prepAdvance(force) { return serial(() => _prepAdvance(force)); }
async function _prepAdvance(force) {
  const p = await prepState();
  if (p.current != null && !force && Date.now() < (p.deadline || 0)) return;   // still preparing this tab
  p.current = null;
  while (p.queue.length) {
    const next = p.queue.shift();
    if (p.done.includes(next)) continue;
    try {
      await chrome.tabs.update(next, { active: true });
      p.current = next;
      p.deadline = Date.now() + PREP_TIMEOUT_MS;
      break;
    } catch (e) { /* tab closed meanwhile */ }
  }
  if (p.current == null && p.first != null) {        // all prepared: back to the first offer
    try { await chrome.tabs.update(p.first, { active: true }); } catch (e) { /* closed */ }
    await savePrep({ queue: [], done: [] });
    return;
  }
  await savePrep(p);
  setTimeout(() => prepAdvance(), PREP_TIMEOUT_MS + 500);   // a tab that never reports does not block the rest
}
function prepReported(tabId, status) {
  if (!TERMINAL.has(status)) return Promise.resolve();
  return serial(async () => {
    const p = await prepState();
    if (!p.done.includes(tabId)) p.done.push(tabId);
    await savePrep(p);
    if (p.current === tabId) await _prepAdvance(true);
  });
}

// One-click fill (Alt+Shift+P or the popup button) on ANY page the user is on, at any step of a form:
// every frame of the tab fills what it recognises. The user triggers it; nothing is ever submitted.
async function manualFill(tab) {
  if (!tab || tab.id == null) return { ok: false, message: "Aucun onglet actif." };
  let job = await getJob(tab.id);
  let jobId = job && job.jobId;
  if (!jobId && tab.url) {
    try {
      const r = await (await fetch(`${API}/fill/resolve?url=${encodeURIComponent(tab.url)}`)).json();
      jobId = r && r.job_id ? String(r.job_id) : null;
    } catch (e) { return { ok: false, message: "Fitly n'est pas ouvert sur ton PC." }; }
  }
  jobId = jobId || "manual";
  await setJob(tab.id, { jobId, done: false, filled: 0, at: Date.now() });
  const msg = { type: "manual_fill", jobId };
  try {
    await chrome.tabs.sendMessage(tab.id, msg);
  } catch (e) {                                  // page opened before the extension was (re)loaded
    await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames: true }, files: ["content.js"] });
    await chrome.tabs.sendMessage(tab.id, msg);
  }
  return { ok: true, jobId };
}
chrome.commands.onCommand.addListener(async (command, tab) => {
  if (command === "manual-fill") await manualFill(tab);
});

// Redirect flows: "Postuler" on a job board opens the employer's form in a new tab.
chrome.tabs.onCreated.addListener(async (tab) => {
  if (tab.openerTabId == null) return;
  const parent = await getJob(tab.openerTabId);
  if (parent && Date.now() - (parent.at || 0) < LINK_WINDOW_MS) {
    await setJob(tab.id, { jobId: parent.jobId, done: false, filled: 0, at: Date.now() });
  }
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    const tabId = sender.tab && sender.tab.id;
    if (msg.type === "hello") {
      const m = /#fitly-fill=(\d+)/.exec(msg.url || "");
      if ((m || (tabId != null && (await getJob(tabId)))) && (await outdated())) {
        sendResponse({ fill: false, updating: true });
        await chrome.storage.local.remove("loaded");
        setTimeout(() => { if (tabId != null) chrome.tabs.reload(tabId); chrome.runtime.reload(); }, 300);
        return;
      }
      if (m && tabId != null) await setJob(tabId, { jobId: m[1], done: false, filled: 0, at: Date.now() });
      if (m && tabId != null && /^http:\/\/127\.0\.0\.1:8765\/go\?/.test(msg.url || "")) prepEnqueue(tabId);
      const job = tabId != null ? await getJob(tabId) : null;
      const live = job && !job.done && Date.now() - (job.at || 0) < LINK_WINDOW_MS;
      sendResponse(live ? { fill: true, jobId: job.jobId } : { fill: false });
    } else if (msg.type === "manual_fill_self") {      // from the "✍️ Pré-remplir" button on the page
      sendResponse(await manualFill(sender.tab));
    } else if (msg.type === "manual_fill_active") {    // from the popup button
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      sendResponse(await manualFill(tab));
    } else if (msg.type === "status") {
      sendResponse((tabId != null && (await getJob(tabId))) || {});
    } else if (msg.type === "plan") {
      const r = await fetch(`${API}/fill/plan`, { method: "POST", headers: { "Content-Type": "application/json" },
                                                 body: JSON.stringify(msg.payload) });
      sendResponse(await r.json());
    } else if (msg.type === "cv") {
      const r = await fetch(`${API}/fill/cv?job_id=${encodeURIComponent(msg.jobId)}`);
      sendResponse(await r.json());
    } else if (msg.type === "report") {
      await fetch(`${API}/fill/report`, { method: "POST", headers: { "Content-Type": "application/json" },
                                          body: JSON.stringify(msg.payload) });
      if (tabId != null) {
        const job = (await getJob(tabId)) || { jobId: msg.payload.job_id, at: Date.now() };
        job.filled = (job.filled || 0) + (msg.payload.filled || 0);
        if (msg.final) job.done = true;   // keep the marker otherwise: the next page of this tab is tried again
        job.at = Date.now();
        await setJob(tabId, job);
        prepReported(tabId, msg.payload.status);
      }
      sendResponse({ ok: true });
    } else {
      sendResponse({ ok: false });
    }
  })().catch((e) => sendResponse({ ok: false, message: String(e) }));
  return true;   // async response
});

chrome.tabs.onRemoved.addListener((tabId) => chrome.storage.session.remove(key(tabId)));
