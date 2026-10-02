"""Score postings against your priorities.

The score is a product of interpretable factors, each in [0, 1], times 100:

    score = 100 * family_weight * firm_tier * seniority * cycle_fit * location_fit * freshness

A product (rather than a weighted sum) means any single dealbreaker, like a PhD only
role or the wrong graduation year, drags the posting to the bottom on its own. Every
factor is stored with the posting so the queue can always explain itself.
"""

from __future__ import annotations

import re
from datetime import datetime

from .models import Posting, utcnow

# Support functions that trading firms hire for. Checked first so "HR Intern" at a
# trading firm is never mistaken for a trading role.
NON_TARGET_RE = re.compile(
    r"\b(hr|human resources|recruit\w*|talent|people (operations|partner)|accounting|accountant|payroll|tax"
    r"|legal|counsel|paralegal|compliance|executive assistant|assistant to|office manager|facilities"
    r"|administrative|marketing|communications|event|wallet operations|operations specialist"
    r"|it support|help ?desk|procurement)\b",
    re.I,
)

# Order matters: first match wins, most specific families first.
FAMILY_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("quant_research", re.compile(r"quant(itative)?\s*(research|researcher|analyst)|alpha research|research (intern|analyst)|machine learning research", re.I)),
    ("quant_trading", re.compile(r"\btrad(er|ing)\b|market mak", re.I)),
    ("quant_dev", re.compile(r"quant(itative)?\s*(\w+\s+)?(developer|dev|engineer)|performance engineer|trading systems|low latency|core developer|c\+\+", re.I)),
    ("investment_banking", re.compile(r"investment bank|\bibd\b|m&a|mergers|advisory|corporate finance|leveraged finance|restructuring", re.I)),
    ("private_equity", re.compile(r"private equity|\bpe\b investment|buyout|growth equity", re.I)),
    ("markets", re.compile(r"sales (and|&) trading|global markets|\bmarkets\b|macro|commodit|structur(ed|ing)|rates|fixed income", re.I)),
    ("data_science", re.compile(r"data scien|machine learning|\bml\b|analytics", re.I)),
    ("swe", re.compile(r"software|developer|\bswe\b|full ?stack|backend|frontend|platform engineer", re.I)),
]

INTERN_RE = re.compile(r"\bintern(ship)?s?\b|summer analyst|co-?op\b", re.I)
FULLTIME_RE = re.compile(r"\bgraduate\b|new grad|full[- ]time|\bexperienced\b|\bassociate\b", re.I)
SUMMER_RE = re.compile(r"summer\s*(20\d\d)", re.I)
OFF_SEASON_RE = re.compile(r"\b(winter|spring|fall|autumn|term[- ]time|part[- ]time|off[- ]cycle)\b", re.I)
GRAD_YEAR_RE = re.compile(r"graduat\w*[^.;]{0,80}", re.I)
YEAR_RE = re.compile(r"\b(20[2-3]\d)\b")


def classify_family(title: str, description: str = "") -> str:
    """Classify by title only.

    Descriptions are not used: nearly every posting at a trading firm describes the
    firm as a trading firm, which made HR and accounting roles look like trading roles.
    """
    if NON_TARGET_RE.search(title):
        return "other"
    for family, pattern in FAMILY_PATTERNS:
        if pattern.search(title):
            return family
    if re.search(r"\bquant", title, re.I):
        return "quant_research"
    return "other"


def seniority_factor(p: Posting, cfg: dict) -> tuple[float, str]:
    s = cfg.get("scoring", {})
    meta_values = " ".join(str(v) for v in p.raw_meta.values() if v).lower()
    if INTERN_RE.search(p.title) or "intern" in meta_values:
        return s.get("intern_bonus", 1.0), "internship"
    if FULLTIME_RE.search(p.title) or any(k in meta_values for k in ("graduate", "experienced", "full")):
        return s.get("non_intern_penalty", 0.15), "full time or graduate"
    return 0.5, "unclear seniority"


