"""The reporter's cycle timing must survive a wall-clock correction.

`_wait_for_next_cycle` is the whole of the reporter's pacing: whatever it
sleeps is how long the next reconciliation pass waits, and a pass that never
comes leaves terminal jobs unfinalised in the portal. The regression these
tests pin is a wall-clock `time.time()` measurement, where an NTP step
backwards makes the measured cycle negative and the sleep grows by the size of
the step.
"""

from middleware import job_reporter


class FakeClock:
    """A monotonic clock and a wall clock that disagree, plus a sleep log."""

    def __init__(self, monotonic: float, wall: float):
        self._monotonic = monotonic
        self._wall = wall
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self._monotonic

    def time(self) -> float:
        return self._wall

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self._monotonic += seconds
        self._wall += seconds


def _install(monkeypatch, clock: FakeClock) -> None:
    monkeypatch.setattr(job_reporter.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(job_reporter.time, "time", clock.time)
    monkeypatch.setattr(job_reporter.time, "sleep", clock.sleep)
    monkeypatch.setattr(job_reporter, "_should_terminate", False)


def test_sleeps_out_the_remainder_of_the_cycle(monkeypatch):
    # A 4-second pass out of a 10-second cycle leaves 6 seconds.
    clock = FakeClock(monotonic=1_000.0, wall=1_700_000_000.0)
    _install(monkeypatch, clock)

    job_reporter._wait_for_next_cycle(loop_start=996.0, sleep_time=10)

    assert clock.slept == [1] * 6


def test_a_backwards_wall_clock_step_does_not_extend_the_sleep(monkeypatch):
    # The wall clock has jumped an hour into the past since the cycle began —
    # the correction a VM gets shortly after boot. Measured on the wall clock
    # the pass would look like it took -3596 seconds, and the reporter would
    # sleep 3600 of them instead of 6.
    clock = FakeClock(monotonic=1_000.0, wall=1_700_000_000.0 - 3_600)
    _install(monkeypatch, clock)

    job_reporter._wait_for_next_cycle(loop_start=996.0, sleep_time=10)

    assert clock.slept == [1] * 6


def test_a_pass_longer_than_the_cycle_does_not_sleep(monkeypatch):
    clock = FakeClock(monotonic=1_050.0, wall=1_700_000_000.0)
    _install(monkeypatch, clock)

    job_reporter._wait_for_next_cycle(loop_start=1_000.0, sleep_time=10)

    assert clock.slept == []


def test_shutdown_interrupts_the_sleep(monkeypatch):
    clock = FakeClock(monotonic=1_000.0, wall=1_700_000_000.0)
    _install(monkeypatch, clock)

    real_sleep = clock.sleep

    def sleep_then_terminate(seconds: float) -> None:
        real_sleep(seconds)
        monkeypatch.setattr(job_reporter, "_should_terminate", True)

    monkeypatch.setattr(job_reporter.time, "sleep", sleep_then_terminate)

    job_reporter._wait_for_next_cycle(loop_start=1_000.0, sleep_time=600)

    # One second in, the signal handler has fired; the remaining 599 are not
    # slept out.
    assert clock.slept == [1]
