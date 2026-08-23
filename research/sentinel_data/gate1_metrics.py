"""Pre-swap feature construction and Gate 1 criterion evaluation.

Features are computed strictly from information available before each swap:
signals use venue prints and study-pool swaps with earlier timestamps (or the
same timestamp with a smaller log index). With an empty v4-era training split,
the frozen score is the equal-weight composite of per-pool-day z-scored
signals (prereg section 6 amendment); Spearman correlation makes the composite
rank-comparable without fitted scales.
"""

from __future__ import annotations

import bisect
import json
import math
import random
from typing import Any


def _swap_sort_key(row: dict[str, Any]) -> tuple[int, int]:
    return int(row["timestamp"]), row.get("log_index", 0)


def ewma_volatility(
    series_times: list[float],
    series_prices: list[float],
    *,
    half_life: float,
    step_seconds: float = 1.0,
) -> tuple[list[float], list[float]]:
    """Per-second reference prices and EWMA volatility of their returns."""

    if not series_times:
        return [], []
    start = math.ceil(series_times[0])
    end = math.floor(series_times[-1])
    times: list[float] = []
    prices: list[float] = []
    cursor = 0
    for second in range(start, end + 1):
        while cursor + 1 < len(series_times) and series_times[cursor + 1] <= second:
            cursor += 1
        times.append(float(second))
        prices.append(series_prices[cursor])

    lam = 0.5 ** (step_seconds / half_life)
    var = None
    prev = None
    vols: list[float] = []
    for index, price in enumerate(prices):
        if prev is not None and prev > 0:
            ret = price / prev - 1.0
            var = ret * ret if var is None else lam * var + (1 - lam) * ret * ret
        prev = price
        vols.append(math.sqrt(var) if var is not None else float("nan"))
    return times, vols


def vol_at(times: list[float], vols: list[float], moment: float) -> float:
    idx = bisect.bisect_left(times, moment) - 1
    if idx < 0 or idx >= len(vols):
        return float("nan")
    return vols[idx]


def rolling_flow_imbalance(
    labeled_rows: list[dict[str, Any]],
    window_seconds: float,
) -> list[float]:
    """Signed-notional share over a trailing window, strictly pre-swap, O(n).

    Trades are processed in (timestamp, log_index) order; each trade's value is
    computed before that trade joins the window, so no trade ever sees itself
    or same-instant trades ordered after it.
    """

    ordered = sorted(labeled_rows, key=_swap_sort_key)
    flows: list[float] = []
    window: list[tuple[float, float, float]] = []  # (timestamp, signed, notional)
    running_signed = 0.0
    running_total = 0.0
    for row in ordered:
        t = float(row["timestamp"])
        while window and t - window[0][0] > window_seconds:
            _, sgn, ntn = window.pop(0)
            running_signed -= sgn
            running_total -= ntn
        denom = running_total if running_total > 0 else 1.0
        flows.append(running_signed / denom)
        direction = float(row["direction"])
        notional = float(row["notional_token1"] or 0.0)
        window.append((t, direction * notional, notional))
        running_signed += direction * notional
        running_total += notional
    return flows


def zscore_within_day(values: list[float]) -> list[float]:
    finite = [v for v in values if not math.isnan(v)]
    if len(finite) < 2:
        return [0.0 for _ in values]
    mean = sum(finite) / len(finite)
    var = sum((v - mean) ** 2 for v in finite) / (len(finite) - 1)
    std = math.sqrt(var) if var > 0 else 1.0
    return [0.0 if math.isnan(v) else (v - mean) / std for v in values]


