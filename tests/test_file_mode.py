"""Headless tests for file-view mode.

Loading a saved log file uses the same parse-and-buffer pipeline as a live
session, so these tests assert through the buffer and the toolbar state: what
a loaded file leaves in view, and that the two sources — file and board — are
mutually exclusive.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication
from test_main_window import FakeLink, StubAuthenticator, pump_until, raw_lines

from ohme_log_viewer.ui.main_window import MainWindow


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def window_with_fake_port(qapp, link=None):
    window = MainWindow(
        link_factory=lambda: link or FakeLink(), authenticator_factory=StubAuthenticator
    )
    window._port_combo.clear()  # the constructor enumerated the host's real ports
    window._port_combo.addItem("fake port", "/dev/fake")
    return window


def write_log(tmp_path, name, lines):
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def test_loading_a_file_shows_its_lines_and_tags(qapp, tmp_path):
    path = write_log(
        tmp_path,
        "device.txt",
        ["I/MODEM [t] modem line", "E/RCD   [t] rcd line", "not a log line"],
    )
    window = window_with_fake_port(qapp)
    try:
        window._load_file(path)
        assert pump_until(qapp, lambda: len(raw_lines(window)) == 3), raw_lines(window)
        assert raw_lines(window) == [
            "I/MODEM [t] modem line",
            "E/RCD   [t] rcd line",
            "not a log line",
        ]
        assert set(window._buffer.tags()) == {"MODEM", "RCD", "(unparsed)"}
    finally:
        window.close()


def test_file_mode_cannot_connect_to_the_board(qapp, tmp_path):
    path = write_log(tmp_path, "device.txt", ["I/MODEM [t] hello"])
    window = window_with_fake_port(qapp)
    try:
        window._load_file(path)
        assert not window._connect_btn.isEnabled()
        assert not window._port_combo.isEnabled()
        assert not window._refresh_btn.isEnabled()
        assert window._open_file_btn.text() == "Close File"
        assert "device.txt" in window.statusBar().currentMessage()
    finally:
        window.close()


def test_closing_the_file_returns_to_disconnected_with_an_empty_view(qapp, tmp_path):
    path = write_log(tmp_path, "device.txt", ["I/MODEM [t] hello"])
    window = window_with_fake_port(qapp)
    try:
        window._load_file(path)
        window._open_file_btn.click()  # Close File

        assert window._open_file_btn.text() == "Open File…"
        assert window._connect_btn.isEnabled()
        assert window._port_combo.isEnabled()
        assert window._refresh_btn.isEnabled()
        assert raw_lines(window) == []
        assert window._buffer.tags() == []
        assert pump_until(qapp, lambda: window._tag_panel._items == {}), sorted(
            window._tag_panel._items
        )
        assert pump_until(qapp, lambda: window._pane.line_count() == 0)
        assert "Disconnected" in window.statusBar().currentMessage()
    finally:
        window.close()


def test_loading_a_file_replaces_what_was_in_view(qapp, tmp_path):
    """Lines and tags from a previous source must not bleed into the file view."""
    first = write_log(tmp_path, "first.txt", ["I/MODEM [t] from first"])
    second = write_log(tmp_path, "second.txt", ["I/RCD   [t] from second"])
    window = window_with_fake_port(qapp)
    try:
        window._load_file(first)
        window._load_file(second)
        assert raw_lines(window) == ["I/RCD   [t] from second"]
        assert window._buffer.tags() == ["RCD"]
    finally:
        window.close()


def test_an_unreadable_file_reports_and_leaves_the_view_alone(qapp, tmp_path):
    path = write_log(tmp_path, "good.txt", ["I/MODEM [t] hello"])
    window = window_with_fake_port(qapp)
    try:
        window._load_file(path)
        window._load_file(str(tmp_path / "missing.txt"))

        assert "missing.txt" in (window._last_error or "")
        # Still viewing the file that did load, board controls still off.
        assert window._file_path == path
        assert window._open_file_btn.text() == "Close File"
        assert not window._connect_btn.isEnabled()
        assert "I/MODEM [t] hello" in raw_lines(window)
    finally:
        window.close()


def test_a_failed_load_outside_file_mode_leaves_connecting_enabled(qapp, tmp_path):
    window = window_with_fake_port(qapp)
    try:
        window._load_file(str(tmp_path / "missing.txt"))
        assert "missing.txt" in (window._last_error or "")
        assert window._file_path is None
        assert window._open_file_btn.text() == "Open File…"
        assert window._connect_btn.isEnabled()
    finally:
        window.close()


def test_opening_a_file_is_disabled_while_a_session_is_live(qapp):
    window = window_with_fake_port(qapp, FakeLink(["I/MODEM [t] hello"]))
    try:
        window._on_connect_clicked()
        assert not window._open_file_btn.isEnabled()

        window._on_connect_clicked()  # Disconnect
        assert window._open_file_btn.isEnabled()
    finally:
        window.close()


def test_a_wider_tag_arriving_later_realigns_the_lines_already_shown(qapp):
    window = window_with_fake_port(qapp)
    try:
        window._on_line_received("I/OTA [t] first")
        window._flush()
        window._on_line_received("W/WIFI_MGR [t] second")
        window._flush()
        assert window._pane._view.toPlainText().splitlines() == [
            "I/OTA      [t] first",
            "W/WIFI_MGR [t] second",
        ]
    finally:
        window.close()
