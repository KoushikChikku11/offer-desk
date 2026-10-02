"""Greenhouse job board API: boards-api.greenhouse.io/v1/boards/<slug>/jobs"""

from __future__ import annotations

from ..models import Posting, html_to_text, parse_ts


def board_url(slug: str) -> str:
    return f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"


def parse(payload: dict, firm: str) -> list[Posting]:
    out = []
    for job in payload.get("jobs", []):
        meta = {m.get("name"): m.get("value") for m in job.get("metadata") or [] if m.get("value") is not None}
        # Greenhouse metadata is per company. Keep anything useful for scoring, like
        # IMC's "Worker Sub Type" (Intern / Graduate / Experienced).
        out.append(
            Posting(
                firm=firm,
                ats="greenhouse",
                external_id=str(job["id"]),
                title=(job.get("title") or "").strip(),
                location=((job.get("location") or {}).get("name") or "").strip(),
                url=job.get("absolute_url", ""),
                description=html_to_text(job.get("content")),
                published_at=parse_ts(job.get("first_published") or job.get("updated_at")),
                raw_meta=meta,
            )
        )
    return out
