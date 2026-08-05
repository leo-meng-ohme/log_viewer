import json

import pytest

from ohme_log_viewer.auth import AuthError
from ohme_log_viewer.reader import COMMAND, MARKER, CycleTimeout, run_extract_cycle

CHALLENGE = "a3f1c8" + "00" * 13
AUTH_REQUIRED = json.dumps({"result": "auth_required", "challenge": CHALLENGE})


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


class FakeAuthenticator:
    """Stands in for DeviceAuthenticator; records the challenges it is given."""

    def __init__(self, on_authenticate=None, result=True):
        self.challenges: list[str] = []
        self._on_authenticate = on_authenticate
        self._result = result

    def authenticate(self, link, challenge, emit, should_stop=lambda: False):
        self.challenges.append(challenge)
        if self._on_authenticate is not None:
            self._on_authenticate(link, emit)
        return self._result


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


def test_marker_is_matched_through_chunk_padding():
    """The previous chunk's NUL padding runs straight into the sentinel with no
    newline between them, so the sentinel arrives with the padding attached."""
    completed, emitted = collect(FakeLink(["\x00" * 200 + MARKER]))
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


# ── authentication ───────────────────────────────────────────────────────


def test_an_auth_challenge_triggers_the_handshake():
    link = FakeLink([AUTH_REQUIRED, "line", MARKER])
    auth = FakeAuthenticator()
    completed, _ = collect(link, authenticator=auth)
    assert completed is True
    assert auth.challenges == [CHALLENGE]


def test_the_command_is_resent_once_the_session_is_authenticated():
    link = FakeLink([AUTH_REQUIRED, "line", MARKER])
    collect(link, authenticator=FakeAuthenticator())
    assert link.written == [COMMAND, COMMAND]


def test_the_challenge_json_is_not_shown_as_a_log_line():
    link = FakeLink([AUTH_REQUIRED, "line", MARKER])
    _, emitted = collect(link, authenticator=FakeAuthenticator())
    assert emitted == ["line"]


def test_the_cycle_deadline_restarts_after_the_handshake():
    """ECDSA verification on the device costs seconds; that must not time the
    cycle out when the whole extraction had barely begun."""
    clock = [0.0]

    def now():
        return clock[0]

    def slow_handshake(_link, _emit):
        clock[0] += 90.0

    link = FakeLink([AUTH_REQUIRED, "line", MARKER])
    completed, emitted = collect(
        link,
        authenticator=FakeAuthenticator(slow_handshake),
        timeout_s=100.0,
        now=now,
    )
    assert completed is True
    assert emitted == ["line"]


def test_a_device_that_keeps_challenging_gives_up_rather_than_looping():
    link = FakeLink([AUTH_REQUIRED] * 5 + [MARKER])
    with pytest.raises(AuthError, match="authentication"):
        collect(link, authenticator=FakeAuthenticator())


def test_a_handshake_abandoned_by_a_stop_request_ends_the_cycle():
    link = FakeLink([AUTH_REQUIRED, "line", MARKER])
    auth = FakeAuthenticator(result=False)
    completed, emitted = collect(link, authenticator=auth)
    assert completed is False
    assert emitted == []
    assert link.written == [COMMAND]  # not resent


def test_a_challenge_with_no_authenticator_is_an_auth_error():
    link = FakeLink([AUTH_REQUIRED, MARKER])
    with pytest.raises(AuthError):
        collect(link)
