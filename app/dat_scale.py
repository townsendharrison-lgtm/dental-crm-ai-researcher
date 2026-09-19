"""DAT dual-scale helpers (ADA Academic Average concordance).

Legacy scale: 1–30
Modern scale: 200–600 (10-point increments)

Detection: score ≤ 30 → legacy; score > 30 → modern.
Keep in sync with backend/src/services/datScale.ts and next-frontend/lib/utils/datScale.ts.
"""
from __future__ import annotations

from decimal import Decimal
import re
from typing import Literal

DatScale = Literal["legacy", "modern"]

# Official ADA AA concordance: old (1–30) → new (200–600).
DAT_AA_OLD_TO_NEW: dict[int, int] = {
    1: 200, 2: 200, 3: 210, 4: 220, 5: 220, 6: 230, 7: 240, 8: 240, 9: 250, 10: 250,
    11: 260, 12: 270, 13: 290, 14: 310, 15: 330, 16: 350, 17: 370, 18: 390, 19: 410, 20: 420,
    21: 440, 22: 460, 23: 470, 24: 490, 25: 510, 26: 520, 27: 540, 28: 560, 29: 580, 30: 600,
}

_DAT_SECTION_KEY = re.compile(r"^(min|avg|max)_dat_[a-z0-9_]+$")


def is_dat_section_factor(factor_key: str) -> bool:
    """True for numeric DAT section scores (AA/PAT/…), not attempts/policy text."""
    return bool(_DAT_SECTION_KEY.match(factor_key))


def detect_dat_scale(score: Decimal | float | int | None) -> DatScale | None:
    if score is None:
        return None
    try:
        n = float(score)
    except (TypeError, ValueError):
        return None
    if not n or n <= 0:
        return None
    return "legacy" if n <= 30 else "modern"


def normalize_dat_to_legacy(score: Decimal | float | int | None) -> Decimal | None:
    """Convert any DAT AA/section score to a legacy 1–30 equivalent."""
    if score is None:
        return None
    try:
        n = float(score)
    except (TypeError, ValueError):
        return None
    if not n or n <= 0:
        return None
    if n <= 30:
        return Decimal(str(round(n, 1)))
    best_old = 1
    best_dist = float("inf")
    for old, neu in DAT_AA_OLD_TO_NEW.items():
        dist = abs(neu - n)
        if dist < best_dist:
            best_dist = dist
            best_old = old
    return Decimal(best_old)


def normalize_dat_to_modern(score: Decimal | float | int | None) -> Decimal | None:
    """Convert any DAT AA/section score to modern 200–600 via AA concordance."""
    if score is None:
        return None
    try:
        n = float(score)
    except (TypeError, ValueError):
        return None
    if not n or n <= 0:
        return None
    if n > 30:
        return Decimal(int(round(n)))
    old = min(30, max(1, int(round(n))))
    neu = DAT_AA_OLD_TO_NEW.get(old)
    return Decimal(neu) if neu is not None else None


def align_dat_pair(
    student: Decimal,
    school: Decimal | None,
    *,
    student_scale_hint: str | None = None,
) -> tuple[Decimal, Decimal | None, str] | None:
    """Align student + school DAT values onto the school's native scale for comparison.

    Returns (aligned_student, aligned_school, method_suffix) or None if unresolvable.
    Prefer the school's scale so expectations stay as published.
    """
    school_scale = detect_dat_scale(school) if school is not None else None
    student_scale = _scale_from_hint(student_scale_hint) or detect_dat_scale(student)
    if student_scale is None:
        return None
    if school is None:
        return student, None, "dat_student_only"
    if school_scale is None:
        return None
    if student_scale == school_scale:
        return student, school, f"dat_same_{student_scale}"
    if school_scale == "legacy":
        aligned = normalize_dat_to_legacy(student)
        if aligned is None:
            return None
        return aligned, school, "dat_student_modern_to_legacy"
    aligned = normalize_dat_to_modern(student)
    if aligned is None:
        return None
    return aligned, school, "dat_student_legacy_to_modern"


def _scale_from_hint(hint: str | None) -> DatScale | None:
    if not hint:
        return None
    cleaned = hint.strip().lower().replace("-", "_")
    if cleaned in {"legacy", "classic", "old", "1_30", "scale_30"}:
        return "legacy"
    if cleaned in {"modern", "new", "irt_200_600", "200_600", "scale_600", "irt"}:
        return "modern"
    if "200" in cleaned and "600" in cleaned:
        return "modern"
    if "30" in cleaned and "600" not in cleaned:
        return "legacy"
    return None
