"""Lever postings API: api.lever.co/v0/postings/<slug>?mode=json"""

from __future__ import annotations

from ..models import Posting, html_to_text, parse_ts


def board_url(slug: str) -> str:
    return f"https://api.lever.co/v0/postings/{slug}?mode=json"


def parse(payload: list, firm: str) -> list[Posting]:
    out = []
    for job in payload or []:
        cats = job.get("categories") or {}
        description = job.get("descriptionPlain") or html_to_text(job.get("description"))
        for block in job.get("lists") or []:
            description += " " + block.get("text", "") + " " + html_to_text(block.get("content"))
        comp = job.get("salaryRange") or {}
        comp_text = ""
        if comp.get("min") and comp.get("max"):
            comp_text = f"{comp['min']} to {comp['max']} {comp.get('currency', '')} {comp.get('interval', '')}".strip()
        out.append(
            Posting(
                firm=firm,
                ats="lever",
                external_id=str(job["id"]),
                title=(job.get("text") or "").strip(),
                location=(cats.get("location") or "").strip(),
                url=job.get("hostedUrl", ""),
                description=description.strip(),
                published_at=parse_ts(job.get("createdAt")),
                comp_text=comp_text,
                raw_meta={"commitment": cats.get("commitment"), "team": cats.get("team")},
            )
        )
    return out
