// Runs extension/background.js with a fake Chrome API: Fitly tabs are shown one after another, then the first.
const fs = require("fs"), vm = require("vm"), path = require("path");
const store = {}, activated = [], listeners = {};
const ev = (name) => ({ addListener: (f) => { listeners[name] = f; } });
const chrome = {
  storage: { session: { get: async (k) => ({ [k]: store[k] }), set: async (o) => Object.assign(store, o),
                        remove: async (k) => { delete store[k]; } },
             local: { get: async () => ({ loaded: "v" }), set: async () => {}, remove: async () => {} } },
  tabs: { update: async (id, o) => { if (o.active) activated.push(id); }, onCreated: ev("created"), onRemoved: ev("removed"),
          reload: () => {}, query: async () => [], sendMessage: async () => {} },
  runtime: { onMessage: ev("message"), onInstalled: ev("installed"), reload: () => {} },
  commands: { onCommand: ev("command") }, scripting: { executeScript: async () => {} },
};
const fetch = async (url) => ({ json: async () => (url.endsWith("/ping") ? { extension: "v" } : { ok: true }) });
const ctx = { chrome, fetch, console, setTimeout: (f, ms) => (ms > 1000 ? 0 : setTimeout(f, ms)), Date, Promise, URL };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "..", "..", "extension", "background.js"), "utf8"), ctx);
const send = (tabId, msg) => new Promise((res) => listeners.message(msg, { tab: { id: tabId } }, res));
(async () => {
  for (const id of [11, 12, 13]) await send(id, { type: "hello", url: `http://127.0.0.1:8765/go?job=${id}#fitly-fill=${id}` });
  await new Promise((r) => setTimeout(r, 50));
  const afterHello = [...activated];
  await send(12, { type: "report", final: false, payload: { job_id: "12", status: "platform", filled: 0 } }); // 12 done early
  await send(11, { type: "report", final: true, payload: { job_id: "11", status: "prefilled", filled: 5 } });
  await new Promise((r) => setTimeout(r, 50));
  await send(13, { type: "report", final: true, payload: { job_id: "13", status: "prefilled", filled: 4 } });
  await new Promise((r) => setTimeout(r, 50));
  console.log(JSON.stringify({ afterHello, activated }));
})();
