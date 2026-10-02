"""Short cover letter drafts for Greenhouse, Lever, and Ashby text boxes.

This only stitches together sentences you wrote in config/profile.yaml. It never adds
claims about you. Treat the output as a first draft to edit, not something to paste blind.
"""

from __future__ import annotations

ROLE_NOUN = {
    "quant_research": "quantitative research",
    "quant_trading": "trading",
    "quant_dev": "quantitative development",
    "markets": "markets",
    "investment_banking": "investment banking",
    "private_equity": "private equity",
    "swe": "software engineering",
    "data_science": "data science",
    "other": "",
}


def draft(profile: dict, firm: str, title: str, family: str, resume_version: str) -> str:
    pitch = (profile.get("pitch") or {}).get(resume_version, "").strip()
    why = (profile.get("why_family") or {}).get(family) or (profile.get("why_family") or {}).get("other", "")
    name = profile.get("name", "")
    school = profile.get("school", "")
    majors = profile.get("majors", "")
    grad = profile.get("graduation", "")
    noun = ROLE_NOUN.get(family, "")

    intro = f"I am a {majors} student at {school} graduating in {grad}, applying for the {title} role at {firm}."
    interest = f"{why.strip()} That is what draws me to {noun} at {firm}." if noun else why.strip()
    lines = [
        f"Dear {firm} Recruiting Team,",
        "",
        intro,
        "",
        pitch,
        "",
        interest,
        "",
        profile.get("closing", "Thank you for your consideration.").strip(),
        "",
        name,
    ]
    return "\n".join(line for line in lines if line is not None).strip() + "\n"


def word_count(text: str) -> int:
    return len(text.split())
