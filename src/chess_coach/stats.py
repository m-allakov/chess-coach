"""Small statistics helpers shared by the reports."""

from __future__ import annotations

MIN_SAMPLE = 10   # each side needs at least this many observations to call anything clear


def compare_rates(a: float, na: int, b: float, nb: int, z: float = 1.96) -> dict:
    """Compare two percentages (0-100) measured on `na` and `nb` observations.

    `clear` is True only when the difference is larger than ordinary sampling noise
    (about 95% confidence) and both sides have enough observations. A small sample
    can show a big difference by luck, so it never counts as clear.
    """
    pa, pb = a / 100, b / 100
    noise = z * 100 * ((pa * (1 - pa)) / max(na, 1) + (pb * (1 - pb)) / max(nb, 1)) ** 0.5
    change = a - b
    clear = na >= MIN_SAMPLE and nb >= MIN_SAMPLE and change != 0 and abs(change) >= noise
    return {"change": round(change, 1), "noise_margin": round(noise, 1), "clear": clear}


def compare_means(a: list[float], b: list[float], z: float = 1.96) -> dict:
    """Compare the averages of two lists of per-game values (Welch's approach).

    Use this for rates that are built from many moves per game: moves within one game
    are not independent, so the honest sample size is the number of games and the
    noise comes from how much games differ from each other."""
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return {"change": 0.0, "noise_margin": None, "clear": False}   # None keeps the output valid JSON
    ma, mb = sum(a) / na, sum(b) / nb
    va = sum((x - ma) ** 2 for x in a) / (na - 1)
    vb = sum((x - mb) ** 2 for x in b) / (nb - 1)
    noise = z * (va / na + vb / nb) ** 0.5
    change = ma - mb
    clear = na >= MIN_SAMPLE and nb >= MIN_SAMPLE and change != 0 and abs(change) >= noise
    return {"change": round(change, 1), "noise_margin": round(noise, 1), "clear": clear}
