from services import link_checker_service as lc


def test_extracts_article_links_excludes_nav_footer_and_nonarticles():
    html = """
    <html><body>
      <nav><a href="/about">About</a></nav>
      <main>
        <a href="/how-many-seconds-in-a-day/">art1</a>
        <a href="https://example.com/another-trivia-post/">art2</a>
        <a href="/category/news/">cat</a>
        <a href="/contact">contact</a>
        <a href="https://other.com/external-post/">ext</a>
        <a href="/image.jpg">img</a>
        <a href="/">home</a>
      </main>
      <footer><a href="/footer-article-here/">f</a></footer>
    </body></html>
    """
    links = lc.extract_internal_article_links(html, "https://example.com")
    assert any(l.endswith("/how-many-seconds-in-a-day/") for l in links)
    assert any("another-trivia-post" in l for l in links)
    assert not any("/category/" in l for l in links)
    assert not any("contact" in l for l in links)
    assert not any("other.com" in l for l in links)
    assert not any("image.jpg" in l for l in links)
    assert not any("footer-article" in l for l in links)


def test_returns_empty_on_unparseable():
    assert lc.extract_internal_article_links("", "https://example.com") == []
