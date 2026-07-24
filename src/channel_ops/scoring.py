from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Criterion:
    key: str
    label: str
    weight: int


CRITERIA = (
    Criterion("demand", "Audience demand", 20),
    Criterion("story", "Story / curiosity", 20),
    Criterion("competition", "Competitive gap", 15),
    Criterion("evergreen", "Evergreen value", 15),
    Criterion("revenue", "Revenue / sponsor fit", 10),
    Criterion("sources", "Source verifiability", 10),
    Criterion("visuals", "Visual / license availability", 5),
    Criterion("production", "Production feasibility", 5),
)


def calculate(scores: dict[str, float], *, threshold: float = 75.0) -> dict:
    """Calculate a weighted 0-100 topic score.

    Parameters
    ----------
    scores:
        A dict mapping each criterion key to a 0-10 rating.
    threshold:
        The minimum score to qualify. Loaded from config in practice;
        kept as a parameter so the function is self-contained and testable.
    """
    unknown = set(scores) - {c.key for c in CRITERIA}
    missing = {c.key for c in CRITERIA} - set(scores)
    if unknown:
        raise ValueError(f"Unknown score criteria: {', '.join(sorted(unknown))}")
    if missing:
        raise ValueError(f"Missing score criteria: {', '.join(sorted(missing))}")

    items = []
    total = 0.0
    for criterion in CRITERIA:
        value = scores[criterion.key]
        if not 0 <= value <= 10:
            raise ValueError(f"'{criterion.key}' must be between 0 and 10, got {value}.")
        weighted = value / 10 * criterion.weight
        total += weighted
        items.append({
            "key": criterion.key,
            "label": criterion.label,
            "weight": criterion.weight,
            "rating": value,
            "weighted_score": round(weighted, 2),
        })

    qualified = total >= threshold
    logger.info(
        "Topic scored %.1f/100 (threshold=%.0f) → %s",
        total, threshold, "QUALIFIED" if qualified else "NOT QUALIFIED",
    )
    return {
        "scale": 100,
        "total": round(total, 2),
        "threshold": threshold,
        "qualified": qualified,
        "decision_status": "provisional_pending_evidence",
        "criteria": items,
    }


def parse_score_arguments(values: list[str]) -> dict[str, float]:
    """Parse CLI ``--score key=value`` pairs into a dict."""
    scores: dict[str, float] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"Invalid score '{value}'. Use criterion=value.")
        key, raw_value = value.split("=", 1)
        scores[key.strip()] = float(raw_value)
    return scores
