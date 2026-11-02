"""Form analysis, independent of any browser: what may be filled, what is left to the human.

`classify_field` maps a label to a known answer (first name, e-mail, notice period...); `build_fill_plan`
keeps only fields with a validated answer from the user's answer library; `gate_session` stops on CAPTCHA /
anti-bot pages and on unknown required questions. Nothing here ever submits a form.
"""

from __future__ import annotations

import re
from typing import Any

CAPTCHA_PATTERNS = [
    r"captcha",
    r"recaptcha",
    r"hcaptcha",
    r"verify you are human",
    r"i am not a robot",
    r"pas (à|a) un robot",
    r"faites glisser vers la droite",
    r"slide to the right",
]
ANTIBOT_PATTERNS = [r"access denied", r"unusual traffic", r"bot detection", r"automated access"]

FIELD_ALIASES = {
    "first_name": ["first name", "firstname", "prénom", "prenom", "given name"],
    "last_name": ["last name", "lastname", "family name", "surname", "nom de famille", "nom d'usage"],
    "full_name": ["full name", "nom complet"],
    "email": ["email", "e mail", "adresse e mail", "courriel"],
    "phone": ["phone", "telephone", "téléphone", "mobile"],
    "linkedin": ["linkedin"],
    "cv_file": ["resume", "résumé", "curriculum", "cv"],
    "salary_expectation": [
        "salary",
        "salary expectation",
        "prétention salariale",
        "prétentions salariales",
        "pretentions salariales",
        "rémunération",
    ],
    "notice_period": ["notice period", "préavis"],
    "years_experience": ["years of experience", "années d'expérience"],
    "work_authorization": ["work authorization", "right to work", "autorisation de travail"],
    "sponsorship": ["sponsorship", "visa sponsorship"],
    "location": ["city", "ville", "current location", "localisation", "location city"],
    "country": ["country", "pays"],
    "current_company": ["current company", "entreprise actuelle", "current employer", "employeur actuel"],
    "github": ["github"],
    "website": ["website", "site web", "site personnel", "portfolio"],
    "school": ["school", "école", "ecole", "university", "université", "universite"],
    "degree": ["degree", "diplôme", "diplome"],
    "graduation_year": ["graduation year", "année d'obtention", "année de diplôme", "annee de diplome"],
    "english_level": ["english level", "level of english", "niveau d'anglais", "niveau en anglais"],
    "how_heard": [
        "how did you hear",
        "where did you see or hear",
        "how you found us",
        "comment avez-vous connu",
        "comment as-tu connu",
        "source de candidature",
    ],
}
# Fields a human must always handle personally (legal consent, attestations).
HUMAN_ONLY = ["consent", "privacy", "gdpr", "rgpd", "i agree", "j'accepte", "terms", "conditions", "certify", "atteste"]

# Decorations ATS add to labels ("Nom*\nRequis", "Email (required)").
_DECOR = re.compile(r"\b(requis|required|obligatoire|mandatory|optionnel|optional|facultatif)\b\.?", re.I)

# Standard screening questions answered from the user's own validated answers (never invented).
QUESTION_PATTERNS = [
    (
        "salary_expectation",
        r"pr[ée]tentions? salariales?|r[ée]mun[ée]ration (souhait|attendu|envisag|vis)|salaire (souhait|attendu|vis)|"
        r"salary expectations?|expected salary|desired salary|compensation expectations?",
    ),
    (
        "notice_period",
        r"disponib|d[ée]lai.{0,40}(prise de poste|d[ée]marr|commencer|rejoindre|int[ée]grer)|notice period|"
        r"pr[ée]avis|start date|when (can|could) you start|earliest start",
    ),
    ("years_experience", r"ann[ée]es? d.exp[ée]rience|years of (professional |relevant )?experience"),
    ("sponsorship", r"sponsor|visa|d[ée]marches administratives"),
    (
        "work_authorization",
        r"autoris[ée]e? [àa] travailler|autorisation de travail|right to work|authori[sz]ed to work|"
        r"work permit|permis de travail|eligible to work",
    ),
    ("linkedin", r"linkedin"),
    ("github", r"github"),
    ("english_level", r"level of english|english level|niveau d.anglais|niveau en anglais"),
    (
        "how_heard",
        r"hear about|how (did )?you (found|find|hear)|how you found|where did you (see|hear|find)|"
        r"comment (avez.vous|as.tu) (connu|entendu)|o[uù] (avez.vous|as.tu) (vu|trouv)",
    ),
    ("graduation_year", r"(ann[ée]e|year).{0,40}(dipl[oô]m|graduat)|graduat.{0,20}year"),
    ("school", r"(dans )?quelle [ée]cole|which (school|university)|[ée]cole.{0,20}(étudi|etudi)"),
    (
        "relocation",
        r"based in paris|ready to relocate|willing to relocate|pr[eê]te?.{0,12}d[ée]m[ée]nager|"
        r"bas[ée]e? [àa] paris|r[ée]sid.{0,20}paris",
    ),
]
QUESTION_PATTERNS.insert(
    1,
    (
        "notice_period",
        r"able to start|available to start|quand (pourriez|pourrais|peux)|"
        r"date de d[ée]but|start(ing)? date",
    ),
)


