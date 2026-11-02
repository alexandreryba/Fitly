"""Application follow-up from e-mails: classification, matching, proposals, application after confirmation."""

from pathlib import Path

from fitly.storage import connect, upsert_jobs
from fitly.tracking import mail_tracker

ROOT = Path(__file__).resolve().parents[1]


def _db(tmp_path):
    db = tmp_path / "m.db"
    upsert_jobs(
        [
            {
                "source": "lever",
                "external_id": str(i),
                "title": t,
                "company": c,
                "location": "Paris",
                "description": "",
                "url": f"https://jobs.lever.co/{c.lower()}/{i}",
                "application_route": "external_ats",
                "score": 80,
            }
            for i, (c, t) in enumerate(
                [
                    ("Vandelay", "Data Engineer"),
                    ("Globex", "Analytics Engineer"),
                    ("Globex", "Data Engineer"),
                    ("Initech", "AI Engineer"),
                ]
            )
        ],
        str(db),
    )
    con = connect(str(db))
    ids = {(r["company"], r["title"]): r["id"] for r in con.execute("SELECT id, company, title FROM jobs")}
    con.close()
    return db, ids


MAILS = [
    {
        "id": "1",
        "date": "2026-10-01T09:00:00",
        "from": "LinkedIn <jobs-noreply@linkedin.com>",
        "subject": "Votre candidature a été envoyée à Vandelay",
        "body": "Data Engineer · Vandelay · Paris",
    },
    {
        "id": "2",
        "date": "2026-10-02T09:00:00",
        "from": "Globex <no-reply@hire.lever.co>",
        "subject": "Thank you for your application",
        "body": "Thanks for applying to the Data Engineer role at Globex.",
    },
    {
        "id": "3",
        "date": "2026-10-09T09:00:00",
        "from": "Globex Recruiting <no-reply@hire.lever.co>",
        "subject": "Your application at Globex",
        "body": "Thank you for applying. Unfortunately we have decided to move "
        "forward with other candidates for the Data Engineer position.",
    },
    {
        "id": "4",
        "date": "2026-10-05T09:00:00",
        "from": "Initech Talent <talent@initech.com>",
        "subject": "Initech - next steps",
        "body": "We would like to schedule an interview for the AI Engineer role. "
        "Please pick a slot: https://calendly.com/initech/30min",
    },
    {
        "id": "5",
        "date": "2026-10-03T09:00:00",
        "from": "Recrutement Hooli <jobs@hooli.com>",
        "subject": "Candidature chez Hooli",
        "body": "Nous avons bien reçu votre candidature pour le poste de Data Analyst.",
    },
    {
        "id": "6",
        "date": "2026-10-04T09:00:00",
        "from": "Newsletter <news@medium.com>",
        "subject": "Top stories for you",
        "body": "Data engineering trends",
    },
]


def test_classification():
    assert [mail_tracker.classify(m) for m in MAILS] == [
        "submitted",
        "submitted",
        "rejected",
        "interview",
        "submitted",
        None,
    ]
    assert (
        mail_tracker.classify(
            {
                "subject": "Merci pour votre candidature",
                "body": "Si votre profil correspond, nous vous proposerons un entretien.",
            }
        )
        == "submitted"
    )


def test_proposals_and_application(tmp_path):
    db, ids = _db(tmp_path)
    props = mail_tracker.analyse(MAILS, str(db))
    by = {(p["company"], p["status"]): p for p in props}
    assert by[("Vandelay", "submitted")]["job_id"] == ids[("Vandelay", "Data Engineer")]
    assert by[("Globex", "rejected")]["job_id"] == ids[("Globex", "Data Engineer")]  # title disambiguates
    assert by[("Initech", "interview")]["job_id"] == ids[("Initech", "AI Engineer")]
    assert by[("Hooli", "submitted")]["job_id"] is None  # applied outside Fitly
    assert not any(p["subject"] == "Top stories for you" for p in props)
    con = connect(str(db))
    assert con.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 0  # nothing applied yet
    con.close()
    assert mail_tracker.apply(props, str(db)) == len(props)
    con = connect(str(db))
    st = {
        r["company"]: r["status"]
        for r in con.execute("SELECT j.company, a.status FROM applications a JOIN jobs j ON j.id=a.job_id")
    }
    sent = con.execute(
        "SELECT applied_at FROM applications WHERE job_id=?", (ids[("Vandelay", "Data Engineer")],)
    ).fetchone()
    con.close()
    assert st == {"Vandelay": "submitted", "Globex": "rejected", "Initech": "interview", "Hooli": "submitted"}
    assert str(sent["applied_at"]).startswith("2026-10-01")  # dated from the e-mail
    assert mail_tracker.analyse(MAILS, str(db)) == []  # idempotent, no downgrade
