from services import legitimacy_service as ls


def test_detects_contact_signals():
    html = """
    <html><body>
      <a href="mailto:info@acme.com">email</a>
      <a href="tel:+1-202-555-0143">call</a>
      <script type="application/ld+json">
      {"@type":"LocalBusiness","name":"Acme","address":"123 Main St, Boston, MA"}
      </script>
      <footer>123 Main Street, Boston, MA 02101</footer>
    </body></html>
    """
    text = "Acme Ltd. Contact us at 123 Main Street, Boston."
    out = ls.assess(html, text)
    sig = out["signals"]
    assert sig["email"] is True
    assert sig["phone"] is True
    assert sig["schema_org_business"] is True
    assert sig["address"] is True
    assert out["is_legit"] is True
    assert out["score"] >= 2


def test_empty_page_not_legit():
    out = ls.assess("<html><body>buy now</body></html>", "buy now cheap")
    assert out["is_legit"] is False
    assert out["score"] == 0
