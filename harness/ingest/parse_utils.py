"""Strict helpers for untrusted dates and numbers extracted from documents."""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from typing import Any, Optional


def parse_iso_date(value: Any) -> Optional[date]:
    """Parse an unambiguous ISO date; return None instead of inventing a date."""
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    # Date-time values are accepted only when the complete value is ISO.
    try:
        if len(candidate) == 10:
            return date.fromisoformat(candidate)
        return datetime.fromisoformat(candidate.replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        return None


def parse_finite_number(value: Any, default: Optional[float] = None) -> Optional[float]:
    """Parse common OCR numeric formats while rejecting NaN and infinity."""
    if isinstance(value, bool) or value is None:
        return default
    try:
        if isinstance(value, str):
            candidate = value.strip().replace("−", "-")
            if not candidate:
                return default
            negative = candidate.startswith("(") and candidate.endswith(")")
            if negative:
                candidate = candidate[1:-1]
            candidate = re.sub(r"(?i)INR|RS\.?|₹|,|\s", "", candidate)
            number = float(candidate)
            if negative:
                number = -number
        else:
            number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default
