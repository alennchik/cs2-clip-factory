"""Metadata-only ranking.

This is a heuristic to decide what to process first, not a virality predictor. Tune it with
real TikTok results (Phase 3) before adding anything smarter.
"""

from __future__ import annotations

from datetime import datetime


def duration_factor(duration: float) -> float:
    if duration < 8:
        return 0.5  # too short to carry a story
    if duration <= 45:
        return 1.0
    if duration <= 60:
        return 0.85  # usable, will be trimmed
    return 0.6


def score_clip(
    view_count: int,
    created_at: datetime,
    duration: float,
    *,
    now: datetime,
    weight: float = 1.0,
) -> float:
    """Views per hour with gravity (fresh clips rise), times duration fit and streamer weight."""
    age_hours = max((now - created_at).total_seconds() / 3600.0, 0.0)
    velocity = view_count / (age_hours + 2.0) ** 1.2
    return round(velocity * duration_factor(duration) * weight, 4)