def _norm_label(label):
    lines = [line.strip() for line in str(label or "").splitlines() if _DECOR.sub("", line).strip(" .*:")]
    low = (lines[0] if lines else str(label or "")).lower()  # first meaningful line: the field's name
    low = _DECOR.sub(" ", low)
    low = re.sub(r"[_\[\]\-*:]+", " ", low)
    return re.sub(r"\s+", " ", low).strip()


# Legal notices of invisible captchas (Greenhouse, Lever...) are not a challenge shown to the user.
_CAPTCHA_NOTICE = re.compile(
    r"(this (site|page|form) is )?(protected|protégé|protegé|protege)( by| par)( google)? (re|h)?captcha"
    r"|(re|h)?captcha (privacy|terms|enterprise)|google (re|h)?captcha|(re|h)?captcha v3"
)


def detect_blockers(text) -> str | None:
    low = _CAPTCHA_NOTICE.sub(" ", (text or "").lower())
    if any(re.search(p, low) for p in CAPTCHA_PATTERNS):
        return "captcha"
    if any(re.search(p, low) for p in ANTIBOT_PATTERNS):
        return "anti_bot"
    return None


def classify_field(label) -> str | None:
    low = _norm_label(label)
    if any(h in low for h in HUMAN_ONLY):
        return "human_only"
    # A long label is a free question: answer it only when it is a standard screening question
    # (salary, availability, visa...) the user answered in "Mes infos"; never anything else.
    if len(low) > 45 or low.count(" ") > 6:
        for key, pattern in QUESTION_PATTERNS:
            if re.search(pattern, low):
                return key
        return None
    for key, aliases in FIELD_ALIASES.items():
        if any(re.search(rf"(?<![a-z]){re.escape(a)}(?![a-z])", low) for a in aliases):
            if key == "last_name" and "prénom" in low:
                continue
            return key
    for key, pattern in QUESTION_PATTERNS:  # short screening questions ("Compensation Expectations")
        if re.search(pattern, low):
            return key
    # "nom" alone (French last name) only when it is the whole label
    if low in ("nom", "name"):
        return "last_name" if low == "nom" else "full_name"
    return None


def with_derived(answers) -> dict[str, Any]:
    """Fill obvious derived values (full name from first + last) without inventing anything."""
    out = dict(answers or {})
    if not out.get("full_name") and out.get("first_name") and out.get("last_name"):
        out["full_name"] = f"{out['first_name']} {out['last_name']}"
    # Country from a French city given by the user (Paris, Lyon...): only when the user left "Pays" empty.
    if not out.get("country") and re.search(
        r"\b(paris|ile.de.france|île.de.france|lyon|lille|nantes|bordeaux|toulouse|"
        r"marseille|rennes|montpellier|strasbourg|grenoble|nice|france)\b",
        str(out.get("location") or ""),
        re.I,
    ):
        out["country"] = "France"
    return out


# Checkboxes that subscribe to something: never ticked, even if a form marks them as required.
MARKETING = re.compile(
    r"newsletter|marketing|promotion|publicit|offres? (commerciales|promotionnelles|partenaires)|"
    r"partenaires|partners|communications? (commerciales|marketing)|"
    r"futures? opportunit|nouvelles? opportunit|other (job )?opportunit|future (job )?opportunit|"
    r"(contact|recontact)\w* (me|you|moi)? ?(about|au sujet|pour|concernant)|"
    r"talent (pool|community|network)|vivier|career related information|job alerts?|alertes? emploi|"
    r"recevoir (des|nos|les) (actualit|informations|offres|e.?mails|nouvelles)|"
    r"receive (news|updates|emails?|information|offers)|abonne|subscribe|sms|whatsapp",
    re.I,
)
_REQUIRED_MARK = re.compile(r"\b(requis|required|obligatoire|mandatory)\b|\*", re.I)


def consent_checkbox(field) -> str | None:
    """'tick' for a required acknowledgement/consent box, 'never' for subscription boxes, None otherwise."""
    label = str(field.get("label") or "") + " " + str(field.get("option") or "")
    if MARKETING.search(label):
        return "never"
    if field.get("required") or _REQUIRED_MARK.search(label):
        return "tick"
    return None


def _same_answer(option, value):
    a, b = _norm_label(option), _norm_label(value)
    return bool(a) and bool(b) and (a == b or a.startswith(b + " ") or b.startswith(a + " "))


