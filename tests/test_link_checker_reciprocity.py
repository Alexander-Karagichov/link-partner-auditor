from services import link_checker_service as lc


def test_extract_all_external_links_includes_footer():
    html = """
    <html><body>
      <main><a href="https://partner-a.com/page">A</a></main>
      <footer><a href="https://partner-b.com">B</a>
              <a href="https://example.com/internal">self</a></footer>
    </body></html>
    """
    links = lc.extract_all_external_links(html, "https://example.com")
    domains = {lc._extract_domain(h) for h in links}
    assert "partner-a.com" in domains
    assert "partner-b.com" in domains   # footer link IS included
    assert "example.com" not in domains  # internal excluded


def test_extract_hreflang_alternates():
    html = """
    <head>
      <link rel="alternate" hreflang="es" href="https://brightdata.es/">
      <link rel="alternate" hreflang="de" href="https://brightdata.de/">
      <link rel="canonical" href="https://brightdata.com/">
    </head>
    """
    alts = lc.extract_hreflang_alternates(html)
    assert alts == {"brightdata.es", "brightdata.de"}


def test_links_back_true_when_partner_links_to_us():
    partner_html = '<footer><a href="https://example.com/">friend</a></footer>'
    assert lc.links_back(partner_html, "https://partner.com", "example.com") is True


def test_links_back_false_when_no_link():
    partner_html = '<a href="https://unrelated.com/">x</a>'
    assert lc.links_back(partner_html, "https://partner.com", "example.com") is False
