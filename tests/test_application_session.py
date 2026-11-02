from fitly.apply.application_session import detect_blockers, gate_session


def test_captcha_stop():
    assert detect_blockers("Please complete reCAPTCHA") == "captcha"
    assert gate_session("captcha", [], {})["submit_allowed"] is False


def test_unknown_stops():
    r = gate_session("normal", [{"label": "First name", "selector": "#a"}], {"first_name": "Jane"})
    assert r["status"] == "READY_FOR_HUMAN_REVIEW"
    r = gate_session("normal", [{"label": "Unknown mandatory question", "selector": "#x"}], {})
    assert r["status"] == "STOP"
