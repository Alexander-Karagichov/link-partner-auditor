from services import recommendation_service as rec


def test_same_site():
    assert rec.same_site("https://xavor.com/x", "xavor.com") is True
    assert rec.same_site("https://www.xavor.com/x", "xavor.com") is True
    assert rec.same_site("https://china.xavor.com/x", "xavor.com") is False
    assert rec.same_site("https://notxavor.com/x", "xavor.com") is False


def test_skip_reason_warn_is_manual_review():
    steps = {
        "homepage_gate": {"status": "PASS", "detail": ""},
        "porn_gamble_links": {"status": "WARN", "count": 1, "examples": ["a.com"]},
    }
    rows = rec.build_phase_rows(steps, "Software")
    pbn = next(r for r in rows if r["name"] == "PBN")
    assert "manual review" in pbn["detail"] and "couldn't fetch" not in pbn["detail"]
