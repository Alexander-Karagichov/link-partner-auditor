from services import scraper_service as ss


def test_exposes_primitives_and_wrappers():
    # The seam must surface both backend primitives and the shared wrappers
    # the audit engine relies on.
    for name in (
        "scrape_page",
        "serp_search",
        "site_search_porn_gambling",
        "site_search_core",
        "reload_serp_terms",
    ):
        assert callable(getattr(ss, name)), name


def test_default_backend_is_brightdata():
    # With SCRAPER_PROVIDER unset, the primitives come from bright_data_service.
    from services import bright_data_service
    assert ss.scrape_page is bright_data_service.scrape_page
    assert ss.serp_search is bright_data_service.serp_search


def test_site_search_filters_to_domain_and_tags_term(monkeypatch):
    def fake_serp(query, num_results=10):
        return (
            [
                {"position": 1, "title": "", "url": "https://example.com/casino-page", "snippet": ""},
                {"position": 2, "title": "", "url": "https://other.com/casino", "snippet": ""},
            ],
            None,
        )

    monkeypatch.setattr(ss, "serp_search", fake_serp)
    results, err = ss._site_search_terms("example.com", ["casino"])

    assert err is None
    assert len(results) == 1  # off-domain result filtered out
    assert results[0]["url"] == "https://example.com/casino-page"
    assert results[0]["matched_term"] == "casino"


def test_site_search_includes_subdomains(monkeypatch):
    def fake_serp(query, num_results=10):
        return ([{"position": 1, "title": "", "url": "https://blog.example.com/x", "snippet": ""}], None)

    monkeypatch.setattr(ss, "serp_search", fake_serp)
    results, _ = ss._site_search_terms("example.com", ["casino"])

    assert len(results) == 1
    assert results[0]["url"] == "https://blog.example.com/x"


def test_site_search_empty_terms_is_noop():
    assert ss._site_search_terms("example.com", []) == ([], None)
