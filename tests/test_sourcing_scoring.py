from conftest import NOW

from offerdesk.models import Posting
from offerdesk.scoring import classify_family, cycle_factor, score_posting


def test_greenhouse_parse(gh_postings):
    p = gh_postings[0]
    assert p.uid == "greenhouse:Example Trading:4907430101"
    assert p.location == "Chicago, United States"
    assert "Python" in p.description and "<" not in p.description
    assert p.raw_meta == {"Worker Sub Type": "Intern", "Job Category": "Trading"}
    assert p.published_at.tzinfo is not None


def test_lever_parse(lever_postings):
    p = lever_postings[0]
    assert p.title.startswith("Investment Banking")
    assert "Financial modeling" in p.description
    assert p.comp_text.startswith("50 to 60 USD")
    assert p.raw_meta["commitment"] == "Intern"


def test_ashby_skips_unlisted(ashby_postings):
    assert [p.external_id for p in ashby_postings] == ["f00"]
    assert ashby_postings[0].comp_text == "$55/hr"


def test_family_classification():
    assert classify_family("Quantitative Research Intern") == "quant_research"
    assert classify_family("Trading Intern, Summer 2027") == "quant_trading"
    assert classify_family("Investment Banking Summer Analyst") == "investment_banking"
    assert classify_family("C++ Software Engineer") == "quant_dev"
    assert classify_family("Software Engineering Intern") == "swe"
    assert classify_family("Office Manager") == "other"


def test_intern_beats_graduate(gh_postings, cfg):
    _, intern_score, _ = score_posting(gh_postings[0], cfg, now=NOW)
    _, grad_score, _ = score_posting(gh_postings[1], cfg, now=NOW)
    assert intern_score > 4 * grad_score


def test_wrong_summer_is_penalized(cfg):
    right = Posting("F", "x", "1", "Quant Research Intern Summer 2027")
    wrong = Posting("F", "x", "2", "Quant Research Intern Summer 2026")
    assert cycle_factor(right, cfg)[0] == 1.0
    assert cycle_factor(wrong, cfg)[0] == 0.35


def test_grad_window_in_description(cfg):
    fits = Posting("F", "x", "1", "Intern", description="Candidates graduating between December 2027 and June 2028.")
    misses = Posting("F", "x", "2", "Intern", description="Must be graduating in 2027.")
    assert cycle_factor(fits, cfg)[0] == 1.0
    assert cycle_factor(misses, cfg)[0] < 1.0


def test_skip_keyword_zeroes_score(cfg):
    p = Posting("F", "x", "1", "Senior Quant Researcher")
    _, score, detail = score_posting(p, cfg, now=NOW)
    assert score == 0 and "senior" in detail["skip"]


def test_freshness_decays(cfg):
    from datetime import timedelta

    fresh = Posting("F", "x", "1", "Quant Research Intern", published_at=NOW - timedelta(days=1))
    stale = Posting("F", "x", "2", "Quant Research Intern", published_at=NOW - timedelta(days=30))
    assert score_posting(fresh, cfg, now=NOW)[1] > score_posting(stale, cfg, now=NOW)[1]
