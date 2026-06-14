from services import content_farm_service as cf


def test_evaluate_articles_trivia_or_thin():
    arts = [
        {"url": "a", "is_trivia": True, "word_count": 900},   # trivia
        {"url": "b", "is_trivia": False, "word_count": 120},  # thin
        {"url": "c", "is_trivia": False, "word_count": 800},  # ok
    ]
    out = cf.evaluate_articles(arts, thin_words=250)
    assert out["judged"] == 3
    assert abs(out["trash_share"] - 2 / 3) < 1e-6
    assert "a" in out["trash_examples"] and "b" in out["trash_examples"]


def test_evaluate_articles_empty():
    assert cf.evaluate_articles([], thin_words=250) == {
        "trash_share": 0.0, "trash_examples": [], "judged": 0,
    }


def test_should_escalate_each_trigger():
    kw = dict(trash_threshold=0.4, link_threshold=30, footprint_threshold=5000)
    assert cf.should_escalate(0.5, 0, 0, **kw) is True
    assert cf.should_escalate(0.0, 40, 0, **kw) is True
    assert cf.should_escalate(0.0, 0, 9000, **kw) is True
    assert cf.should_escalate(0.1, 5, 100, **kw) is False


def test_compute_signals_high_when_farmy():
    out = cf.compute_signals(
        trivia_share=0.8, trash_share=0.75, judged_articles=8,
        article_link_count=40, keyword_footprint=9000, semrush_checked=True,
    )
    assert out["band"] == "HIGH"
    assert out["score"] >= 55
    assert out["signals"]["semrush_checked"] is True


def test_compute_signals_low_when_clean():
    out = cf.compute_signals(
        trivia_share=None, trash_share=0.0, judged_articles=6,
        article_link_count=4, keyword_footprint=100, semrush_checked=False,
    )
    assert out["band"] == "LOW"
    assert out["score"] == 0
