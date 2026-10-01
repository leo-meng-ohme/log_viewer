"""Display formatting of the log pane: timestamps aligned into one column.

Tags have no fixed width on the wire, so the pane pads them itself. The width is
the widest tag on display, which means a wider tag arriving later has to push
every earlier line across with it.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from ohme_log_viewer.log_line import parse
from ohme_log_viewer.ui.log_pane import LogPane, format_line

TS = "2026-07-01T10:11:11.950Z"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _shown(pane: LogPane) -> list[str]:
    return pane._view.toPlainText().splitlines()


def test_a_short_tag_is_padded_to_the_column_width():
    line = parse(f"I/OTA   [{TS}] Download started")
    assert format_line(line, 5) == f"I/OTA   [{TS}] Download started"


def test_a_tag_wider_than_the_column_is_never_truncated():
    line = parse(f"W/CHARGING_SESSION_MGR [{TS}] Session aborted")
    assert format_line(line, 5) == f"W/CHARGING_SESSION_MGR [{TS}] Session aborted"


def test_an_assert_keeps_its_source_ahead_of_the_message():
    line = parse(f"A/OCPP [{TS}] (ocpp.c:foo:42) boom")
    assert format_line(line, 6) == f"A/OCPP   [{TS}] (ocpp.c:foo:42) boom"


def test_an_unparsed_line_is_shown_unchanged():
    line = parse("  some  raw   text")
    assert format_line(line, 20) == "  some  raw   text"


def test_rendered_timestamps_start_in_one_column(qapp):
    pane = LogPane(max_lines=100)
    pane.render(
        [
            parse(f"I/OTA   [{TS}] a"),
            parse(f"W/WIFI_MGR [{TS}] b"),
            parse(f"E/MODEM [{TS}] c"),
        ]
    )
    columns = {text.index("[") for text in _shown(pane)}
    assert columns == {len("W/WIFI_MGR ")}


def test_a_line_that_fits_the_column_is_appended_in_place(qapp):
    pane = LogPane(max_lines=100)
    pane.render([parse(f"W/WIFI_MGR [{TS}] a")])
    assert pane.append_lines([parse(f"I/OTA [{TS}] b")]) is True
    assert _shown(pane)[-1] == f"I/OTA      [{TS}] b"


def test_a_wider_tag_asks_for_a_re_render_instead_of_appending(qapp):
    pane = LogPane(max_lines=100)
    pane.render([parse(f"I/OTA [{TS}] a")])
    assert pane.append_lines([parse(f"W/WIFI_MGR [{TS}] b")]) is False
    assert len(_shown(pane)) == 1


def test_clearing_resets_the_column_width(qapp):
    pane = LogPane(max_lines=100)
    pane.render([parse(f"W/WIFI_MGR [{TS}] a")])
    pane.clear()
    assert pane.append_lines([parse(f"I/OTA [{TS}] b")]) is True
    assert _shown(pane) == [f"I/OTA [{TS}] b"]
