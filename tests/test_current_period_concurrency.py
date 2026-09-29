from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Barrier, Event, Lock

import pytest

from mcp_server.tools import current_period


@pytest.mark.parametrize("refresh", [False, True])
def test_overlapping_requests_share_one_measurement_run(monkeypatch, refresh):
    calls = Counter()
    lock = Lock()
    started = Event()
    release = Event()
    ready = Barrier(3)
    joined = Event()

    class ObservedFuture(Future):
        def result(self, timeout=None):
            joined.set()
            return super().result(timeout)

    monkeypatch.setattr(current_period, "Future", ObservedFuture)

    def measure(metric):
        with lock:
            calls[metric] += 1
        started.set()
        assert release.wait(5)
        return {"metric_id": metric, "category": None}

    monkeypatch.setattr(current_period, "assess", measure)
    current_period.assess_all.cache_clear()

    def request():
        ready.wait()
        return current_period.assess_all(refresh=refresh)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(request) for _ in range(2)]
        ready.wait()
        assert started.wait(5)
        assert joined.wait(5)
        release.set()
        results = [future.result(timeout=5) for future in futures]
    assert max(calls.values()) == 1
    assert results[0] is results[1]
    current_period.assess_all.cache_clear()


def test_failed_run_releases_refresh_and_cache_can_be_retried(monkeypatch):
    current_period.assess_all.cache_clear()
    calls = []

    def compute():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("unexpected calculation failure")
        return {"status": "ok", "items": []}

    monkeypatch.setattr(current_period, "_compute_all", compute)
    with pytest.raises(RuntimeError):
        current_period.assess_all()
    result = current_period.assess_all()
    assert current_period.assess_all() is result
    assert len(calls) == 2
    current_period.assess_all(refresh=True)
    assert len(calls) == 3
    current_period.assess_all.cache_clear()
