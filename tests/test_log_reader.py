"""Headless tests for the LogReader QThread glue.

The plan assumed this layer could only be checked on hardware. It cannot be
checked on hardware *safely* — ``extract_logs`` destroys the device's logs — but
it does not need hardware at all: a fake link and an offscreen QApplication
exercise the real thread, the real signal connections and the real stop path.

Signals are received by a QObject created on the main thread, which is what
MainWindow does, so Qt delivers them queued across the thread boundary exactly
as it does in the running application. That is the part worth testing; the loop
logic itself is covered by ``test_reader.py`` without any Qt at all.
"""

import os
import time
from contextlib import contextmanager

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json

import pytest
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from ohme_log_viewer.auth import AuthError
from ohme_log_viewer.reader import COMMAND, COMMAND_ECHO_PREFIX, MARKER, LogReader
from ohme_log_viewer.serial_link import SerialLinkError

AUTH_REQUIRED = json.dumps({"result": "auth_required", "challenge": "a3f1c8" + "00" * 13})

#: Stand-in for the link's real 0.3 s read timeout. Short enough to keep the
#: tests quick, long enough that an idle reader does not spin on the CPU.
_FAKE_READ_TIMEOUT_S = 0.005

#: Ceiling for every wait in this module. Nothing here should come close to it;
#: it exists so a broken reader fails the test instead of hanging the suite.
_WAIT_TIMEOUT_S = 5.0


class FakeLink:
    """Scripted stand-in for SerialLink, read from the reader's thread.

    Each scripted entry is handed out once; once they run out ``read_line``
    reports a read timeout, which is what a quiet device looks like.
    """

    def __init__(self, lines=None, raise_on_read=None):
        self._lines = list(lines or [])
        self._raise_on_read = raise_on_read
        self.written: list[str] = []

    def write_line(self, text):
        self.written.append(text)

    def feed(self, line):
        """Queue another line mid-test. List ops are atomic, so no lock."""
        self._lines.append(line)

    def drained(self) -> bool:
        """True once the reader has consumed every queued line."""
        return not self._lines

    def read_line(self):
        if self._raise_on_read is not None:
            raise self._raise_on_read
        if self._lines:
            return self._lines.pop(0)
        time.sleep(_FAKE_READ_TIMEOUT_S)
        return None


class Collector(QObject):
    """Slots living on the main thread, so deliveries are genuinely queued."""

    def __init__(self):
        super().__init__()
        self.lines: list[str] = []
        self.errors: list[str] = []
        self.auth_failures: list[str] = []
        self.finished = False

    def on_line(self, text: str) -> None:
        self.lines.append(text)

    def on_error(self, message: str) -> None:
        self.errors.append(message)

    def on_auth_failed(self, message: str) -> None:
        self.auth_failures.append(message)

    def on_finished(self) -> None:
        self.finished = True


@pytest.fixture(scope="session")
def qapp():
    """The one QApplication. Reused if the process already made one."""
    return QApplication.instance() or QApplication([])


@contextmanager
def running(reader: LogReader):
    """Start *reader* and guarantee its thread is joined before the test ends.

    Leaving a QThread running past the end of a test aborts the interpreter, so
    the join is unconditional rather than an assertion the test body could skip.
    """
    reader.start()
    try:
        yield reader
    finally:
        reader.stop()
        reader.wait(int(_WAIT_TIMEOUT_S * 1000))


def pump_until(app, predicate, timeout_s: float = _WAIT_TIMEOUT_S) -> bool:
    """Run the event loop until *predicate* holds or the deadline passes."""
    deadline = time.monotonic() + timeout_s
    while True:
        app.processEvents()
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.002)


def test_started_thread_sends_the_command_and_emits_received_lines(qapp):
    link = FakeLink(["first", "second", MARKER])
    reader = LogReader(link)
    collector = Collector()
    reader.line_received.connect(collector.on_line)

    with running(reader):
        assert pump_until(qapp, lambda: len(collector.lines) >= 2), collector.lines

    assert collector.lines[:2] == ["first", "second"]
    assert MARKER not in collector.lines
    assert link.written[0] == COMMAND


def test_stop_ends_the_thread_promptly(qapp):
    # No marker is ever sent, so the reader stays in its read loop and only
    # leaves it because it was asked to.
    reader = LogReader(FakeLink())
    reader.start()
    try:
        assert pump_until(qapp, reader.isRunning), "thread never started"
        start = time.monotonic()
        reader.stop()
        stopped = reader.wait(int(_WAIT_TIMEOUT_S * 1000))
        elapsed = time.monotonic() - start
    finally:
        reader.stop()
        reader.wait(int(_WAIT_TIMEOUT_S * 1000))

    assert stopped, "thread did not exit after stop()"
    assert not reader.isRunning()
    # Generous next to the 5 s ceiling, tight next to a reader that only
    # notices interruption between whole extract cycles.
    assert elapsed < 1.0, elapsed


