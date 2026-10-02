"""Sync: pull every configured board, score it, and update the database."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import requests

from . import db, sources
from .models import Posting
from .scoring import score_posting


@dataclass
class SyncReport:
    firms_ok: int = 0
    seen: int = 0
    new: int = 0
    closed: int = 0
    errors: dict = field(default_factory=dict)
    new_top: list = field(default_factory=list)  # (score, firm, title) for new postings worth a look


def ingest(conn, postings: list[Posting], firm: dict, cfg: dict, report: SyncReport) -> None:
    seen = set()
    for p in postings:
        family, score, detail = score_posting(p, cfg, firm_tier=firm.get("tier", 1.0))
        is_new = db.upsert_posting(conn, p, family, score, detail)
        seen.add(p.uid)
        report.seen += 1
        if is_new:
            report.new += 1
            if score >= cfg.get("scoring", {}).get("min_score_for_queue", 20):
                report.new_top.append((score, p.firm, p.title))
    report.closed += db.mark_closed(conn, firm["name"], firm["ats"], seen)


def sync(conn, firms: list[dict], cfg: dict, only: str | None = None) -> SyncReport:
    report = SyncReport()
    sess = sources.session()
    for firm in firms:
        if firm.get("ats") not in sources.PROVIDERS:
            continue
        if only and only.lower() not in firm["name"].lower():
            continue
        try:
            postings = sources.fetch(firm, sess)
        except (requests.RequestException, ValueError) as exc:
            report.errors[firm["name"]] = type(exc).__name__
            continue
        ingest(conn, postings, firm, cfg, report)
        report.firms_ok += 1
    report.new_top.sort(reverse=True)
    return report


def rescore(conn, firms: list[dict], cfg: dict) -> int:
    """Recompute scores after you change settings, without refetching."""
    tiers = {f["name"]: f.get("tier", 1.0) for f in firms}
    rows = conn.execute("SELECT * FROM postings WHERE active=1").fetchall()
    for r in rows:
        p = Posting(
            firm=r["firm"], ats=r["ats"], external_id=r["external_id"], title=r["title"],
            location=r["location"] or "", url=r["url"] or "", description=r["description"] or "",
            published_at=_parse(r["published_at"]) or _parse(r["first_seen"]), comp_text=r["comp_text"] or "",
            raw_meta=json.loads(r["meta"] or "{}"),
        )
        family, score, detail = score_posting(p, cfg, firm_tier=tiers.get(r["firm"], 1.0))
        db.upsert_posting(conn, p, family, score, detail)
    return len(rows)


def _parse(value):
    from .models import parse_ts
    return parse_ts(value)
