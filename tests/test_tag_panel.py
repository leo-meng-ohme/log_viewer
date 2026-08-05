"""Headless tests for the tag panel's selection behaviour.

Driven by real QTest clicks and keystrokes on Qt's offscreen platform. The
behaviour under test is Qt's own — when ``itemChanged`` fires relative to the
selection collapsing — so stubbing Qt out would leave nothing worth testing.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QItemSelectionModel, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ohme_log_viewer.ui.tag_panel import TagPanel

TAGS = ["CHARGING", "MODEM", "OCPP", "RCD"]


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(qapp):
    """A shown panel listing TAGS, all enabled, with its signal recorded."""
    widget = TagPanel()
    widget.sync(TAGS, {tag: 1 for tag in TAGS}, {tag: True for tag in TAGS})
    widget.resize(200, 300)
    widget.show()
    emitted: list[tuple[str, bool]] = []
    widget.tag_toggled.connect(lambda tag, on: emitted.append((tag, on)))
    return widget, emitted


def select(panel, *tags):
    """Select exactly *tags*, leaving the last of them current."""
    widget, _ = panel
    widget._list.clearSelection()
    widget._list.setCurrentItem(widget._items[tags[-1]], QItemSelectionModel.SelectionFlag.NoUpdate)
    for tag in tags:
        widget._items[tag].setSelected(True)


def click_checkbox(panel, tag):
    """Click the check indicator of *tag*'s row."""
    widget, _ = panel
    rect = widget._list.visualItemRect(widget._items[tag])
    QTest.mouseClick(
        widget._list.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        QPoint(rect.left() + 8, rect.center().y()),
    )


def states(panel):
    widget, _ = panel
    return {tag: widget._items[tag].checkState() == Qt.CheckState.Checked for tag in TAGS}


def selected_tags(panel):
    widget, _ = panel
    return {item.data(Qt.ItemDataRole.UserRole) for item in widget._list.selectedItems()}


# ── propagation ──────────────────────────────────────────────────────────


def test_unchecking_one_row_of_a_selection_disables_all_of_them(panel):
    _, emitted = panel
    select(panel, "MODEM", "OCPP", "RCD")
    click_checkbox(panel, "MODEM")
    assert sorted(emitted) == [("MODEM", False), ("OCPP", False), ("RCD", False)]
    assert states(panel) == {"CHARGING": True, "MODEM": False, "OCPP": False, "RCD": False}


def test_checking_one_row_of_a_selection_enables_all_of_them(panel):
    widget, emitted = panel
    widget.sync(TAGS, {tag: 1 for tag in TAGS}, {tag: False for tag in TAGS})
    emitted.clear()
    select(panel, "MODEM", "OCPP")
    click_checkbox(panel, "OCPP")
    assert sorted(emitted) == [("MODEM", True), ("OCPP", True)]


def test_a_row_outside_the_selection_toggles_alone(panel):
    _, emitted = panel
    select(panel, "MODEM", "OCPP")
    click_checkbox(panel, "CHARGING")
    assert emitted == [("CHARGING", False)]
    assert states(panel)["MODEM"] is True


def test_space_propagates_across_the_selection(panel):
    widget, emitted = panel
    select(panel, "MODEM", "OCPP", "RCD")
    QTest.keyClick(widget._list, Qt.Key.Key_Space)
    assert sorted(emitted) == [("MODEM", False), ("OCPP", False), ("RCD", False)]


def test_rows_already_in_the_target_state_emit_nothing(panel):
    widget, emitted = panel
    widget.sync(
        TAGS,
        {tag: 1 for tag in TAGS},
        {"CHARGING": True, "MODEM": False, "OCPP": True, "RCD": True},
    )
    emitted.clear()
    select(panel, "MODEM", "OCPP")
    click_checkbox(panel, "OCPP")  # OCPP goes off; MODEM is off already
    assert emitted == [("OCPP", False)]


def test_a_single_row_selection_behaves_as_before(panel):
    _, emitted = panel
    select(panel, "MODEM")
    click_checkbox(panel, "MODEM")
    assert emitted == [("MODEM", False)]


# ── the selection as a working set ───────────────────────────────────────


def test_the_selection_survives_a_propagated_click(qapp, panel):
    select(panel, "MODEM", "OCPP", "RCD")
    click_checkbox(panel, "MODEM")
    qapp.processEvents()  # the restore lands on the next event-loop turn
    assert selected_tags(panel) == {"MODEM", "OCPP", "RCD"}


def test_a_surviving_selection_can_be_flipped_straight_back(qapp, panel):
    _, emitted = panel
    select(panel, "MODEM", "OCPP")
    click_checkbox(panel, "MODEM")
    qapp.processEvents()
    emitted.clear()
    click_checkbox(panel, "MODEM")
    assert sorted(emitted) == [("MODEM", True), ("OCPP", True)]


def test_restoring_the_selection_emits_no_further_toggles(qapp, panel):
    _, emitted = panel
    select(panel, "MODEM", "OCPP")
    click_checkbox(panel, "MODEM")
    emitted.clear()
    qapp.processEvents()
    assert emitted == []


def test_a_click_outside_the_selection_leaves_the_selection_alone(qapp, panel):
    """Qt collapses the selection only when the pressed row was already part of
    it, so ticking an unselected tag costs the working set nothing — and there
    is deliberately no code here forcing it either way."""
    _, emitted = panel
    select(panel, "MODEM", "OCPP")
    click_checkbox(panel, "CHARGING")
    qapp.processEvents()
    assert selected_tags(panel) == {"MODEM", "OCPP"}
    assert emitted == [("CHARGING", False)]


# ── All / None ───────────────────────────────────────────────────────────


def test_none_emits_once_per_tag_even_with_a_selection_active(panel):
    # All/None must stay independent of whatever happens to be selected: one
    # signal per row it changed, never a second from propagation.
    widget, emitted = panel
    select(panel, "MODEM", "OCPP")
    widget.set_all(False)
    assert sorted(emitted) == [
        ("CHARGING", False),
        ("MODEM", False),
        ("OCPP", False),
        ("RCD", False),
    ]


def test_all_emits_only_for_the_tags_it_changed(panel):
    widget, emitted = panel
    widget.sync(
        TAGS,
        {tag: 1 for tag in TAGS},
        {"CHARGING": True, "MODEM": False, "OCPP": False, "RCD": True},
    )
    emitted.clear()
    select(panel, "MODEM", "OCPP")
    widget.set_all(True)
    assert sorted(emitted) == [("MODEM", True), ("OCPP", True)]


def test_the_selection_survives_a_sync_flush(panel):
    # sync() runs every 100 ms while streaming. Losing the selection to it
    # would make the panel unusable during a live session.
    widget, _ = panel
    select(panel, "MODEM", "OCPP")
    widget.sync(TAGS, {tag: 99 for tag in TAGS}, {tag: True for tag in TAGS})
    assert selected_tags(panel) == {"MODEM", "OCPP"}