def spearman(x: list[float], y: list[float]) -> float:
    def ranks(values: list[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        result = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            avg_rank = (i + j) / 2 + 1
            for k in range(i, j + 1):
                result[order[k]] = avg_rank
            i = j + 1
        return result

    pairs = [(a, b) for a, b in zip(x, y) if not (math.isnan(a) or math.isnan(b))]
    if len(pairs) < 3:
        return float("nan")
    rx = ranks([p[0] for p in pairs])
    ry = ranks([p[1] for p in pairs])
    n = len(pairs)
    mx = sum(rx) / n
    my = sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den > 0 else float("nan")


def _global_midranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    n = len(values)
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def fast_clustered_bootstrap_rho(
    rows: list[dict[str, Any]],
    *,
    clusters_key: str = "day_pool",
    seed: int = 20260823,
    iterations: int = 10_000,
    alpha: float = 0.05,
) -> dict[str, float]:
    """Clustered bootstrap of the pooled rank correlation.

    Global midranks of score and cost are computed once; the bootstrap then
    resamples pool-days with replacement and evaluates the Pearson correlation
    on those fixed ranks, which equals the pooled Spearman estimator applied to
    any resample. Per-cluster partial sums make each iteration O(#clusters)
    instead of O(n log n), so the frozen B=10,000 runs in seconds.
    """

    xs = [float(row["score"]) for row in rows]
    ys = [float(row["as_cost_h60"]) for row in rows]
    rx = _global_midranks(xs)
    ry = _global_midranks(ys)

    clusters: dict[str, list[int]] = {}
    for index, row in enumerate(rows):
        clusters.setdefault(str(row[clusters_key]), []).append(index)

    names = sorted(clusters)

    def partials(indices: list[int]) -> tuple[float, ...]:
        sx = sy = sxx = syy = sxy = 0.0
        for i in indices:
            a, b = rx[i], ry[i]
            sx += a
            sy += b
            sxx += a * a
            syy += b * b
            sxy += a * b
        return float(len(indices)), sx, sy, sxx, syy, sxy

    per_cluster = {name: partials(clusters[name]) for name in names}

    def pearson(total: tuple[float, ...]) -> float:
        n, sx, sy, sxx, syy, sxy = total
        if n < 3:
            return float("nan")
        num = sxy - (sx * sy) / n
        den_x = sxx - (sx * sx) / n
        den_y = syy - (sy * sy) / n
        den = math.sqrt(max(den_x, 0.0) * max(den_y, 0.0))
        return num / den if den > 0 else float("nan")

    grand = [sum(per_cluster[c][i] for c in names) for i in range(6)]
    point = pearson(tuple(grand))

    rng = random.Random(seed)
    samples: list[float] = []
    m = len(names)
    for _ in range(iterations):
        tx = ty = txx = tyy = txy = tn = 0.0
        for _c in range(m):
            name = names[rng.randrange(m)]
            n_c, sx, sy, sxx, syy, sxy = per_cluster[name]
            tn += n_c
            tx += sx
            ty += sy
            txx += sxx
            tyy += syy
            txy += sxy
        value = pearson((tn, tx, ty, txx, tyy, txy))
        if not math.isnan(value):
            samples.append(value)
    samples.sort()

    def quantile(q: float) -> float:
        position = min(len(samples) - 1, max(0, int(q * len(samples))))
        return samples[position]

    lower = quantile(alpha / 2)
    upper = quantile(1 - alpha / 2)
    return {
        "rho": point,
        "ci_low": lower,
        "ci_high": upper,
        "excludes_zero": bool(lower > 0 or upper < 0),
        "n_clusters": len(names),
        "n_rows": len(rows),
    }


def clustered_bootstrap_rho(
    rows: list[dict[str, Any]],
    *,
    clusters_key: str = "day_pool",
    seed: int = 20260823,
    iterations: int = 10_000,
    alpha: float = 0.05,
) -> dict[str, float]:
    """Clustered bootstrap over pool-days for the score-vs-cost correlation."""

    return fast_clustered_bootstrap_rho(
        rows,
        clusters_key=clusters_key,
        seed=seed,
        iterations=iterations,
        alpha=alpha,
    )


def gate1_criteria(
    active_days: list[dict[str, Any]],
    windows_by_day: dict[str, list[float]],
    bootstrap: dict[str, float],
) -> dict[str, Any]:
    """Evaluate the three pre-registered bars (prereg section 5)."""

    total_active = len(active_days)
    positive = sum(1 for day in active_days if day["positive_as_cost"])
    c1_share = positive / total_active if total_active else float("nan")

    losses: list[float] = []
    top_decile_loss = 0.0
    all_windows: list[float] = []
    for day_windows in windows_by_day.values():
        all_windows.extend(day_windows)
    all_windows_sorted = sorted(all_windows, reverse=True)
    cutoff_index = max(1, math.ceil(0.1 * len(all_windows_sorted)))
    threshold = all_windows_sorted[cutoff_index - 1]
    for value in all_windows_sorted[:cutoff_index]:
        top_decile_loss += value
    total_positive_loss = sum(v for v in all_windows if v > 0)
    c2_share = (
        top_decile_loss / total_positive_loss if total_positive_loss > 0 else float("nan")
    )

    return {
        "c1_active_days": total_active,
        "c1_positive_days": positive,
        "c1_positive_share": c1_share,
        "c1_pass": bool(total_active > 0 and c1_share >= 0.70),
        "c2_top_decile_share": c2_share,
        "c2_pass": bool(c2_share >= 0.30),
        "c3_rho": bootstrap["rho"],
        "c3_ci": [bootstrap["ci_low"], bootstrap["ci_high"]],
        "c3_excludes_zero": bootstrap["excludes_zero"],
        "c3_abs_rho_ge_015": bool(abs(bootstrap["rho"]) >= 0.15),
        "c3_pass": bool(bootstrap["excludes_zero"] and abs(bootstrap["rho"]) >= 0.15),
        "gate1_pass": False,
    }
