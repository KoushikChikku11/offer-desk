"""Ashby posting API: api.ashbyhq.com/posting-api/job-board/<slug>"""

from __future__ import annotations

from ..models import Posting, html_to_text, parse_ts


def board_url(slug: str) -> str:
    return f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"


def parse(payload: dict, firm: str) -> list[Posting]:
    out = []
    for job in payload.get("jobs", []):
        if job.get("isListed") is False:
            continue
        comp = job.get("compensation") or {}
        out.append(
            Posting(
                firm=firm,
                ats="ashby",
                external_id=str(job.get("id") or job.get("jobUrl")),
                title=(job.get("title") or "").strip(),
                location=(job.get("location") or "").strip(),
                url=job.get("jobUrl") or job.get("applyUrl") or "",
                description=job.get("descriptionPlain") or html_to_text(job.get("descriptionHtml")),
                published_at=parse_ts(job.get("publishedAt")),
                comp_text=comp.get("compensationTierSummary") or "",
                raw_meta={"employmentType": job.get("employmentType"), "department": job.get("department")},
            )
        )
    return out
