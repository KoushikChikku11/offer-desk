"""Referral and networking tracker.

At most quant shops and banks a referral or a real conversation moves an application
further than any resume tweak. Contacts live next to applications so the analytics can
measure that effect on your own data.
"""

from __future__ import annotations

from datetime import timedelta

from .models import parse_ts, utcnow

OPEN_STATUSES = ("reached_out", "replied", "chatted")
STATUSES = ("reached_out", "replied", "chatted", "referred", "dormant")


def due_followups(conn, after_days: int = 10) -> list[dict]:
    """Open conversations that have gone quiet for longer than `after_days`."""
    cutoff = utcnow() - timedelta(days=after_days)
    out = []
    for r in conn.execute("SELECT * FROM contacts WHERE status IN (?,?,?)", OPEN_STATUSES):
        last = parse_ts(r["last_contact"])
        if last is None or last < cutoff:
            days = (utcnow() - last).days if last else None
            out.append({**dict(r), "days_quiet": days})
    return sorted(out, key=lambda c: -(c["days_quiet"] or 10_000))


def firm_coverage(conn) -> list[dict]:
    """For each firm with an open posting or application, do you know anyone there?"""
    rows = conn.execute(
        """SELECT f.firm,
                  COUNT(DISTINCT c.id) AS contacts,
                  SUM(CASE WHEN c.status = 'referred' THEN 1 ELSE 0 END) AS referrals
           FROM (SELECT DISTINCT firm FROM postings WHERE active = 1 AND status = 'new' AND score >= 20
                 UNION SELECT DISTINCT firm FROM applications) f
           LEFT JOIN contacts c ON lower(c.firm) = lower(f.firm)
           GROUP BY f.firm ORDER BY contacts ASC, f.firm"""
    ).fetchall()
    return [dict(r) for r in rows]
