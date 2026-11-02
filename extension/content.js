// Pre-fills an application form in a tab opened by Fitly (or a tab it led to). Never clicks a submit button.
(() => {
  if (window.__fitlyContent) return;          // injected twice (manifest + one-click fill): run once
  window.__fitlyContent = true;
  // Never act inside an anti-robot challenge frame (the script now also runs in embedded frames).
  if (/(^|\.)(captcha-delivery\.com|hcaptcha\.com|challenges\.cloudflare\.com)$/.test(location.hostname) ||
      (/(^|\.)google\.com$/.test(location.hostname) && /recaptcha/.test(location.pathname))) return;
  const SKIP = new Set(["hidden", "submit", "button", "reset", "image"]);
  // Labels of buttons/links that OPEN a form ("je suis interesse e" = "Je suis intéressé(e)" folded).
  const OPENER = /^(postuler|je postule|postuler maintenant|postuler a cette offre|postuler sur le site( de l'entreprise)?|candidater|je candidate|candidater sur le site|apply|apply now|apply for this job|apply for this position|apply to this job|apply on (the )?(company|employer) (site|website)|i'm interested|i am interested|je suis interess(e|ee)?( e)?)$/;
  // Job platforms: their own pages are never automated here (terms of use); the user clicks "Postuler"
  // and the employer's form opened from there is pre-filled.
  const PLATFORMS = /(^|\.)(linkedin\.com|indeed\.[a-z.]+|apec\.fr|hellowork\.com|welcometothejungle\.com|cadremploi\.fr|jobteaser\.com|bebee\.com|lefigaro\.fr|jooble\.org|glassdoor\.[a-z.]+|monster\.[a-z.]+|meteojob\.com|free-work\.com|google\.[a-z.]+)$/;
  const CAPTCHA = /captcha-delivery\.com|recaptcha|hcaptcha|challenges\.cloudflare\.com|turnstile/i;
  const EXPIRED = /n'est plus disponible|n.est plus en ligne|offre (a )?expir|offre (est )?(close|cl[oô]tur|pourvue)|no longer (available|accepting|open)|job (has )?expired|position has been filled|this job is no longer|n'existe plus|a été pourvue|a ete pourvue|annonce (a été |a ete )?(retirée|supprimée|expirée)|offre (retirée|supprimée|désactivée)|plus d'actualité/i;
  const isTop = window === window.top;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const send = (msg) => new Promise((resolve) => chrome.runtime.sendMessage(msg, resolve));
  const fold = (t) => (t || "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9+#.' ]+/g, " ").replace(/\s+/g, " ").trim();
  const registry = [];                       // inspected elements (some live in shadow roots: no CSS path)

  // All elements matching `sel`, including inside open shadow roots.
  function deepAll(sel, root = document) {
    const out = [];
    const walk = (r) => {
      r.querySelectorAll(sel).forEach((e) => out.push(e));
      r.querySelectorAll("*").forEach((e) => { if (e.shadowRoot) walk(e.shadowRoot); });
    };
    walk(root);
    return out;
  }

  function labelOf(el) {
    let labelText = el.labels && el.labels.length ? Array.from(el.labels).map((l) => l.innerText).join(" ") : "";
    const ownText = labelText.trim();
    const aria = el.getAttribute("aria-label") || "";
    const byId = (el.getAttribute("aria-labelledby") || "").split(" ").map((id) => {
      const n = el.getRootNode().getElementById ? el.getRootNode().getElementById(id) : null;
      return n ? n.innerText : "";
    }).join(" ").trim();
    const weak = (t) => !t || t.trim().length < 2 || /^(attach|upload|type your response|start typing|select)/i.test(t.trim());
    if (el.type === "radio" || weak(labelText || byId || aria || el.getAttribute("placeholder"))) {   // a radio's own label is the option
      let cur = el.parentElement || (el.getRootNode().host || null);
      for (let i = 0; i < 6 && cur; i++, cur = cur.parentElement || (cur.getRootNode && cur.getRootNode().host) || null) {
        const cap = cur.querySelector && cur.querySelector('legend, label, [class*="label"], [class*="Label"], [class*="question"], h3, h4');
        if (cap && cap.innerText && !weak(cap.innerText)) { labelText = cap.innerText.split(String.fromCharCode(10))[0]; break; }
      }
    }
    return { label: (labelText || byId || aria || el.getAttribute("placeholder") || el.getAttribute("name") || "").trim(), ownText };
  }

  function inspect() {
    registry.length = 0;
    const fields = [];
    for (const el of deepAll("input, textarea, select")) {
      const type = (el.getAttribute("type") || el.tagName).toLowerCase();
      if (SKIP.has(type)) continue;
      const style = getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      const visible = type === "file" || (style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0);
      if (!visible) continue;
      const { label, ownText } = labelOf(el);
      registry.push(el);
      fields.push({ label, option: type === "radio" || type === "checkbox" ? ownText || el.value || "" : "",
                    name: el.getAttribute("name") || "", type, required: el.required || el.getAttribute("aria-required") === "true",
                    // Google's own field classification (Chrome flag "Show autofill predictions") + HTML autocomplete hint
                    prediction: el.getAttribute("autofill-prediction") || "", autocomplete: el.getAttribute("autocomplete") || "",
                    selector: "jp:" + (registry.length - 1) });
    }
    return fields;
  }

  const known = (fields) => fields.filter((f) => f.type === "file" || /^(NAME_|EMAIL|PHONE)/.test(f.prediction) ||
    /given-name|family-name|^name$|email|tel/.test(f.autocomplete) ||
    /pr[ée]nom|first ?name|\bnom\b|last ?name|full ?name|e.?mail|phone|t[ée]l[ée]phone/i.test(f.label)).length;
  const VERSION = (chrome.runtime.getManifest && chrome.runtime.getManifest().version) || "";

  // A challenge actually shown to the user (invisible reCAPTCHA/hCaptcha badges of Lever, Greenhouse... are ignored).
  function captchaPresent() {
    const shown = (f) => { const r = f.getBoundingClientRect(); return r.width >= 250 && r.height >= 250 &&
                                                                     getComputedStyle(f).visibility !== "hidden"; };
    return Array.from(document.querySelectorAll("iframe")).some((f) => CAPTCHA.test(f.src || "") && shown(f)) ||
           /verify you are human|je ne suis pas un robot|i am not a robot|pas (à|a) un robot|not a robot|faites glisser vers la droite|slide to the right|press (and|&) hold/i.test((document.body.innerText || "").slice(0, 5000));
  }

  // Anti-robot challenge (it may appear only after the page has loaded): never touched; Fitly reports it,
  // waits for the user to solve it (3 min max) and goes on. Returns false if it is still there.
  let captchaReported = false;
  async function captchaGate(base) {
    if (!captchaPresent()) return true;
    if (!captchaReported) {
      captchaReported = true;
      banner("🧭 <b>Fitly</b> : vérification anti-robot affichée. Résous-la toi-même ; le pré-remplissage reprendra ensuite.", "#fef3c7");
      await send({ type: "report", final: false, payload: { ...base, status: "captcha", filled: 0,
        reason: "CAPTCHA / vérification anti-robot : à résoudre par toi, le remplissage reprend ensuite" } });
    }
    const end = Date.now() + 180000;
    while (captchaPresent() && Date.now() < end) await sleep(1500);
    return !captchaPresent();
  }

  // Cookie banners: always the most privacy-preserving choice ("Tout refuser", "Reject all", "Continuer sans
  // accepter", "Nécessaires uniquement"). Never "accept". Known consent tools first, then any banner about cookies.
  const COOKIE_IDS = ["#onetrust-reject-all-handler", "#didomi-notice-disagree-button", "#axeptio_btn_dismiss",
    "#CybotCookiebotDialogBodyButtonDecline", "#CybotCookiebotDialogBodyLevelButtonLevelOptinDeclineAll",
    "[data-testid=uc-deny-all-button]", "#tarteaucitronAllDenied2", ".cc-deny", "#cookie-decline", "[data-cookie-decline]"];
  const REFUSE = /^(tout refuser|refuser tout|refuser( les cookies)?|tout rejeter|rejeter( tout)?|je refuse|continuer sans accepter|reject( all)?( cookies)?|decline( all)?( cookies)?|deny( all)?|refuse all|only (necessary|essential)( cookies)?|use necessary cookies only|n[ée]cessaires uniquement|cookies n[ée]cessaires uniquement|accepter uniquement les cookies (n[ée]cessaires|essentiels)|uniquement les cookies n[ée]cessaires)$/;
  function declineCookies() {
    for (const sel of COOKIE_IDS) {
      const b = deepAll(sel)[0];
      if (b && b.getBoundingClientRect().width > 0) { b.click(); return true; }
    }
    for (const b of deepAll("button, a[role=button], [role=button], a")) {
      const t = fold(b.innerText || b.textContent);
      if (!t || t.length > 60 || !REFUSE.test(t) || !b.getBoundingClientRect().width) continue;
      let box = b, about = false;
      for (let i = 0; i < 6 && box && box !== document.body && box !== document.documentElement;
           i++, box = box.parentElement || (box.getRootNode && box.getRootNode().host)) {
        if (/cookie|traceur|consent/i.test((box.innerText || "").slice(0, 3000))) { about = true; break; }
      }
      if (about) { b.click(); return true; }
    }
    return false;
  }
  async function declineCookiesSoon() {           // banners often appear a moment after the page
    for (let i = 0; i < 6; i++) { if (declineCookies()) return true; await sleep(700); }
    return false;
  }

  // After filling: take the user to each field left for them (orange), with a "next field" button.
  function guide(selectors) {
    const els = (selectors || []).map((s) => s.startsWith("jp:") ? registry[Number(s.slice(3))] : document.querySelector(s))
      .filter((e) => e && !(e.value && String(e.value).trim() && e.type !== "radio" && e.type !== "checkbox"));
    if (!els.length || !isTop) return;
    els.forEach((e) => { const t = e.type === "file" || e.type === "radio" || e.type === "checkbox" ? (e.parentElement || e) : e;
                         t.style.outline = "3px solid #f59e0b"; t.style.outlineOffset = "2px"; });
    let i = 0;
    const btn = document.createElement("button");
    btn.type = "button";                        // never a submit button
    btn.style.cssText = "position:fixed;right:20px;bottom:20px;z-index:2147483647;padding:12px 16px;border:0;border-radius:24px;" +
      "background:#f59e0b;color:#111;font:600 14px Segoe UI,Arial;box-shadow:0 4px 12px rgba(0,0,0,.25);cursor:pointer";
    const show = () => {
      const el = els[i % els.length];
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      btn.textContent = "🧭 À compléter " + ((i % els.length) + 1) + "/" + els.length + " · suivant ↓";
      i++;
    };
    btn.onclick = (e) => { e.preventDefault(); e.stopPropagation(); show(); };
    document.body.appendChild(btn);
    setTimeout(show, 600);
  }

  // Sign-in / account creation required before the form (Workday, Taleo, SuccessFactors, Indeed smart apply...).
  function loginWall() {
    const pwd = deepAll("input[type=password]").some((e) => e.getBoundingClientRect().width > 0);
    const text = (document.body.innerText || "").slice(0, 8000);
    return pwd || /se connecter|connectez-vous|cr[ée]er (un|mon) compte|sign in|log in|create (an )?account|identifiez-vous/i.test(text) &&
                  !/postuler|apply/i.test(text.slice(0, 300));
  }

  async function waitForFields(ms, base) {
    let end = Date.now() + ms;
    let fields = inspect();
    while (known(fields) < 2 && Date.now() < end) {
      await sleep(700);
      if (captchaPresent()) {
        if (!(await captchaGate(base))) return [];
        end = Date.now() + ms;                     // the form loads after the challenge
      }
      fields = inspect();
    }
    if (fields.length && !fields.some((f) => f.prediction)) { await sleep(1200); fields = inspect(); }  // Chrome annotates a bit later
    return fields;
  }

  async function openForm() {
    for (const el of deepAll("a, button, [role=button]")) {
      const text = fold(el.innerText || el.textContent);
      if (!text || text.length > 50 || !OPENER.test(text)) continue;
      if ((el.getAttribute("type") || "").toLowerCase() === "submit" || el.closest("form")) continue;
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) continue;
      el.click();        // opens the form; navigation or a new tab is followed by the extension
      await sleep(2500);
      return true;
    }
    return false;
  }

  function setValue(el, value) {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, value);   // React/Vue-controlled inputs
    el.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
    el.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
    el.dispatchEvent(new Event("blur", { bubbles: true, composed: true }));
  }

  function mark(el) { el.style.outline = "2px solid #22c55e"; el.style.backgroundColor = "#f0fdf4"; }

  async function apply(action, jobId, warnings) {
    const el = action.selector.startsWith("jp:") ? registry[Number(action.selector.slice(3))] : document.querySelector(action.selector);
    if (!el) { warnings.push("champ introuvable"); return false; }
    if (action.type === "file") {
      const cv = await send({ type: "cv", jobId });
      if (!cv || !cv.ok) { warnings.push(cv && cv.message ? cv.message : "CV non disponible"); return false; }
      const bytes = Uint8Array.from(atob(cv.data), (c) => c.charCodeAt(0));
      const dt = new DataTransfer();
      dt.items.add(new File([bytes], cv.filename, { type: cv.mime }));
      el.files = dt.files;
      el.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
      el.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
    } else if (action.type === "radio" || action.type === "checkbox") {
      if (!el.checked) el.click();
    } else if (el.tagName === "SELECT") {
      const want = fold(String(action.value));
      const opt = Array.from(el.options).find((o) => fold(o.text) === want || fold(o.value) === want || fold(o.text).startsWith(want));
      if (!opt) { warnings.push("option introuvable : " + action.value); return false; }
      el.value = opt.value;
      el.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
    } else {
      if (el.value && el.value.trim()) return false;   // already filled (Chrome autofill or the user): never overwrite
      setValue(el, String(action.value));
      if (el.getAttribute("role") === "combobox" || el.getAttribute("aria-autocomplete") || el.getAttribute("aria-haspopup") === "listbox") {
        // Searchable list (Greenhouse "Country"...): pick the option matching the validated value, nothing else.
        el.focus();
        el.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: "ArrowDown" }));
        await sleep(800);
        const want = fold(String(action.value));
        const opt = deepAll("[role=option]").find((o) => { const t = fold(o.innerText || o.textContent);
          return t && (t === want || t.startsWith(want)) && o.getBoundingClientRect().height > 0; });
        if (opt) { opt.scrollIntoView({ block: "nearest" }); opt.click(); }
        else { warnings.push("liste déroulante : choisis « " + action.value + " »"); }
      }
    }
    mark(el.type === "file" ? el.parentElement || el : el);
    return true;
  }

  let bannerEl = null;
  function banner(html, color) {
    if (!isTop) return;
    if (bannerEl) bannerEl.remove();
    bannerEl = document.createElement("div");
    bannerEl.style.cssText = "position:fixed;top:0;left:0;right:0;z-index:2147483647;padding:10px 16px;font:14px Segoe UI,Arial;" +
      "color:#111;background:" + color + ";box-shadow:0 2px 6px rgba(0,0,0,.2)";
    bannerEl.innerHTML = html + ' <a href="#" style="float:right;color:#111">fermer</a>';
    bannerEl.querySelector("a").onclick = (e) => { e.preventDefault(); bannerEl.remove(); };
    document.body.appendChild(bannerEl);
  }

  let running = false;
  async function run(jobId) {
    if (running) return;                          // one fill at a time per frame
    running = true;
    try { await runOnce(jobId); } finally { running = false; }
  }

  async function runOnce(jobId) {
    const base = { job_id: jobId, url: location.href.split("#")[0], title: document.title, company: "", ext_version: VERSION };
    if (isTop && PLATFORMS.test(location.hostname)) {
      // Nothing is automated on job boards; Fitly only READS the page the user opened: an offer the site
      // says is gone (APEC, Figaro... relayed by Google Jobs) leaves the list.
      for (let i = 0; i < 6; i++) {
        if (EXPIRED.test((document.body.innerText || "").slice(0, 20000))) {
          await send({ type: "report", final: true, payload: { ...base, status: "closed", filled: 0,
            reason: "le site d'emploi indique que l'offre n'est plus disponible (retirée de la liste)" } });
          banner("🧭 <b>Fitly</b> : cette offre n'est plus disponible. Elle est retirée de ta liste.", "#fee2e2");
          return;
        }
        await sleep(700);
      }
      banner("🧭 <b>Fitly</b> : page d'un site d'emploi — Fitly n'y automatise rien. Clique sur <b>Postuler</b> : " +
             "le formulaire du site de l'entreprise sera pré-rempli automatiquement.", "#e0f2fe");
      await send({ type: "report", final: false, payload: { ...base, status: "platform", filled: 0,
        reason: "page d'un site d'emploi : clique sur Postuler pour aller sur le site de l'entreprise" } });
      return;
    }
    if (!(await captchaGate(base))) return;
    if (isTop) await declineCookiesSoon();         // cookie banner: refused before filling
    const warnings = [];
    let fields = await waitForFields(isTop ? 6000 : 4000, base);
    if (known(fields) < 2 && isTop && await openForm()) {
      warnings.push("formulaire ouvert via « Postuler / Je suis intéressé(e) »");
      fields = await waitForFields(15000, base);  // single-page apps render the form late
      declineCookies();
    }
    if (captchaPresent()) return;                 // still unsolved after 3 min: already reported
    if (!isTop && known(fields) < 2) return;      // embedded frame without an application form (newsletter, ad...)
    if (known(fields) === 0 && isTop && EXPIRED.test((document.body.innerText || "").slice(0, 20000))) {
      await send({ type: "report", final: true, payload: { ...base, status: "closed", filled: 0,
        reason: "l'offre n'est plus disponible (retirée de la liste)" } });
      banner("🧭 <b>Fitly</b> : cette offre n'est plus disponible. Elle est retirée de ta liste.", "#fee2e2");
      return;
    }
    if (known(fields) === 0) {
      if (!isTop) return;                         // forms often live in only one of the page's frames
      for (let i = 0; i < 8; i++) {               // forms often live in an embedded frame: give it time
        await sleep(1000);
        const state = await send({ type: "status" });
        if (state && state.filled > 0) return;   // a frame of this tab filled the form
      }
      if (loginWall()) {
        await send({ type: "report", final: false, payload: { ...base, status: "login", filled: 0, warnings,
          reason: "connexion / création de compte demandée par le site : connecte-toi, le formulaire sera pré-rempli ensuite" } });
        banner("🧭 <b>Fitly</b> : ce site demande de te connecter (ou de créer un compte). Connecte-toi : " +
               "le formulaire qui suit sera pré-rempli.", "#e0f2fe");
        return;
      }
      await send({ type: "report", final: false, payload: { ...base, status: "no_form", filled: 0, warnings,
        reason: "aucun formulaire reconnu ici (connexion au site requise ? formulaire sur une autre page ?)" } });
      banner("🧭 <b>Fitly</b> : aucun formulaire reconnu sur cette page. Ouvre le formulaire de candidature : il sera pré-rempli.", "#fef3c7");
      return;
    }
    const plan = await send({ type: "plan", payload: { job_id: jobId, url: location.href,
      text: (document.body.innerText || "").slice(0, 20000), fields } });
    if (!plan || !plan.ok) { banner("🧭 <b>Fitly</b> : pas de réponse de Fitly. L'interface est-elle ouverte ?", "#fee2e2"); return; }
    if (plan.status === "stopped") {
      await send({ type: "report", final: false, payload: { ...base, status: "stopped", filled: 0, reason: plan.reason } });
      banner("🧭 <b>Fitly</b> : arrêt (" + plan.reason + "). Rien n'a été rempli.", "#fee2e2");
      return;
    }
    if (dryRun) {                                 // audit: what WOULD be filled; nothing is typed
      await send({ type: "report", final: true, payload: { ...base, status: "audit", filled: 0, warnings,
        would_fill: plan.actions.length, fields: fields.length, remaining_required: plan.remaining_required,
        your_decision: plan.your_decision, google_types: fields.filter((f) => f.prediction && f.prediction !== "UNKNOWN_TYPE").length } });
      return;
    }
    let filled = 0;
    for (const a of plan.actions) if (await apply(a, jobId, warnings)) filled++;
    await send({ type: "report", final: true, payload: { ...base, status: "prefilled", filled, warnings,
      remaining_required: plan.remaining_required, your_decision: plan.your_decision } });
    const todo = (plan.remaining_required || []).map((l) => l.split("\n")[0]).slice(0, 6);
    banner("🧭 <b>Fitly</b> a pré-rempli <b>" + filled + "</b> champ(s) (en vert)." +
           (todo.length ? " À compléter : " + todo.join(" · ") + "." : "") +
           " Champ vide restant : clique dedans et choisis ton profil <b>Chrome</b> (remplissage auto Google)." +
           " <b>Vérifie, puis clique toi-même sur Envoyer.</b>", "#dcfce7");
    guide(plan.todo_selectors);                  // scroll to each field left for the user (orange)
  }

  let dryRun = false;
  // Relay page opened by Fitly (http://127.0.0.1:8765/go?job=N): mark the tab, then open the offer.
  if (location.host === "127.0.0.1:8765" && location.pathname === "/go") {
    send({ type: "hello", url: location.href }).then(() => {
      const target = document.body && document.body.dataset.fitlyTarget;
      if (target) location.replace(target);
    });
    return;
  }
  // One-click fill (Alt+Shift+P / popup button), in every frame of the tab, at any step of a form.
  if (window.chrome && chrome.runtime && chrome.runtime.onMessage) {
    chrome.runtime.onMessage.addListener((msg) => {
      if (msg && msg.type === "manual_fill") { dryRun = false; run(msg.jobId); }
    });
  }
  // One click on the page itself: a small "✍️ Pré-remplir" button, bottom-left, on pages that hold an
  // application form (or a "Postuler" button). Never on job boards; never a submit button; can be closed.
  function floatingButton() {
    if (!isTop || PLATFORMS.test(location.hostname) || document.getElementById("fitly-fill-btn")) return;
    let tries = 0;
    const check = () => {
      if (document.getElementById("fitly-fill-btn") || ++tries > 30) return;
      const fields = deepAll("input, textarea, select").map((el) => ({ label: labelOf(el).label,   // count only
        type: (el.getAttribute("type") || el.tagName).toLowerCase(), prediction: el.getAttribute("autofill-prediction") || "",
        autocomplete: el.getAttribute("autocomplete") || "" }));
      const opener = deepAll("a, button, [role=button]").some((el) => {
        const t = fold(el.innerText || el.textContent);
        return t && t.length <= 50 && OPENER.test(t);
      });
      if (known(fields) < 2 && !opener && !document.querySelector("iframe[src*='greenhouse'], iframe[src*='lever'], " +
          "iframe[src*='workday'], iframe[src*='smartrecruiters'], iframe[src*='taleez'], iframe[src*='teamtailor']")) {
        setTimeout(check, 2000);                 // forms are often drawn late
        return;
      }
      const box = document.createElement("div");
      box.id = "fitly-fill-btn";
      box.style.cssText = "position:fixed;left:16px;bottom:16px;z-index:2147483647;display:flex;gap:4px;" +
        "font:600 14px Segoe UI,Arial";
      const btn = document.createElement("button");
      btn.type = "button";                       // never submits anything
      btn.textContent = "✍️ Pré-remplir";
      btn.title = "Fitly : remplir ce formulaire avec tes infos et ton CV (Alt+Maj+P). Rien n'est envoyé.";
      btn.style.cssText = "padding:10px 14px;border:0;border-radius:22px;background:#16a34a;color:#fff;cursor:pointer;" +
        "box-shadow:0 4px 12px rgba(0,0,0,.25);font:inherit";
      const close = document.createElement("button");
      close.type = "button";
      close.textContent = "×";
      close.title = "Masquer";
      close.style.cssText = "border:0;border-radius:50%;width:26px;height:26px;align-self:center;background:#e5e7eb;cursor:pointer";
      btn.onclick = (e) => { e.preventDefault(); e.stopPropagation(); btn.textContent = "⏳ Remplissage…";
                             send({ type: "manual_fill_self" }).then(() => setTimeout(() => { btn.textContent = "✍️ Pré-remplir"; }, 4000)); };
      close.onclick = (e) => { e.preventDefault(); e.stopPropagation(); box.remove(); };
      box.append(btn, close);
      document.body.appendChild(box);
    };
    setTimeout(check, 1500);
  }
  if (!dryRun) floatingButton();

  // Background tabs are throttled and many sites only draw their form when shown: start once the tab is
  // visible (the extension shows each Fitly tab in turn, see "préparation" in background.js).
  const whenVisible = () => document.visibilityState === "visible" ? Promise.resolve() : new Promise((resolve) => {
    const onChange = () => {
      if (document.visibilityState === "visible") { document.removeEventListener("visibilitychange", onChange); resolve(); }
    };
    document.addEventListener("visibilitychange", onChange);
  });
  send({ type: "hello", url: location.href }).then(async (r) => {
    if (r && r.fill) { dryRun = !!r.dry; await whenVisible(); run(r.jobId); }
  });
})();
