"""Headless tests for the command box: submission and shell-style history."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ohme_log_viewer.ui.command_input import CommandInput


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def box(qapp):
    widget = CommandInput()
    sent: list[str] = []
    widget.command_entered.connect(sent.append)
    widget.sent = sent
    return widget


def submit(box, text):
    QTest.keyClicks(box, text)
    QTest.keyClick(box, Qt.Key.Key_Return)


def test_enter_emits_the_trimmed_command_and_clears_the_box(box):
    submit(box, "  get_cli_status  ")
    assert box.sent == ["get_cli_status"]
    assert box.text() == ""


def test_a_blank_line_is_not_sent(box):
    submit(box, "   ")
    assert box.sent == []
    assert box.history() == []


def test_up_and_down_walk_the_history(box):
    submit(box, "first")
    submit(box, "second")

    QTest.keyClick(box, Qt.Key.Key_Up)
    assert box.text() == "second"
    QTest.keyClick(box, Qt.Key.Key_Up)
    assert box.text() == "first"
    QTest.keyClick(box, Qt.Key.Key_Up)  # already at the oldest
    assert box.text() == "first"
    QTest.keyClick(box, Qt.Key.Key_Down)
    assert box.text() == "second"
    QTest.keyClick(box, Qt.Key.Key_Down)  # past the newest: a fresh line
    assert box.text() == ""


def test_a_repeated_command_is_kept_once(box):
    submit(box, "status")
    submit(box, "status")
    assert box.sent == ["status", "status"]
    assert box.history() == ["status"]
