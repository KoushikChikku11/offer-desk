"""Funnel analytics with honest uncertainty.

With tens of applications instead of thousands, a raw conversion rate is mostly noise.
Everything here reports an interval or a probability alongside the point estimate:

  Wilson score intervals for every rate (behaves well near 0 and with small n)
  Fisher's exact test and a Beta Binomial posterior for A vs B comparisons
  Kaplan Meier curves for time to first response, treating "no reply yet" as
  right censored rather than pretending those applications were rejections
"""

from __future__ import annotations

import math
from datetime import datetime

import numpy as np
import pandas as pd

from .models import STAGES, TERMINAL, parse_ts, utcnow

STAGE_RANK = {s: i for i, s in enumerate(STAGES)}
METRICS = {
    "advanced": "reached OA or later",
    "interviewed": "reached an interview",
    "offered": "received an offer",
    "replied": "heard anything back",
}
AGE_BUCKETS = [(-1, 3, "0 to 3 days"), (3, 7, "4 to 7 days"), (7, 14, "8 to 14 days"), (14, 10_000, "15+ days")]


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def _bucket(days) -> str:
    if days is None or (isinstance(days, float) and math.isnan(days)):
        return "unknown"
    for lo, hi, label in AGE_BUCKETS:
        if lo < days <= hi:
            return label
    return "unknown"


def build_outcomes(apps: pd.DataFrame, events: pd.DataFrame, ghost_after_days: int = 30,
                   now: datetime | None = None) -> pd.DataFrame:
    """One row per application with the furthest stage reached and response timing."""
    now = now or utcnow()
    rows = []
    for _, a in apps.iterrows():
        applied = parse_ts(a["applied_at"])
        ev = events[events["application_id"] == a["id"]].copy()
        ev["ts"] = ev["at"].map(parse_ts)
        ev = ev.sort_values("ts")
        later = ev[ev["stage"] != "applied"]

        furthest = max((STAGE_RANK[s] for s in ev["stage"] if s in STAGE_RANK), default=0)
        terminal = next((s for s in reversed(list(later["stage"])) if s in TERMINAL), None)
        age_days = (now - applied).total_seconds() / 86400

        if len(later):
            first = (later["ts"].iloc[0] - applied).total_seconds() / 86400
            responded, duration = True, max(first, 0.0)
        else:
            responded, duration = False, age_days

        if terminal:
            status = terminal
        elif not len(later) and age_days >= ghost_after_days:
            status = "ghosted"
        else:
            status = STAGES[furthest]

        rows.append({
            "id": a["id"],
            "firm": a["firm"],
            "title": a["title"],
            "family": a.get("family") or "other",
            "resume_version": a["resume_version"],
            "routed_version": a.get("routed_version"),
            "referred":bool(a.get("referral_contact_id")) and not pd.isna(a.get("referral_contact_id")),
            "days_after_posting": a.get("days_after_posting"),
            "apply_lag": _bucket(a.get("days_after_posting")),
            "applied_at": applied,
            "age_days": round(age_days, 1),
            "furthest_stage": STAGES[furthest],
            "status": status,
            "replied": responded,
            "advanced": furthest >= STAGE_RANK["oa"],
            "interviewed": furthest >= STAGE_RANK["interview"],
            "offered": furthest >= STAGE_RANK["offer"],
            "response_days": round(duration, 2),
        })
    return pd.DataFrame(rows)


def funnel(df: pd.DataFrame) -> pd.DataFrame:
    n = len(df)
    out = []
    for stage in STAGES:
        k = int((df["furthest_stage"].map(STAGE_RANK) >= STAGE_RANK[stage]).sum()) if n else 0
        lo, hi = wilson(k, n)
        out.append({"stage": stage, "count": k, "rate": k / n if n else 0.0, "lo": lo, "hi": hi})
    return pd.DataFrame(out)


