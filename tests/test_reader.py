import pytest

from ohme_log_viewer.reader import COMMAND, MARKER, CycleTimeout, run_extract_cycle


class FakeLink:
    """Scripted stand-in for SerialLink. A None entry models a read timeout."""

    def __init__(self, lines=None, raise_on_read=None):
        self._lines = list(lines or [])
        self._raise_on_read = raise_on_read
        self.written: list[str] = []

    def write_line(self, text):
        self.written.append(text)

    def read_line(self):
        if self._raise_on_read is not None:
            raise self._raise_on_read
        if not self._lines:
            return None
        return self._lines.pop(0)


def collect(link, should_stop=lambda: False, **kwargs):
    emitted: list[str] = []
    completed = run_extract_cycle(link, should_stop, emitted.append, **kwargs)
    return completed, emitted


def test_cycle_sends_the_extract_command():
    link = FakeLink([MARKER])
    run_extract_cycle(link, lambda: False, lambda _line: None)
    assert link.written == [COMMAND]


def test_cycle_emits_lines_until_the_marker():
    link = FakeLink(["first", "second", MARKER, "after"])
    completed, emitted = collect(link)
    assert completed is True
    assert emitted == ["first", "second"]


def test_the_marker_itself_is_not_emitted():
    completed, emitted = collect(FakeLink([MARKER]))
    assert completed is True
    assert emitted == []


def test_marker_is_matched_after_stripping():
    completed, emitted = collect(FakeLink([f"  {MARKER}  "]))
    assert completed is True
    assert emitted == []


def test_read_timeouts_do_not_end_the_cycle():
    link = FakeLink([None, "line", None, MARKER])
    completed, emitted = collect(link)
    assert completed is True
    assert emitted == ["line"]


def test_stop_request_ends_the_cycle_without_completing():
    calls = {"n": 0}

    def should_stop():
        calls["n"] += 1
        return calls["n"] > 2

    completed, emitted = collect(FakeLink(["a", "b", "c", MARKER]), should_stop=should_stop)
    assert completed is False
    assert emitted == ["a", "b"]


def test_missing_marker_raises_cycle_timeout():
    clock = iter([0.0, 0.0, 1.0, 500.0])
    link = FakeLink([None, None, None, None])
    with pytest.raises(CycleTimeout):
        run_extract_cycle(
            link, lambda: False, lambda _line: None, timeout_s=120.0, now=lambda: next(clock)
        )


def test_read_failure_propagates():
    link = FakeLink(raise_on_read=OSError("boom"))
    with pytest.raises(OSError, match="boom"):
        run_extract_cycle(link, lambda: False, lambda _line: None)
