from services import outbound_classifier as oc


def test_is_legit_matches_subdomain():
    legit = ["google.com", "facebook.com"]
    assert oc.is_legit_domain("maps.google.com", legit) is True
    assert oc.is_legit_domain("facebook.com", legit) is True
    assert oc.is_legit_domain("notgoogle.com", legit) is False


def test_is_own_entity_subdomain_and_hreflang():
    alts = {"brightdata.es", "brightdata.de"}
    assert oc.is_own_entity("docs.brightdata.com", "brightdata.com", alts) is True
    assert oc.is_own_entity("brightdata.es", "brightdata.com", alts) is True
    assert oc.is_own_entity("randomsite.com", "brightdata.com", alts) is False


def test_classify_buckets():
    html = """
    <head><link rel="alternate" hreflang="es" href="https://acme.es/"></head>
    <body>
      <a href="https://docs.acme.com/x">docs</a>
      <a href="https://acme.es/">spanish</a>
      <a href="https://facebook.com/acme">fb</a>
      <a href="https://weird-blog-network.xyz/">weird</a>
    </body>
    """
    result = oc.classify_outbound(html, "https://acme.com",
                                  legit_domains=["facebook.com"])
    assert "acme.es" in result["own_entity"]
    assert "docs.acme.com" in result["own_entity"]
    assert "facebook.com" in result["legit"]
    assert "weird-blog-network.xyz" in result["candidates"]
