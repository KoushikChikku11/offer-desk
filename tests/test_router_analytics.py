from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from offerdesk import analytics, db, letters
from offerdesk.router import Router

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


# Router

def test_router_rules():
    r = Router()
    quant = r.route("Quant research intern. Python, C++, probability, machine learning.", "quant_research")
    bank = r.route("Investment banking analyst. M&A, valuation, DCF, financial modeling.", "investment_banking")
    assert quant.version == "CSQNT" and quant.confidence > 0.9
    assert bank.version == "FIN" and bank.confidence > 0.9
    assert quant.model == "rules"


def test_router_needs_enough_labels():
    r = Router(min_labels=20)
    assert r.fit(["a"] * 10, ["FIN"] * 10) is False
    assert r.pipeline is None


def test_router_learns_from_choices():
    texts = [f"systematic macro research role {i}" for i in range(15)] + \
            [f"restructuring advisory coverage role {i}" for i in range(15)]
    labels = ["CSQNT"] * 15 + ["FIN"] * 15
    r = Router(min_labels=20)
    assert r.fit(texts, labels)
    out = r.route("systematic macro research role", "other")
    assert out.version == "CSQNT" and "model" in out.model


# Letters

def test_letter_uses_only_profile_text():
    profile = {"name": "Test Person", "school": "Purdue University", "majors": "CS and Finance",
               "graduation": "May 2028", "pitch": {"CSQNT": "I built a pricing engine."},
               "why_family": {"quant_research": "I like testable ideas."}, "closing": "Thanks."}
    text = letters.draft(profile, "Example", "Quant Intern", "quant_research", "CSQNT")
    assert "I built a pricing engine." in text and "Example" in text
    assert letters.word_count(text) < 120


# Analytics

def test_wilson_bounds():
    lo, hi = analytics.wilson(0, 10)
    assert lo == 0 and 0.2 < hi < 0.35
    lo, hi = analytics.wilson(5, 10)
    assert lo < 0.5 < hi
    assert analytics.wilson(0, 0) == (0.0, 1.0)


def test_kaplan_meier_hand_computed():
    # Replies at day 2 and 5, one silent application censored at day 3, one at day 10.
    km = analytics.kaplan_meier([2, 3, 5, 10], [True, False, True, False])
    s = dict(zip(km["t"], km["survival"]))
    assert s[2.0] == pytest.approx(3 / 4)
    assert s[5.0] == pytest.approx(3 / 4 * (1 - 1 / 2))
    assert analytics.median_survival(km) == 5.0


def test_compare_detects_large_effect():
    df = pd.DataFrame({"g": ["a"] * 40 + ["b"] * 40,
                       "advanced": [True] * 20 + [False] * 20 + [True] * 4 + [False] * 36})
    c = analytics.compare(df, "g", "a", "b")
    assert c["p_a_better"] > 0.99 and c["fisher_p"] < 0.01
    assert c["diff_ci"][0] > 0


def test_outcomes_end_to_end(tmp_path):
    path = tmp_path / "t.db"
    with db.connect(path) as conn:
        a1 = db.create_application(conn, firm="F1", title="Quant Intern", family="quant_research",
                                   resume_version="CSQNT", routed_version="CSQNT",
                                   applied_at=(NOW - timedelta(days=20)).isoformat())
        db.add_event(conn, a1, "oa", (NOW - timedelta(days=15)).isoformat())
        db.add_event(conn, a1, "interview", (NOW - timedelta(days=8)).isoformat())
        a2 = db.create_application(conn, firm="F2", title="IB Analyst", family="investment_banking",
                                   resume_version="FIN", routed_version="CSQNT",
                                   applied_at=(NOW - timedelta(days=40)).isoformat())
        db.create_application(conn, firm="F3", title="SWE Intern", family="swe", resume_version="CSQNT",
                              applied_at=(NOW - timedelta(days=3)).isoformat())
        db.add_event(conn, a2, "rejected", (NOW - timedelta(days=30)).isoformat())
        apps, events = db.applications_frame(conn)

    df = analytics.build_outcomes(apps, events, ghost_after_days=30, now=NOW).set_index("firm")
    assert df.loc["F1", "status"] == "interview" and df.loc["F1", "response_days"] == pytest.approx(5)
    assert df.loc["F2", "status"] == "rejected" and not df.loc["F2", "advanced"]
    assert df.loc["F3", "status"] == "applied" and not df.loc["F3", "replied"]

    f = analytics.funnel(df.reset_index()).set_index("stage")
    assert f.loc["applied", "count"] == 3 and f.loc["interview", "count"] == 1

    audit = analytics.router_audit(df.reset_index())
    assert set(audit["choice"]) == {"followed router", "overrode router"}


def test_demo_runs(tmp_path):
    from offerdesk import demo

    path = tmp_path / "demo.db"
    demo.seed(path, n_apps=80)
    with db.connect(path) as conn:
        apps, events = db.applications_frame(conn)
    df = analytics.build_outcomes(apps, events)
    assert len(df) == 80
    assert np.isfinite(analytics.funnel(df)["rate"]).all()