def cycle_factor(p: Posting, cfg: dict) -> tuple[float, str]:
    """Does the posting target your recruiting cycle and graduation year?"""
    grad = int(cfg.get("candidate", {}).get("graduation_year", 2028))
    penalty = cfg.get("scoring", {}).get("wrong_cycle_penalty", 0.35)
    target_summer = grad - 1

    off = OFF_SEASON_RE.search(p.title)
    if off and not SUMMER_RE.search(p.title):
        return penalty, f"{off.group(1).lower()} program, want summer {target_summer}"

    summer = SUMMER_RE.search(p.title)
    if summer:
        year = int(summer.group(1))
        return (1.0, f"summer {year}") if year == target_summer else (penalty, f"summer {year}, want {target_summer}")

    grad_years: list[int] = []
    for clause in GRAD_YEAR_RE.findall(p.description):
        grad_years += [int(y) for y in YEAR_RE.findall(clause)]
    if grad_years:
        lo, hi = min(grad_years), max(grad_years)
        if lo <= grad <= hi:
            return 1.0, f"grad window {lo} to {hi} fits"
        return penalty, f"grad window {lo} to {hi} misses {grad}"
    return 1.0, "no cycle stated"


def location_factor(p: Posting, cfg: dict) -> tuple[float, str]:
    wanted = [w.lower() for w in cfg.get("targets", {}).get("locations", [])]
    if not wanted or not p.location:
        return 1.0, "no location filter"
    loc = p.location.lower()
    if any(w in loc for w in wanted):
        return 1.0, p.location
    return cfg.get("scoring", {}).get("location_miss_penalty", 0.6), f"{p.location} not preferred"


def freshness_factor(published: datetime | None, cfg: dict, now: datetime | None = None) -> tuple[float, float | None]:
    if published is None:
        return 0.8, None
    now = now or utcnow()
    days = max((now - published).total_seconds() / 86400, 0.0)
    s = cfg.get("scoring", {})
    half_life = s.get("freshness_half_life_days", 7)
    floor = s.get("freshness_floor", 0.75)
    decay = 0.5 ** (days / half_life)
    # Campus postings at quant firms often open in July and stay up for months, so age
    # is a tiebreaker, not a dealbreaker. Fit to you should dominate the ranking.
    return floor + (1 - floor) * decay, round(days, 1)


def hard_skip(p: Posting, cfg: dict) -> str | None:
    t = cfg.get("targets", {})
    if p.firm.lower() in {c.lower() for c in t.get("skip_companies", [])}:
        return "firm on skip list"
    title = f" {p.title.lower()} "
    for kw in t.get("skip_title_keywords", []):
        if kw.lower() in title:
            return f"title contains '{kw.strip()}'"
    if cfg.get("candidate", {}).get("needs_sponsorship") and re.search(
        r"no (visa )?sponsorship|security clearance|itar|u\.s\. (citizen|person)", p.text
    ):
        return "sponsorship or clearance restriction"
    return None


def score_posting(p: Posting, cfg: dict, firm_tier: float = 1.0, now: datetime | None = None) -> tuple[str, float, dict]:
    family = classify_family(p.title, p.description)
    skip = hard_skip(p, cfg)
    if skip:
        return family, 0.0, {"skip": skip}

    fam_w = cfg.get("family_weights", {}).get(family, 0.1)
    sen, sen_note = seniority_factor(p, cfg)
    cyc, cyc_note = cycle_factor(p, cfg)
    loc, loc_note = location_factor(p, cfg)
    fresh, age_days = freshness_factor(p.published_at, cfg, now)

    score = 100 * fam_w * firm_tier * sen * cyc * loc * fresh
    detail = {
        "family": [family, round(fam_w, 2)],
        "tier": round(firm_tier, 2),
        "seniority": [sen_note, round(sen, 2)],
        "cycle": [cyc_note, round(cyc, 2)],
        "location": [loc_note, round(loc, 2)],
        "freshness": [age_days, round(fresh, 2)],
    }
    return family, round(score, 1), detail


def explain(detail: dict) -> str:
    """One line, human readable breakdown for the CLI."""
    if "skip" in detail:
        return f"skipped: {detail['skip']}"
    parts = [
        f"{detail['family'][0]} x{detail['family'][1]}",
        f"tier x{detail['tier']}",
        f"{detail['seniority'][0]} x{detail['seniority'][1]}",
        f"{detail['cycle'][0]} x{detail['cycle'][1]}",
        f"{detail['location'][0]} x{detail['location'][1]}",
    ]
    age = detail["freshness"][0]
    parts.append(f"{age if age is not None else '?'}d old x{detail['freshness'][1]}")
    return "  |  ".join(parts)
