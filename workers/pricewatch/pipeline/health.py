"""Run health checks: detect broken scrapers before they corrupt prices."""
from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Literal


@dataclass
class RunStats:
    items_seen: int = 0
    items_upserted: int = 0
    items_failed: int = 0
    new_products: int = 0
    matched_products: int = 0
    unmatched: int = 0
    needs_review: int = 0
    price_changes: int = 0
    stale_marked: int = 0
    quantity_unparsed: int = 0
    failure_reasons: Counter = field(default_factory=Counter)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["failure_reasons"] = dict(self.failure_reasons)
        return d


@dataclass
class Alert:
    code: str
    severity: Literal["warning", "critical"]
    message: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class HealthPolicy:
    drop_threshold: float = 0.40          # >40% fewer items than the usual run
    min_history: int = 3
    max_failure_rate: float = 0.20        # >20% items failing to parse = layout/API changed
    max_unparsed_qty_rate: float = 0.35
    max_price_change_rate: float = 0.60   # most prices changed at once = currency switch / parsing bug


def evaluate(stats: RunStats, history: list[int], policy: HealthPolicy = HealthPolicy()) -> tuple[list[Alert], int | None]:
    alerts: list[Alert] = []
    baseline = int(statistics.median(history)) if len(history) >= policy.min_history else None
    seen = stats.items_seen

    if seen == 0:
        alerts.append(Alert("empty_run", "critical", "Adapter returned no products"))
    elif baseline and seen < baseline * (1 - policy.drop_threshold):
        alerts.append(Alert("item_count_drop", "critical",
                            f"Only {seen} products vs usual {baseline} (-{100 - 100 * seen // baseline}%)"))
    if seen:
        fail_rate = stats.items_failed / seen
        if fail_rate > policy.max_failure_rate:
            top = ", ".join(f"{k}×{v}" for k, v in stats.failure_reasons.most_common(3))
            alerts.append(Alert("schema_drift", "critical",
                                f"{fail_rate:.0%} of products failed to parse ({top}) — source format may have changed"))
        ok = seen - stats.items_failed
        if ok and stats.quantity_unparsed / ok > policy.max_unparsed_qty_rate:
            alerts.append(Alert("size_parse_degraded", "warning",
                                f"{stats.quantity_unparsed}/{ok} products without a readable size"))
        if ok > 20 and stats.price_changes / ok > policy.max_price_change_rate:
            alerts.append(Alert("mass_price_change", "warning",
                                f"{stats.price_changes}/{ok} prices changed in one run — check currency/format"))
    return alerts, baseline


def is_critical(alerts: list[Alert]) -> bool:
    return any(a.severity == "critical" for a in alerts)
