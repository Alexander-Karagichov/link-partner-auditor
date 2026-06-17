from services import link_checker_service as lc


def test_is_listed_competitor(monkeypatch):
    monkeypatch.setattr(lc, "_COMPETITOR_DOMAINS", ["rival.com", "foo.io"])
    assert lc.is_listed_competitor("rival.com") is True
    assert lc.is_listed_competitor("www.rival.com") is True
    assert lc.is_listed_competitor("blog.rival.com") is True
    assert lc.is_listed_competitor("notrival.com") is False
    assert lc.is_listed_competitor("") is False
