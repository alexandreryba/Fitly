from fitly.apply.browser_session import BrowserApplicationSession


def test_wrapper_has_no_submit_method():
    assert not hasattr(BrowserApplicationSession, "submit")
    assert not hasattr(BrowserApplicationSession, "click_submit")