def test_link_error_reaches_the_error_signal(qapp):
    reader = LogReader(FakeLink(raise_on_read=SerialLinkError("port went away")))
    collector = Collector()
    reader.error.connect(collector.on_error)
    reader.finished.connect(collector.on_finished)

    with running(reader):
        assert pump_until(qapp, lambda: collector.errors != []), "no error emitted"
        assert pump_until(qapp, lambda: collector.finished), "thread did not finish"

    assert collector.errors == ["port went away"]
    assert not reader.isRunning()


def test_a_challenge_from_the_device_reaches_the_authenticator(qapp):
    class StubAuthenticator:
        def __init__(self):
            self.challenges = []

        def authenticate(self, link, challenge, emit, should_stop):
            self.challenges.append(challenge)
            emit("[auth] session authenticated")
            return True

    link = FakeLink([AUTH_REQUIRED, "after auth", MARKER])
    authenticator = StubAuthenticator()
    reader = LogReader(link, authenticator=authenticator)
    collector = Collector()
    reader.line_received.connect(collector.on_line)

    with running(reader):
        assert pump_until(qapp, lambda: "after auth" in collector.lines), collector.lines

    assert authenticator.challenges == ["a3f1c8" + "00" * 13]
    assert link.written[:2] == [COMMAND, COMMAND]
    assert AUTH_REQUIRED not in collector.lines


def failing_reader(exc):
    """A reader whose first challenge raises *exc* out of the authenticator."""

    class RefusingAuthenticator:
        def authenticate(self, link, challenge, emit, should_stop):
            raise exc

    return LogReader(FakeLink([AUTH_REQUIRED]), authenticator=RefusingAuthenticator())


def test_a_refused_authentication_reaches_the_auth_failed_signal(qapp):
    # Refusals travel separately from ordinary failures so the window can stop
    # retrying: no amount of waiting fixes a rejected credential.
    reader = failing_reader(AuthError("backend would not sign the challenge"))
    collector = Collector()
    reader.error.connect(collector.on_error)
    reader.auth_failed.connect(collector.on_auth_failed)
    reader.finished.connect(collector.on_finished)

    with running(reader):
        assert pump_until(qapp, lambda: collector.auth_failures != []), "no refusal emitted"
        assert pump_until(qapp, lambda: collector.finished), "thread did not finish"

    assert collector.auth_failures == ["backend would not sign the challenge"]
    assert collector.errors == []
    assert not reader.isRunning()


def test_a_transient_authentication_failure_reaches_the_error_signal(qapp):
    # A device that did not answer in time is worth another attempt, so it
    # travels the same channel as a link failure.
    reader = failing_reader(AuthError("device did not answer in time", retryable=True))
    collector = Collector()
    reader.error.connect(collector.on_error)
    reader.auth_failed.connect(collector.on_auth_failed)

    with running(reader):
        assert pump_until(qapp, lambda: collector.errors != []), "no error emitted"

    assert collector.errors == ["device did not answer in time"]
    assert collector.auth_failures == []


def test_no_lines_are_emitted_after_the_signal_is_disconnected(qapp):
    # The path MainWindow relies on when it parks a reader it could not stop:
    # a disconnected reader must be inert even though it is still running. No
    # marker is scripted, so the reader stays inside one cycle and picks up a
    # line fed to it within a read timeout rather than after a cycle delay.
    link = FakeLink(["before"])
    reader = LogReader(link)
    collector = Collector()
    reader.line_received.connect(collector.on_line)

    with running(reader):
        assert pump_until(qapp, lambda: collector.lines == ["before"]), collector.lines
        reader.line_received.disconnect(collector.on_line)

        link.feed("after")
        # The reader really did read it — it just had nowhere to deliver it.
        assert pump_until(qapp, link.drained), "reader never consumed the fed line"
        assert not pump_until(qapp, lambda: "after" in collector.lines, timeout_s=0.2)

    assert collector.lines == ["before"]


# ── user commands ────────────────────────────────────────────────────────


def test_a_user_command_is_sent_after_the_cycle_and_its_reply_emitted(qapp):
    link = FakeLink(["log line", MARKER])
    reader = LogReader(link)
    collector = Collector()
    reader.line_received.connect(collector.on_line)

    with running(reader):
        reader.send_command("get_cli_status")
        assert pump_until(qapp, lambda: "get_cli_status" in link.written), link.written
        link.feed('{"result":"ok"}')
        assert pump_until(qapp, lambda: '{"result":"ok"}' in collector.lines), collector.lines

    # Never interleaved with an extraction: written only once the marker ended it.
    assert link.written[:2] == [COMMAND, "get_cli_status"]
    echo = COMMAND_ECHO_PREFIX + "get_cli_status"
    assert collector.lines.index(echo) < collector.lines.index('{"result":"ok"}')


def test_a_reply_during_the_gap_arrives_before_the_next_cycle(qapp):
    # The gap reads rather than sleeps, so a reply is not held for a whole
    # cycle delay. Only one extract_logs has been written when it shows up.
    link = FakeLink([MARKER])
    reader = LogReader(link)
    collector = Collector()
    reader.line_received.connect(collector.on_line)

    with running(reader):
        assert pump_until(qapp, link.drained), "first cycle never completed"
        link.feed("reply")
        assert pump_until(qapp, lambda: "reply" in collector.lines, timeout_s=0.5)
        assert link.written.count(COMMAND) == 1
