"""Synthetic demo data so the dashboard has something to show before you have history.

Every firm here is fictional, and the outcomes are simulated from made up rates.
The demo database lives in data/demo.db, separate from your real one.
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np

from . import db
from .models import Posting, utcnow
from .scoring import score_posting

FIRMS = [
    ("Northwind Capital", "quant_research"), ("Halcyon Markets", "quant_trading"),
    ("Basalt Systematic", "quant_research"), ("Larkspur Trading", "quant_trading"),
    ("Meridian Partners", "investment_banking"), ("Copperline Advisors", "investment_banking"),
    ("Granite Peak Equity", "private_equity"), ("Tidewater Securities", "markets"),
    ("Vellum Labs", "swe"), ("Quarry Analytics", "data_science"), ("Ostrich Quant", "quant_dev"),
]
TITLES = {
    "quant_research": "Quantitative Research Intern, Summer 2027",
    "quant_trading": "Trading Intern, Summer 2027",
    "quant_dev": "Quantitative Developer Intern, Summer 2027",
    "investment_banking": "Investment Banking Summer Analyst 2027",
    "private_equity": "Private Equity Summer Analyst 2027",
    "markets": "Global Markets Summer Analyst 2027",
    "swe": "Software Engineering Intern, Summer 2027",
    "data_science": "Data Science Intern, Summer 2027",
}
DESCRIPTIONS = {
    "quant_research": "Use statistics, probability, and Python to research signals. Machine learning and time series experience preferred.",
    "quant_trading": "Make markets in options. Strong probability, mental math, and decision making under uncertainty.",
    "quant_dev": "Build low latency trading systems in C++ and Python alongside researchers.",
    "investment_banking": "Support M&A and capital markets transactions. Financial modeling, valuation, DCF, and pitch book work.",
    "private_equity": "Evaluate buyout opportunities, build LBO models, and support portfolio companies.",
    "markets": "Rotate across sales and trading desks in rates, credit, and commodities.",
    "swe": "Ship backend services in Python and Go. Data structures and algorithms fundamentals.",
    "data_science": "Build analytics and machine learning models on product data.",
}
# Simulated "true" probabilities of advancing past the resume screen.
BASE = {"quant_research": 0.10, "quant_trading": 0.14, "quant_dev": 0.16, "investment_banking": 0.12,
        "private_equity": 0.08, "markets": 0.15, "swe": 0.20, "data_science": 0.18}
QUANTY = {"quant_research", "quant_trading", "quant_dev", "swe", "data_science"}


def seed(path, n_apps: int = 140, seed_value: int = 11) -> None:
    rng = np.random.default_rng(seed_value)
    now = utcnow()
    cfg = {"candidate": {"graduation_year": 2028}, "family_weights": {f: 0.9 for f in BASE}}
    with db.connect(path) as conn:
        for t in ("events", "applications", "contacts", "postings"):
            conn.execute(f"DELETE FROM {t}")

        contact_ids = {}
        for i, (firm, _) in enumerate(FIRMS):
            if rng.random() < 0.55:
                status = rng.choice(["reached_out", "replied", "chatted", "referred"], p=[0.35, 0.25, 0.2, 0.2])
                cid = db.add_contact(
                    conn, name=f"Contact {i + 1}", firm=firm, role="Analyst",
                    relationship=str(rng.choice(["alum", "club", "recruiter"])), status=str(status),
                    last_contact=(now - timedelta(days=int(rng.integers(1, 30)))).isoformat(),
                )
                if status == "referred":
                    contact_ids[firm] = cid

        for i in range(n_apps):
            firm, family = FIRMS[rng.integers(len(FIRMS))]
            posted = now - timedelta(days=float(rng.uniform(5, 120)))
            lag = float(rng.choice([rng.uniform(0, 3), rng.uniform(3, 7), rng.uniform(7, 14), rng.uniform(14, 40)],
                                   p=[0.35, 0.3, 0.2, 0.15]))
            applied = min(posted + timedelta(days=lag), now - timedelta(days=1))
            p = Posting(firm=firm, ats="demo", external_id=str(i), title=TITLES[family],
                        location="New York, NY", description=DESCRIPTIONS[family], published_at=posted)
            fam, score, detail = score_posting(p, cfg)
            db.upsert_posting(conn, p, fam, score, detail)

            routed = "CSQNT" if family in QUANTY else "FIN"
            used = routed if rng.random() < 0.8 else ("FIN" if routed == "CSQNT" else "CSQNT")
            referred = firm in contact_ids and rng.random() < 0.6

            prob = BASE[family]
            prob *= 1.5 if used == routed else 0.7
            prob *= 2.2 if referred else 1.0
            prob *= 1.4 if lag <= 3 else (1.0 if lag <= 7 else 0.6)
            prob = min(prob, 0.9)

            app_id = db.create_application(
                conn, posting_uid=p.uid, firm=firm, title=p.title, family=family,
                resume_version=used, routed_version=routed, route_confidence=0.8,
                referral_contact_id=contact_ids[firm] if referred else None,
                applied_at=applied.isoformat(), days_after_posting=round(lag, 1),
            )
            age = (now - applied).days
            t = applied
            if rng.random() < prob:
                for stage, p_next in (("oa", 1.0), ("interview", 0.5), ("final", 0.45), ("offer", 0.4)):
                    if rng.random() > p_next:
                        if rng.random() < 0.7:
                            t += timedelta(days=float(rng.uniform(3, 14)))
                            if t < now:
                                db.add_event(conn, app_id, "rejected", t.isoformat())
                        break
                    t += timedelta(days=float(rng.uniform(4, 18)))
                    if t >= now:
                        break
                    db.add_event(conn, app_id, stage, t.isoformat())
            elif rng.random() < 0.55 and age > 5:
                t += timedelta(days=float(rng.uniform(5, min(age, 45))))
                db.add_event(conn, app_id, "rejected", t.isoformat())

        # A few open postings so the queue has something in it.
        for j, (firm, family) in enumerate(FIRMS):
            p = Posting(firm=firm, ats="demo", external_id=f"open{j}", title=TITLES[family],
                        location="Chicago, IL", description=DESCRIPTIONS[family],
                        published_at=now - timedelta(days=float(rng.uniform(0, 20))))
            fam, score, detail = score_posting(p, cfg)
            db.upsert_posting(conn, p, fam, score, detail)