def rate_table(df: pd.DataFrame, by: str, metric: str = "advanced") -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=[by, "n", "k", "rate", "lo", "hi"])
    g = df.groupby(by)[metric].agg(["count", "sum"]).reset_index()
    g.columns = [by, "n", "k"]
    g["k"] = g["k"].astype(int)
    g["rate"] = g["k"] / g["n"]
    bounds = g.apply(lambda r: wilson(int(r["k"]), int(r["n"])), axis=1)
    g["lo"] = [b[0] for b in bounds]
    g["hi"] = [b[1] for b in bounds]
    return g.sort_values("rate", ascending=False).reset_index(drop=True)


def compare(df: pd.DataFrame, column: str, a, b, metric: str = "advanced",
            draws: int = 100_000, seed: int = 7) -> dict:
    """A vs B on a binary outcome. Frequentist and Bayesian answers side by side."""
    from scipy.stats import fisher_exact

    ga, gb = df[df[column] == a][metric], df[df[column] == b][metric]
    ka, na, kb, nb = int(ga.sum()), len(ga), int(gb.sum()), len(gb)
    if na == 0 or nb == 0:
        return {"a": a, "b": b, "na": na, "nb": nb, "note": "one group is empty"}

    _, p_value = fisher_exact([[ka, na - ka], [kb, nb - kb]])
    rng = np.random.default_rng(seed)
    # Uniform Beta(1, 1) prior on each rate.
    pa = rng.beta(1 + ka, 1 + na - ka, draws)
    pb = rng.beta(1 + kb, 1 + nb - kb, draws)
    diff = pa - pb
    return {
        "a": a, "b": b, "metric": metric,
        "ka": ka, "na": na, "kb": kb, "nb": nb,
        "rate_a": ka / na, "rate_b": kb / nb,
        "fisher_p": float(p_value),
        "p_a_better": float((pa > pb).mean()),
        "diff_mean": float(diff.mean()),
        "diff_ci": (float(np.quantile(diff, 0.025)), float(np.quantile(diff, 0.975))),
    }


def describe_comparison(c: dict) -> str:
    if "note" in c:
        return f"{c['a']} vs {c['b']}: {c['note']}"
    lo, hi = c["diff_ci"]
    verdict = "not distinguishable yet" if lo < 0 < hi else (f"{c['a']} ahead" if lo > 0 else f"{c['b']} ahead")
    return (
        f"{c['a']}: {c['ka']}/{c['na']} ({c['rate_a']:.0%})  vs  {c['b']}: {c['kb']}/{c['nb']} ({c['rate_b']:.0%})\n"
        f"  P({c['a']} better) = {c['p_a_better']:.0%}   difference 95% CrI [{lo:+.0%}, {hi:+.0%}]   "
        f"Fisher p = {c['fisher_p']:.3f}   -> {verdict}"
    )


def kaplan_meier(durations, observed) -> pd.DataFrame:
    """Survival curve S(t) = P(no reply by day t). Censored rows still count as at risk."""
    d = np.asarray(durations, dtype=float)
    e = np.asarray(observed, dtype=bool)
    if len(d) == 0:
        return pd.DataFrame({"t": [0.0], "survival": [1.0], "at_risk": [0], "events": [0]})
    times = np.unique(d[e])
    surv, rows = 1.0, [{"t": 0.0, "survival": 1.0, "at_risk": len(d), "events": 0}]
    for t in times:
        at_risk = int((d >= t).sum())
        events = int(((d == t) & e).sum())
        if at_risk:
            surv *= 1 - events / at_risk
        rows.append({"t": float(t), "survival": surv, "at_risk": at_risk, "events": events})
    return pd.DataFrame(rows)


def median_survival(km: pd.DataFrame) -> float | None:
    below = km[km["survival"] <= 0.5]
    return float(below["t"].iloc[0]) if len(below) else None


def router_audit(df: pd.DataFrame, metric: str = "advanced") -> pd.DataFrame:
    """Did overriding the router help or hurt? Only rows where the router made a call."""
    sub = df[df["routed_version"].notna()].copy()
    if sub.empty:
        return pd.DataFrame()
    sub["choice"] = np.where(sub["routed_version"] == sub["resume_version"], "followed router", "overrode router")
    return rate_table(sub, "choice", metric)
