"""Core data types shared across sourcing, scoring, routing, and analytics."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import unescape

STAGES = ["applied", "oa", "interview", "final", "offer"]
TERMINAL = ["rejected", "withdrawn", "ghosted"]
ALL_STAGES = STAGES + TERMINAL


@dataclass
class Posting:
    """One job posting, normalized across ATS providers."""

    firm: str
    ats: str
    external_id: str
    title: str
    location: str = ""
    url: str = ""
    description: str = ""
    published_at: datetime | None = None
    comp_text: str = ""
    raw_meta: dict = field(default_factory=dict)

    @property
    def uid(self) -> str:
        return f"{self.ats}:{self.firm}:{self.external_id}"

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.location}\n{self.description}".lower()


_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def html_to_text(html: str | None) -> str:
    """Strip tags and collapse whitespace. Greenhouse double escapes its HTML."""
    if not html:
        return ""
    text = unescape(unescape(html))
    text = _TAG.sub(" ", text)
    return _WS.sub(" ", text).strip()


def parse_ts(value) -> datetime | None:
    """Parse ISO strings or epoch milliseconds into an aware UTC datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
