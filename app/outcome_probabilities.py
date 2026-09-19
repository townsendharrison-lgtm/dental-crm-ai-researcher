"""Outcome probability estimates derived from a deterministic fit score.

These are transparent, fit-score-based estimates for CRM UX — not calibrated
admissions odds. Kind: fit_score_derived_v1.
"""
from __future__ import annotations

import math


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _sigmoid(x: float) -> float:
    # Numerically stable logistic.
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def fit_to_outcome_probabilities(fit_score_0_100: float) -> dict[str, float | str]:
    """Map fit score (0–100) → interview / acceptance / waitlist / reject %.

    Intuition:
    - Interview rises with mid-to-high fit
    - Acceptance needs a higher bar than interview
    - Waitlist peaks in the middle of the fit range
    - Reject falls as interview+acceptance rise
    """
    f = _clamp(float(fit_score_0_100)) / 100.0
    interview = _clamp(100.0 * _sigmoid(6.0 * (f - 0.45)))
    acceptance = _clamp(100.0 * _sigmoid(7.0 * (f - 0.62)))
    waitlist = _clamp(100.0 * (4.0 * f * (1.0 - f)))
    reject = _clamp(100.0 - (0.55 * interview + 0.45 * acceptance))
    return {
        "interview_probability": round(interview, 2),
        "acceptance_probability": round(acceptance, 2),
        "waitlist_probability": round(waitlist, 2),
        "reject_probability": round(reject, 2),
        "probability_kind": "fit_score_derived_v1",
    }
