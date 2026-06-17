from services import recommendation_service as rec


def test_confirmed_pg_domains_dedup():
    hp = [{"found_href": "https://bodog.eu/poker"}]
    deep = [{"bad_links": [{"found_href": "https://www.bodog.eu/casino"}, {"found_href": "gamblizard.com"}]}]
    assert rec.confirmed_pg_domains(hp, deep) == ["bodog.eu", "gamblizard.com"]


def test_confirmed_pg_domains_empty():
    assert rec.confirmed_pg_domains([], []) == []


def test_decide_skip_manual_continue():
    assert rec.decide_porn_gamble(["a.com", "b.com", "c.com"], 3)[0] == "SKIP"
    d, reason, flag = rec.decide_porn_gamble(["a.com", "b.com"], 3)
    assert d == "CHECK_MANUALLY" and "a.com" in flag and "2" in reason
    assert rec.decide_porn_gamble([], 3) == (None, "", None)