# Google's field classification (Chrome "Show autofill predictions": attribute autofill-prediction) and the
# standard HTML autocomplete tokens. Used for personal fields; screening questions keep our own rules.
GOOGLE_TYPES = {
    "NAME_FIRST": "first_name",
    "NAME_LAST": "last_name",
    "NAME_LAST_FIRST": "last_name",
    "NAME_FULL": "full_name",
    "EMAIL_ADDRESS": "email",
    "PHONE_HOME_WHOLE_NUMBER": "phone",
    "PHONE_HOME_CITY_AND_NUMBER": "phone",
    "PHONE_HOME_NUMBER": "phone",
    "PHONE_HOME_CITY_AND_NUMBER_WITHOUT_TRUNK_PREFIX": "phone",
    "ADDRESS_HOME_CITY": "location",
    "ADDRESS_HOME_COUNTRY": "country",
    "COMPANY_NAME": "current_company",
}
AUTOCOMPLETE_TYPES = {
    "given-name": "first_name",
    "family-name": "last_name",
    "name": "full_name",
    "email": "email",
    "tel": "phone",
    "tel-national": "phone",
    "address-level2": "location",
    "country-name": "country",
    "organization": "current_company",
}
PERSONAL_KEYS = set(GOOGLE_TYPES.values()) | {"linkedin"}


def field_key(field) -> str | None:
    """Our label rules, refined by Google's/the page's own field type for personal data."""
    key = classify_field(field.get("label", ""))
    if key == "human_only" or (key and key not in PERSONAL_KEYS):
        return key  # human-only fields and screening questions: our rules decide
    hint = GOOGLE_TYPES.get(str(field.get("prediction") or "").upper()) or AUTOCOMPLETE_TYPES.get(
        str(field.get("autocomplete") or "").lower().split(" ")[-1]
    )
    return hint or key


_GENERIC_UPLOAD = re.compile(
    r"^$|choose a file|choisir un fichier|drop|deposez|glissez|upload|attach|parcourir|browse|televers|joindre"
)


def build_fill_plan(fields, answers, tick_consents=False) -> dict[str, list[dict[str, Any]]]:
    """plan: validated values to fill; unknown: blocking (required) gaps;
    skipped: optional fields left empty; human: fields the user must handle personally."""
    plan: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    human: list[dict[str, Any]] = []
    radio_groups: dict[str, list[dict[str, Any]]] = {}
    for f in fields:
        if f.get("type") == "radio":
            radio_groups.setdefault(f.get("name") or f.get("label"), []).append(f)
    for group in radio_groups.values():
        # Yes/no style question: tick the option that matches the user's validated answer, else leave it.
        key = classify_field(group[0].get("label", ""))
        value = answers.get(key) if key and key != "human_only" else None
        choice = next((o for o in group if value and _same_answer(o.get("option", ""), str(value))), None)
        if choice:
            plan.append({"field": choice, "key": key, "value": choice.get("option")})
        else:
            human.append({**group[0], "reason": "human_decision"})
    # Upload zones with a generic caption ("Choose a file or drop it here", SmartRecruiters): when no file
    # field is labelled as the CV, the LAST generic one is the resume (earlier ones are "autofill from CV" zones).
    files = [f for f in fields if f.get("type") == "file"]
    cv_slot = None
    if not any(classify_field(f.get("label", "")) == "cv_file" for f in files):
        generic = [
            f
            for f in files
            if classify_field(f.get("label", "")) is None and _GENERIC_UPLOAD.search(_norm_label(f.get("label", "")))
        ]
        cv_slot = generic[-1] if generic else None
    for f in fields:
        if f.get("type") == "radio":
            continue
        # Unknown requiredness is treated as required (conservative).
        required = f.get("required", True)
        key = "cv_file" if f is cv_slot else field_key(f)
        if f.get("type") == "checkbox":
            decision = consent_checkbox(f)
            if tick_consents and decision == "tick":
                plan.append({"field": f, "key": "consent", "value": True})
            elif decision == "never":
                skipped.append({**f, "reason": "marketing_or_newsletter"})
            else:
                human.append({**f, "reason": "human_decision"})
            continue
        if key == "human_only":
            human.append({**f, "reason": "human_decision"})
            continue
        value = answers.get(key) if key else None
        if key is None or value in (None, ""):
            entry = {**f, "reason": "unknown_field" if key is None else "missing_validated_answer", "key": key}
            (unknown if required else skipped).append(entry)
        else:
            plan.append({"field": f, "key": key, "value": value})
    return {"plan": plan, "unknown": unknown, "skipped": skipped, "human": human}


def gate_session(page_text, fields, answers, unknown_required="stop", tick_consents=False) -> dict[str, Any]:
    """unknown_required: 'stop' (fill nothing if a required question is unknown) or
    'leave_to_human' (fill the validated fields, list the required ones the user must answer).
    CAPTCHA / anti-bot always stop. Submission is never allowed."""
    blocker = detect_blockers(page_text)
    if blocker:
        return {"status": "STOP", "reason": blocker, "submit_allowed": False}
    fill = build_fill_plan(fields, with_derived(answers), tick_consents=tick_consents)
    if fill["unknown"] and unknown_required != "leave_to_human":
        return {
            "status": "STOP",
            "reason": "unknown_or_unvalidated_required_field",
            "submit_allowed": False,
            "fill_plan": fill,
        }
    return {
        "status": "READY_FOR_HUMAN_REVIEW",
        "submit_allowed": False,
        "fill_plan": fill,
        "remaining_required": [f.get("label") for f in fill["unknown"]],
    }
