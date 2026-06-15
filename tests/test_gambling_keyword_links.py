from services import link_checker_service as lc


def test_flags_gambling_anchor_to_unknown_external():
    html = """
    <a href="https://example.com/page">internal casino</a>
    <a href="https://bxk-media.io/x">play casino now</a>
    <a href="https://facebook.com/x">casino fanpage</a>
    <a href="https://news.com/economy">economy report</a>
    """
    out = lc.gambling_keyword_external_links(
        html, ["casino", "porn"], source_domain="example.com",
        legit_domains=["facebook.com"],
    )
    assert "https://bxk-media.io/x" in out      # gambling anchor → unknown external
    assert not any("example.com" in h for h in out)   # internal excluded
    assert not any("facebook.com" in h for h in out)  # allowlisted excluded
    assert not any("news.com" in h for h in out)      # no gambling keyword


def test_empty_inputs():
    assert lc.gambling_keyword_external_links("", ["casino"], "x", []) == []
    assert lc.gambling_keyword_external_links("<a href='/x'>hi</a>", [], "x", []) == []
