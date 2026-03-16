from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from threading import Lock


@dataclass(frozen=True)
class MetricsSnapshot:
    counters: dict[str, int]
    averages: dict[str, float]


class RuntimeMetrics:
    def __init__(self) -> None:
        self._lock = Lock()
        self._counters: Counter[str] = Counter()
        self._latency_totals: defaultdict[str, float] = defaultdict(float)
        self._latency_counts: Counter[str] = Counter()

    def increment(self, name: str, amount: int = 1) -> None:
        metric_name = str(name or "").strip()
        if not metric_name or amount == 0:
            return
        with self._lock:
            self._counters[metric_name] += int(amount)

    def observe_latency(self, name: str, duration_ms: float) -> None:
        metric_name = str(name or "").strip()
        if not metric_name:
            return
        value = max(0.0, float(duration_ms))
        with self._lock:
            self._latency_totals[metric_name] += value
            self._latency_counts[metric_name] += 1

    def snapshot(self) -> MetricsSnapshot:
        with self._lock:
            averages = {
                name: (self._latency_totals[name] / self._latency_counts[name])
                for name in self._latency_counts
                if self._latency_counts[name] > 0
            }
            return MetricsSnapshot(counters=dict(self._counters), averages=averages)


@lru_cache(maxsize=1)
def get_runtime_metrics() -> RuntimeMetrics:
    return RuntimeMetrics()


def reset_runtime_metrics() -> None:
    get_runtime_metrics.cache_clear()
