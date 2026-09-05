from __future__ import annotations

from datetime import date, timedelta


def sm2_update(quality: int, ease_factor: float, interval: int) -> tuple[float, int]:
    """
    SM-2 algorithm (same as Anki).

    Args:
        quality: 0-5 rating (0=complete blackout, 5=perfect recall)
        ease_factor: current ease factor (starts at 2.5)
        interval: current interval in days

    Returns:
        (new_ease_factor, new_interval_days)

    Rules:
        - If quality < 3: lapse → reset interval to 1 day
        - If quality >= 3: interval grows by ease_factor
        - Ease factor floor: 1.3
        - Ease factor adjustment: EF' = EF + (0.1 - (5-q) * (0.08 + (5-q) * 0.02))
    """
    if quality < 3:
        new_interval = 1
        new_ef = max(ease_factor - 0.2, 1.3)
        return new_ef, new_interval

    if interval == 0:
        new_interval = 1
    elif interval == 1:
        new_interval = 6
    else:
        new_interval = round(interval * ease_factor)

    new_ef = ease_factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    new_ef = max(new_ef, 1.3)

    return new_ef, max(new_interval, 1)


def next_review_date(interval_days: int) -> date:
    return date.today() + timedelta(days=interval_days)
