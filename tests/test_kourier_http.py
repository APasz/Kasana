from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from kasana.kourier.http import RequestPacer, retry_after_seconds, shared_request_pacer


@dataclass
class _PacerClock:
    now_seconds: float = 0.0
    delays: list[float] = field(default_factory=list)

    def now(self) -> float:
        return self.now_seconds

    async def sleep(self, delay_seconds: float) -> None:
        self.delays.append(delay_seconds)
        self.now_seconds += delay_seconds


async def test_request_pacer_spaces_requests_and_honours_cooldowns() -> None:
    clock = _PacerClock()
    pacer = RequestPacer(4.0, sleeper=clock.sleep, clock=clock.now)

    await pacer.wait()
    await pacer.wait()
    await pacer.defer(2.0)
    await pacer.wait()

    assert clock.delays == pytest.approx([0.25, 2.0])


async def test_request_pacer_tightens_to_the_safest_rate() -> None:
    clock = _PacerClock()
    pacer = RequestPacer(10.0, sleeper=clock.sleep, clock=clock.now)

    await pacer.wait()
    pacer.restrict_to(4.0)
    await pacer.wait()

    assert clock.delays == pytest.approx([0.25])


@pytest.mark.parametrize("requests_per_second", (0.0, -1.0, 5e-324, float("inf"), float("nan")))
def test_request_pacer_rejects_non_finite_or_non_positive_rates(requests_per_second: float) -> None:
    with pytest.raises(ValueError, match="finite positive"):
        RequestPacer(requests_per_second)


async def test_request_pacer_rejects_non_finite_cooldowns() -> None:
    pacer = RequestPacer(1.0)

    with pytest.raises(ValueError, match="finite"):
        await pacer.defer(float("inf"))


def test_retry_after_accepts_standard_delay_seconds_only() -> None:
    def clock() -> datetime:
        return datetime(2015, 10, 21, 7, 27, 58, tzinfo=UTC)

    assert retry_after_seconds({"Retry-After": " 3 "}, clock) == 3.0
    assert retry_after_seconds({"retry-after": "3"}, clock) == 3.0
    assert retry_after_seconds({"Retry-After": "0.5"}, clock) is None
    assert retry_after_seconds({"Retry-After": "Infinity"}, clock) is None


async def test_shared_request_pacer_reuses_the_provider_controller() -> None:
    fast = shared_request_pacer("test-kourier-http", 10.0)
    slow = shared_request_pacer("test-kourier-http", 4.0)

    assert fast is slow
