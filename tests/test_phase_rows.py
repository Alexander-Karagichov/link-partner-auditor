from services import recommendation_service as rec


def test_all_phases_ran_approved():
    steps = {
        "homepage_gate": {"status": "PASS", "detail": ""},
        "porn_gamble_links": {"status": "PASS", "count": 0, "examples": []},
        "pbn": {"status": "PASS", "band": "MEDIUM", "score": 45},
        "content_farm": {"status": "PASS", "band": "LOW", "score": 10, "semrush_checked": False},
    }
    rows = rec.build_phase_rows(steps, "Marketing SaaS")
    assert [r["name"] for r in rows] == ["Homepage gate", "Niche", "P/G links", "PBN", "Spam / content farm"]
    assert rows[1]["status"] == "INFO" and rows[1]["detail"] == "Marketing SaaS"
    pbn = next(r for r in rows if r["name"] == "PBN")
    assert pbn["status"] == "MEDIUM" and "45" in pbn["detail"]


def test_skip_at_pg():
    steps = {
        "homepage_gate": {"status": "PASS", "detail": ""},
        "porn_gamble_links": {"status": "FAIL", "count": 32, "examples": []},
    }
    rows = rec.build_phase_rows(steps, "Tech news")
    pg = next(r for r in rows if r["name"] == "P/G links")
    assert pg["status"] == "FAIL" and "32" in pg["detail"]
    for name in ("PBN", "Spam / content farm"):
        row = next(r for r in rows if r["name"] == name)
        assert row["status"] == "SKIPPED" and "P/G links" in row["detail"]


def test_skip_at_homepage():
    rows = rec.build_phase_rows({"homepage_gate": {"status": "FAIL", "detail": "links to casino"}}, "")
    assert rows[1]["detail"] == "—"   # empty niche
    for name in ("P/G links", "PBN", "Spam / content farm"):
        row = next(r for r in rows if r["name"] == name)
        assert row["status"] == "SKIPPED" and "homepage" in row["detail"]
