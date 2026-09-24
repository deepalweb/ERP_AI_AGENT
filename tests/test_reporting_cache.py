"""Phase 5A: reporting.ttl_cache -- the shared TTL cache used by Home's slow
supplementary-display endpoints (dashboard/operations/channels/stock-alerts/
investigate). One real check per behavior it promises: a hit within the
window skips the wrapped call, expiry forces a recompute, and different
args never collide."""
import time

from backend.app.db import reporting


def test_a_second_call_within_the_window_reuses_the_cached_result_sync():
    calls = []

    @reporting.ttl_cache(seconds=60)
    def fn(x):
        calls.append(x)
        return x * 2

    assert fn(3) == 6
    assert fn(3) == 6
    assert calls == [3]  # only computed once


def test_different_args_get_independent_cache_entries():
    calls = []

    @reporting.ttl_cache(seconds=60)
    def fn(x):
        calls.append(x)
        return x * 2

    fn(1)
    fn(2)
    fn(1)
    assert calls == [1, 2]


def test_expiry_forces_a_recompute():
    calls = []

    @reporting.ttl_cache(seconds=0.05)
    def fn():
        calls.append(1)
        return "v"

    fn()
    time.sleep(0.1)
    fn()
    assert calls == [1, 1]


def test_cache_clear_forces_the_next_call_to_recompute():
    calls = []

    @reporting.ttl_cache(seconds=60)
    def fn():
        calls.append(1)
        return "v"

    fn()
    fn.cache_clear()
    fn()
    assert calls == [1, 1]


async def test_async_function_is_cached_the_same_way():
    calls = []

    @reporting.ttl_cache(seconds=60)
    async def fn(x):
        calls.append(x)
        return x + 1

    assert await fn(5) == 6
    assert await fn(5) == 6
    assert calls == [5]
