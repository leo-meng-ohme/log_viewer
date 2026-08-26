"""Headless tests for saving the buffer to a file.

Saving is a read-only snapshot of the buffer: every stored line's original
text, all tags regardless of filter state, oldest first. The written file
must round-trip through Open File, which runs it through the same parser.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication
from test_main_window import FakeLink, StubAuthenticator, raw_lines

from ohme_log_viewer.ui.main_window import MainWindow

LINES = [
    "I/MODEM [t] modem line",
    "E/RCD   [t] rcd line",
    "not a log line",
]


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def window_with_lines(qapp, lines=LINES):
    window = MainWindow(link_factory=FakeLink, authenticator_factory=StubAuthenticator)
    for raw in lines:
        window._on_line_received(raw)
    return window


def test_the_toolbar_offers_save(qapp):
    window = window_with_lines(qapp, lines=[])
    try:
        assert window._save_btn.text() == "Save Logs…"
    finally:
        window.close()


def test_save_writes_every_buffered_line_verbatim(qapp, tmp_path):
    window = window_with_lines(qapp)
    path = tmp_path / "out.txt"
    try:
        window._save_to(str(path))
    finally:
        window.close()
    assert path.read_text(encoding="utf-8") == "".join(f"{raw}\n" for raw in LINES)


def test_save_ignores_the_tag_filter(qapp, tmp_path):
    window = window_with_lines(qapp)
    path = tmp_path / "out.txt"
    try:
        window._buffer.set_tag_enabled("MODEM", False)
        window._save_to(str(path))
    finally:
        window.close()
    assert "I/MODEM [t] modem line" in path.read_text(encoding="utf-8")


def test_a_saved_file_round_trips_through_open_file(qapp, tmp_path):
    path = tmp_path / "out.txt"
    window = window_with_lines(qapp)
    try:
        window._save_to(str(path))
        window._load_file(str(path))
        assert raw_lines(window) == LINES
        assert set(window._buffer.tags()) == {"MODEM", "RCD", "(unparsed)"}
    finally:
        window.close()


def test_saving_an_empty_buffer_refuses_without_a_dialog(qapp):
    window = window_with_lines(qapp, lines=[])
    try:
        window._on_save_clicked()
        assert window._last_error == "No lines to save"
    finally:
        window.close()


def test_an_unwritable_path_reports_and_changes_nothing_else(qapp, tmp_path):
    window = window_with_lines(qapp)
    bad = str(tmp_path / "missing-dir" / "out.txt")
    try:
        window._save_to(bad)
        assert bad in (window._last_error or "")
        assert len(list(window._buffer.lines())) == len(LINES)
    finally:
        window.close()


def test_a_successful_save_is_confirmed_in_the_pane(qapp, tmp_path):
    window = window_with_lines(qapp)
    path = tmp_path / "out.txt"
    try:
        window._save_to(str(path))
        assert f"[saved 3 lines to {path}]" in window._pane._view.toPlainText()
    finally:
        window.close()
