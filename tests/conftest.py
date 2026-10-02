import json
from datetime import datetime, timedelta, timezone

import pytest

from offerdesk.sources import ashby, greenhouse, lever

NOW = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)

# Trimmed from the shape of a real Greenhouse board response.
GREENHOUSE = {
    "jobs": [
        {
            "id": 4907430101,
            "title": "Machine Learning Research Intern - Summer 2027 - Chicago",
            "absolute_url": "https://job-boards.eu.greenhouse.io/example/jobs/4907430101",
            "location": {"name": "Chicago, United States"},
            "first_published": "2026-09-28T13:18:38-04:00",
            "updated_at": "2026-09-30T06:15:59-04:00",
            "content": "&lt;p&gt;Use &lt;b&gt;Python&lt;/b&gt; and statistics to research signals.&lt;/p&gt;",
            "metadata": [
                {"name": "Worker Sub Type", "value": "Intern"},
                {"name": "Job Category", "value": "Trading"},
                {"name": "Unused", "value": None},
            ],
        },
        {
            "id": 4667815101,
            "title": "Graduate Trader",
            "absolute_url": "https://job-boards.eu.greenhouse.io/example/jobs/4667815101",
            "location": {"name": "Amsterdam, Netherlands"},
            "first_published": "2026-07-31T11:11:37-04:00",
            "metadata": [{"name": "Worker Sub Type", "value": "Graduate"}],
        },
    ]
}

LEVER = [
    {
        "id": "abc-123",
        "text": "Investment Banking Summer Analyst 2027",
        "hostedUrl": "https://jobs.lever.co/example/abc-123",
        "createdAt": int((NOW - timedelta(days=2)).timestamp() * 1000),
        "categories": {"location": "New York, NY", "commitment": "Intern", "team": "IBD"},
        "descriptionPlain": "Support M&A transactions and build valuation models.",
        "lists": [{"text": "Requirements", "content": "<li>Financial modeling</li>"}],
        "salaryRange": {"min": 50, "max": 60, "currency": "USD", "interval": "per-hour-wage"},
    }
]

ASHBY = {
    "jobs": [
        {
            "id": "f00",
            "title": "Software Engineering Intern",
            "location": "Remote",
            "jobUrl": "https://jobs.ashbyhq.com/example/f00",
            "publishedAt": "2026-09-30T00:00:00.000Z",
            "descriptionPlain": "Build backend services in Python.",
            "employmentType": "Intern",
            "isListed": True,
            "compensation": {"compensationTierSummary": "$55/hr"},
        },
        {"id": "hidden", "title": "Unlisted", "isListed": False},
    ]
}


@pytest.fixture
def cfg():
    return {
        "candidate": {"graduation_year": 2028, "needs_sponsorship": False},
        "targets": {"locations": ["Chicago", "New York", "Remote"],
                    "skip_title_keywords": ["phd", "senior"]},
        "family_weights": {"quant_research": 1.0, "quant_trading": 0.95, "quant_dev": 0.85,
                           "investment_banking": 0.7, "private_equity": 0.7, "markets": 0.75,
                           "swe": 0.65, "data_science": 0.6, "other": 0.1},
        "scoring": {"freshness_half_life_days": 7, "intern_bonus": 1.0, "non_intern_penalty": 0.15,
                    "wrong_cycle_penalty": 0.35, "location_miss_penalty": 0.6},
    }


@pytest.fixture
def gh_postings():
    return greenhouse.parse(json.loads(json.dumps(GREENHOUSE)), "Example Trading")


@pytest.fixture
def lever_postings():
    return lever.parse(LEVER, "Example Bank")


@pytest.fixture
def ashby_postings():
    return ashby.parse(ASHBY, "Example Labs")
