from services import recommendation_service as rec


def test_porn_gamble_hits_dedup_union():
    out = rec.porn_gamble_hits(
        bad_link_hrefs=["https://a.com", "https://b.com"],
        gambling_keyword_hrefs=["https://b.com", "https://c.com"],
    )
    assert out == ["https://a.com", "https://b.com", "https://c.com"]


def test_collect_flags_all_conditions():
    flags = rec.collect_flags(
        competitor_links=[{"x": 1}], age_days=90, organic_traffic=500,
        pbn_band="MEDIUM", content_farm_band="MEDIUM",
        young_days=180, low_traffic=1000,
    )
    assert any("competitor" in f.lower() for f in flags)
    assert any("new domain" in f.lower() for f in flags)
    assert any("pbn" in f.lower() for f in flags)
    assert any("content-farm" in f.lower() for f in flags)


def test_young_thin_needs_both():
    assert not any("new domain" in f.lower() for f in rec.collect_flags(
        competitor_links=[], age_days=90, organic_traffic=5000,
        pbn_band="LOW", content_farm_band="LOW", young_days=180, low_traffic=1000))
    assert not any("new domain" in f.lower() for f in rec.collect_flags(
        competitor_links=[], age_days=900, organic_traffic=100,
        pbn_band="LOW", content_farm_band="LOW", young_days=180, low_traffic=1000))


def test_decide_after_scores():
    assert rec.decide_after_scores(pbn_band="HIGH", pbn_score=80,
                                   content_farm_band="LOW", content_farm_score=5)[0] == "CHECK_MANUALLY"
    assert rec.decide_after_scores(pbn_band="LOW", pbn_score=5,
                                   content_farm_band="HIGH", content_farm_score=70)[0] == "CHECK_MANUALLY"
    d, r = rec.decide_after_scores(pbn_band="MEDIUM", pbn_score=45,
                                   content_farm_band="LOW", content_farm_score=5)
    assert d == "APPROVED" and r == ""


def test_derive_risk_level():
    assert rec.derive_risk_level("SKIP") == "HIGH"
    assert rec.derive_risk_level("CHECK_MANUALLY") == "MEDIUM"
    assert rec.derive_risk_level("APPROVED") == "LOW"
