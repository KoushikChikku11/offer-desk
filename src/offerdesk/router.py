"""Resume router: send each posting the resume variant most likely to land.

Two layers:

1. Rules. Keyword evidence for each variant adds up in log odds space, starting from a
   prior set by the role family. Transparent and works on day one with zero data.
2. Learned. Once you have logged enough applications, a TF IDF + logistic regression
   model is trained on the postings you applied to and the resume you actually chose.
   Its probability is blended with the rules, weighted by how much data it has seen.

The output is always a recommendation with a confidence and the evidence behind it.
You make the final call, and that call becomes the next training label.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

# Positive weights push toward CSQNT (quant framed), negative toward FIN.
KEYWORDS: dict[str, float] = {
    r"\bpython\b": 0.6,
    r"c\+\+": 0.8,
    r"\bstatistic\w*": 0.5,
    r"probabilit\w*": 0.6,
    r"machine learning|\bml\b": 0.7,
    r"stochastic|time series": 0.7,
    r"option(s)? pricing|volatility|derivative": 0.6,
    r"algorithm|data structure": 0.5,
    r"\bresearch\b": 0.3,
    r"computer science|\bcs\b|engineering degree": 0.5,
    r"\bmath(ematics)?\b|physics": 0.4,
    r"investment bank|\bibd\b": -1.0,
    r"m&a|mergers|acquisitions": -0.9,
    r"valuation|\bdcf\b|lbo": -0.8,
    r"financial model": -0.7,
    r"corporate finance|capital markets": -0.6,
    r"accounting|financial statement": -0.6,
    r"private equity|portfolio compan": -0.8,
    r"client|pitch( book)?|relationship": -0.3,
    r"excel|powerpoint": -0.3,
}
_COMPILED = [(re.compile(k, re.I), w) for k, w in KEYWORDS.items()]

FAMILY_PRIOR: dict[str, float] = {
    "quant_research": 1.2,
    "quant_trading": 0.8,
    "quant_dev": 1.2,
    "data_science": 0.8,
    "swe": 1.0,
    "markets": -0.2,
    "investment_banking": -1.4,
    "private_equity": -1.4,
    "other": 0.0,
}


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


@dataclass
class Route:
    version: str
    p_csqnt: float
    confidence: float
    evidence: list[str] = field(default_factory=list)
    model: str = "rules"

    def summary(self) -> str:
        ev = ", ".join(self.evidence[:5]) or "family prior only"
        return f"{self.version} ({self.confidence:.0%} confident, {self.model}) evidence: {ev}"


def rule_logit(text: str, family: str) -> tuple[float, list[str]]:
    logit = FAMILY_PRIOR.get(family, 0.0)
    evidence = [f"family {family} {FAMILY_PRIOR.get(family, 0):+.1f}"]
    for pattern, weight in _COMPILED:
        matches = list(pattern.finditer(text))
        hits = len(matches)
        if hits:
            # Diminishing returns: the third mention of Python is not 3x the evidence.
            contrib = weight * (1 + math.log(hits))
            logit += contrib
            label = matches[0].group(0).lower()
            evidence.append(f"{label} {contrib:+.1f}")
    evidence.sort(key=lambda e: -abs(float(e.rsplit(" ", 1)[1])))
    return logit, evidence


class Router:
    def __init__(self, min_labels: int = 20):
        self.min_labels = min_labels
        self.pipeline = None
        self.n_train = 0

    def fit(self, texts: list[str], labels: list[str]) -> bool:
        """Train the learned layer. Returns False when there is not enough data yet."""
        n_c = sum(1 for y in labels if y == "CSQNT")
        n_f = len(labels) - n_c
        if len(labels) < self.min_labels or min(n_c, n_f) < 5:
            self.pipeline, self.n_train = None, len(labels)
            return False
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline

        self.pipeline = make_pipeline(
            TfidfVectorizer(ngram_range=(1, 2), min_df=2, max_features=5000, sublinear_tf=True),
            LogisticRegression(C=1.0, class_weight="balanced", max_iter=1000),
        )
        self.pipeline.fit(texts, [1 if y == "CSQNT" else 0 for y in labels])
        self.n_train = len(labels)
        return True

    def route(self, text: str, family: str) -> Route:
        logit, evidence = rule_logit(text, family)
        p = _sigmoid(logit)
        model = "rules"
        if self.pipeline is not None:
            p_ml = float(self.pipeline.predict_proba([text])[0][1])
            # Trust the model more as it sees more of your decisions.
            w = self.n_train / (self.n_train + 50)
            p = (1 - w) * p + w * p_ml
            model = f"rules + model (n={self.n_train}, w={w:.2f})"
        version = "CSQNT" if p >= 0.5 else "FIN"
        return Route(version=version, p_csqnt=round(p, 3), confidence=round(max(p, 1 - p), 3),
                     evidence=evidence, model=model)


def router_from_db(conn, min_labels: int = 20) -> Router:
    rows = conn.execute(
        """SELECT a.resume_version, a.title, COALESCE(p.description, '') AS description
           FROM applications a LEFT JOIN postings p ON p.uid = a.posting_uid
           WHERE a.resume_version IN ('FIN', 'CSQNT')"""
    ).fetchall()
    r = Router(min_labels=min_labels)
    r.fit([f"{x['title']} {x['description']}" for x in rows], [x["resume_version"] for x in rows])
    return r
