"""Read and fill a form through Playwright. Playwright is not imported at module load time.

The adapter inspects a page and types validated answers; it never clicks the final submit button.
"""

from __future__ import annotations

from pathlib import Path

# Technical or action inputs that are never form questions.
SKIP_TYPES = {"hidden", "submit", "button", "reset", "image"}

DESCRIBE = """el => {
  const cssPath = (node) => {
    if (node.id) return '#' + CSS.escape(node.id);
    const parts = [];
    let cur = node;
    while (cur && cur.nodeType === 1 && cur.tagName.toLowerCase() !== 'html') {
      if (cur.id) { parts.unshift('#' + CSS.escape(cur.id)); break; }
      let index = 1;
      for (let prev = cur.previousElementSibling; prev; prev = prev.previousElementSibling) {
        if (prev.tagName === cur.tagName) index++;
      }
      parts.unshift(cur.tagName.toLowerCase() + ':nth-of-type(' + index + ')');
      cur = cur.parentElement;
    }
    return parts.join(' > ');
  };
  let labelText = (el.labels && el.labels.length) ? Array.from(el.labels).map(l => l.innerText).join(' ') : '';
  const ownText = labelText.trim();          // a radio/checkbox's own option text ("Oui", "Non")
  const weak = (t) => !t || t.trim().length < 2 || /^(attach|upload|type your response|start typing|select)/i.test(t.trim());
  if (el.type === 'radio' || weak(labelText || el.getAttribute('aria-label') || el.getAttribute('placeholder'))) {
    // Walk up to the field group and use its caption (Greenhouse 'Resume/CV' > 'Attach', Lever cards...).
    let cur = el.parentElement;
    for (let i = 0; i < 5 && cur; i++, cur = cur.parentElement) {
      const cap = cur.querySelector('legend, label, [class*="label"], [class*="question"], h3, h4');
      if (cap && cap.innerText && !weak(cap.innerText)) { labelText = cap.innerText.split(String.fromCharCode(10))[0]; break; }
    }
  }
  const style = window.getComputedStyle(el);
  const rect = el.getBoundingClientRect();
  return {
    label: (labelText || el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('name') || '').trim(),
    option: (el.type === 'radio' || el.type === 'checkbox') ? (ownText || el.value || '') : '',
    name: el.getAttribute('name') || '',
    type: (el.getAttribute('type') || el.tagName).toLowerCase(),
    tag: el.tagName.toLowerCase(),
    required: el.required || el.getAttribute('aria-required') === 'true',
    // File inputs are often visually hidden behind a styled button: keep them.
    visible: el.type === 'file' || (style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0),
    selector: cssPath(el),
  };
}"""


def inspect_page(page):
    text = page.locator("body").inner_text()
    fields, failures, last_error = [], 0, None
    elements = page.locator("input, textarea, select").all()
    for el in elements:
        try:
            info = el.evaluate(DESCRIBE)
        except Exception as exc:
            failures, last_error = failures + 1, exc
            continue
        if info["type"] in SKIP_TYPES or not info["visible"]:
            continue
        fields.append({k: info.get(k, "") for k in ("label", "name", "type", "required", "selector", "option")})
    if elements and failures == len(elements):
        raise RuntimeError(f"Field inspection failed on every element: {last_error}")
    return {"text": text, "fields": fields}


def fill_validated(page, fill_plan):
    """Fill only validated values. Never ticks a checkbox, never clicks submit."""
    filled, skipped = 0, []
    for item in fill_plan.get("plan", []):
        field = item["field"]
        selector = field.get("selector")
        if not selector:
            continue
        kind = field.get("type", "")
        loc = page.locator(selector)
        try:
            if kind == "file":
                path = Path(str(item["value"]))
                if not path.is_file():
                    skipped.append({"field": field.get("label"), "reason": f"file not found: {path}"})
                    continue
                loc.set_input_files(str(path), timeout=5000)
            elif kind in ("radio", "checkbox"):
                # radio: option matching the validated answer; checkbox: required acknowledgements only
                if kind == "checkbox" and item.get("key") != "consent":
                    skipped.append({"field": field.get("label"), "reason": "choice left to the human"})
                    continue
                try:
                    loc.check(timeout=3000)
                except Exception:  # covered by an overlay (cookie banner...): plain DOM click
                    loc.evaluate("e => { if (!e.checked) e.click(); }")
            elif kind == "select":
                try:
                    loc.select_option(label=str(item["value"]), timeout=3000)
                except Exception:
                    loc.select_option(value=str(item["value"]), timeout=3000)
            else:
                loc.fill(str(item["value"]), timeout=5000)
        except Exception as exc:  # one stubborn field never stops the others
            skipped.append({"field": field.get("label"), "reason": str(exc).splitlines()[0][:120]})
            continue
        filled += 1
    return {"filled": filled, "skipped": skipped, "submit_clicked": False}
