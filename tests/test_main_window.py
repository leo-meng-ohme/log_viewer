"""Headless tests for what the window itself decides.

Which collaborators a session is given, and what reaches the buffer, because
that is where a fix can be complete in the reader or the panel and still never
run in the application. One test follows a tag filter change all the way from a
click in the panel to the lines left in view, since that path crosses three
components and the 100 ms flush that batches them. Widget behaviour in isolation
belongs to ``test_tag_panel.py``.
"""

import json
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from ohme_log_viewer.auth import DeviceAuthenticator
from ohme_log_viewer.reader import MARKER
from ohme_log_viewer.ui.main_window import MainWindow

CHALLENGE = "a3f1c8" + "00" * 13
AUTH_REQUIRED = json.dumps({"result": "auth_required", "challenge": CHALLENGE})

_WAIT_TIMEOUT_S = 5.0
_FAKE_READ_TIMEOUT_S = 0.005


class FakeLink:
    """Stands in for SerialLink across a whole session."""

    def __init__(self, lines=None):
        self._lines = list(lines or [])
        self.written: list[str] = []
        self.opened: list[str] = []
        self.closed = False

    def open(self, device, baud=115200):
        self.opened.append(device)

    def close(self):
        self.closed = True

    def write_line(self, text):
        self.written.append(text)

    def read_line(self):
        if self._lines:
            return self._lines.pop(0)
        time.sleep(_FAKE_READ_TIMEOUT_S)
        return None


class StubAuthenticator:
    def __init__(self):
        self.challenges: list[str] = []

    def authenticate(self, link, challenge, emit, should_stop):
        self.challenges.append(challenge)
        emit("[auth] session authenticated")
        return True


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def pump_until(app, predicate, timeout_s: float = _WAIT_TIMEOUT_S) -> bool:
    deadline = time.monotonic() + timeout_s
    while True:
        app.processEvents()
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.002)


def connected(qapp, link, authenticator):
    """Build a window wired to *link*, select a port, and press Connect."""
    window = MainWindow(link_factory=lambda: link, authenticator_factory=lambda: authenticator)
    window._port_combo.addItem("fake port", "/dev/fake")
    window._on_connect_clicked()
    return window


def raw_lines(window):
    return [line.raw for line in window._buffer.visible()]


def test_hiding_a_selection_of_tags_removes_their_lines_from_the_view(qapp):
    """The whole path in one go: a propagated tick in the panel reaches the
    buffer's filter and the pane, through the 100 ms flush that batches it."""
    lines = [
        "I/MODEM [t] modem line",
        "I/OCPP  [t] ocpp line",
        "I/RCD   [t] rcd line",
        MARKER,
    ]
    window = connected(qapp, FakeLink(lines), StubAuthenticator())
    try:
        panel = window._tag_panel
        # The buffer fills on the reader's signal, but the panel's rows appear
        # on the next 100 ms flush — so wait for the rows, not the lines.
        assert pump_until(qapp, lambda: {"MODEM", "OCPP", "RCD"} <= set(panel._items)), sorted(
            panel._items
        )
        assert len(raw_lines(window)) == 3, raw_lines(window)

        panel._list.clearSelection()
        for tag in ("MODEM", "OCPP"):
            panel._items[tag].setSelected(True)

        # One tick on one of the two selected rows.
        panel._items["MODEM"].setCheckState(Qt.CheckState.Unchecked)

        assert pump_until(qapp, lambda: len(raw_lines(window)) == 1), raw_lines(window)
        assert raw_lines(window) == ["I/RCD   [t] rcd line"]
    finally:
        window.close()


def test_a_session_answers_a_challenge_through_its_authenticator(qapp):
    link = FakeLink([AUTH_REQUIRED, "I/MODEM [t] hello", MARKER])
    authenticator = StubAuthenticator()
    window = connected(qapp, link, authenticator)
    try:
        assert pump_until(qapp, lambda: authenticator.challenges != []), "challenge never handled"
        assert pump_until(qapp, lambda: "I/MODEM [t] hello" in raw_lines(window)), raw_lines(window)
    finally:
        window.close()

    assert authenticator.challenges == [CHALLENGE]


def test_the_challenge_json_never_appears_in_the_log(qapp):
    link = FakeLink([AUTH_REQUIRED, "I/MODEM [t] hello", MARKER])
    authenticator = StubAuthenticator()
    window = connected(qapp, link, authenticator)
    try:
        assert pump_until(qapp, lambda: "I/MODEM [t] hello" in raw_lines(window)), raw_lines(window)
        assert not any("auth_required" in raw for raw in raw_lines(window))
    finally:
        window.close()


def test_a_real_session_is_given_a_real_authenticator(qapp):
    """The default the application actually runs with, not the injected stub."""
    link = FakeLink([MARKER])
    window = MainWindow(link_factory=lambda: link)
    window._port_combo.addItem("fake port", "/dev/fake")
    try:
        window._on_connect_clicked()
        assert isinstance(window._reader._authenticator, DeviceAuthenticator)
    finally:
        window.close()


def test_each_session_gets_its_own_authenticator(qapp):
    """Charger ID and token are per-connection state; a second Connect after a
    device swap must not reuse the first device's identity."""
    built = []

    def factory():
        built.append(StubAuthenticator())
        return built[-1]

    window = MainWindow(link_factory=lambda: FakeLink([MARKER]), authenticator_factory=factory)
    window._port_combo.addItem("fake port", "/dev/fake")
    try:
        window._on_connect_clicked()
        window._on_connect_clicked()  # disconnect
        window._on_connect_clicked()  # connect again
    finally:
        window.close()

    assert len(built) == 2
    assert built[0] is not built[1]
