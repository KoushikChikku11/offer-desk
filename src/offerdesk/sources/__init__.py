"""ATS fetchers. Each one turns a public job board JSON feed into Posting objects.

All three providers publish unauthenticated JSON for their hosted boards, so sourcing
never needs a browser. Parsing is split from fetching so it can be tested on fixtures.
"""

from __future__ import annotations

import requests

from ..models import Posting
from . import ashby, greenhouse, lever

PROVIDERS = {
    "greenhouse": greenhouse,
    "lever": lever,
    "ashby": ashby,
}

USER_AGENT = "offerdesk/0.1 (personal job search tool)"


def session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def fetch(firm: dict, sess: requests.Session | None = None, timeout: float = 20) -> list[Posting]:
    provider = PROVIDERS.get(firm.get("ats", ""))
    if provider is None:
        return []
    sess = sess or session()
    resp = sess.get(provider.board_url(firm["slug"]), timeout=timeout)
    resp.raise_for_status()
    return provider.parse(resp.json(), firm["name"])


def probe(firm: dict, sess: requests.Session | None = None, timeout: float = 15) -> tuple[bool, int, str]:
    """Check whether a slug is live. Returns (ok, job_count, message)."""
    try:
        postings = fetch(firm, sess, timeout)
    except requests.HTTPError as exc:
        return False, 0, f"HTTP {exc.response.status_code}"
    except (requests.RequestException, ValueError) as exc:
        return False, 0, type(exc).__name__
    return True, len(postings), "ok"
