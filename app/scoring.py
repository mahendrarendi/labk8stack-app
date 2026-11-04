"""Perhitungan skor: makin cepat dan makin sedikit percobaan, makin besar."""

from __future__ import annotations

MIN_SCORE = 20
TRY_PENALTY = 10


def compute_score(points: int, duration: int, elapsed: float, tries: int) -> int:
    """Skor 50%-100% dari poin, dikurangi penalti tiap percobaan tambahan."""
    if duration <= 0:
        return MIN_SCORE
    ratio_left = max(0.0, 1.0 - (elapsed / duration))
    score = points * (0.5 + 0.5 * ratio_left)
    score -= max(0, tries - 1) * TRY_PENALTY
    return max(MIN_SCORE, int(round(score)))


def seconds_left(started_at: str, duration: int) -> int:
    from datetime import datetime, timezone

    started = datetime.fromisoformat(started_at)
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    return max(0, int(duration - elapsed))
