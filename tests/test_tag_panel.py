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

from ohme_log_viewer.log_buffer import UNPARSED_TAG
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


# ── ordering ─────────────────────────────────────────────────────────────


def rows(panel):
    widget, _ = panel
    return [
        widget._list.item(row).data(Qt.ItemDataRole.UserRole) for row in range(widget._list.count())
    ]


def show(qapp, *tags):
    """A panel that has seen *tags* in the order given."""
    widget = TagPanel()
    widget.sync(list(tags), {tag: 1 for tag in tags}, {tag: True for tag in tags})
    return widget, []


def test_tags_are_listed_alphabetically_whatever_order_they_arrive_in(qapp):
    panel = show(qapp, "RCD", "CHARGING", "OCPP", "MODEM")
    assert rows(panel) == ["CHARGING", "MODEM", "OCPP", "RCD"]


def test_a_tag_discovered_later_takes_its_place_rather_than_the_end(qapp):
    # Tags appear as their first line arrives, so most of them turn up after
    # the list is already built.
    panel = show(qapp, "MODEM", "RCD")
    widget, _ = panel
    widget.sync(
        ["MODEM", "RCD", "OCPP"],
        {"MODEM": 1, "RCD": 1, "OCPP": 1},
        {"MODEM": True, "RCD": True, "OCPP": True},
    )
    assert rows(panel) == ["MODEM", "OCPP", "RCD"]


def test_ordering_ignores_case(qapp):
    panel = show(qapp, "modem", "CHARGING", "Ocpp")
    assert rows(panel) == ["CHARGING", "modem", "Ocpp"]


def test_the_unparsed_group_sorts_last_despite_its_bracket(qapp):
    panel = show(qapp, UNPARSED_TAG, "RCD", "CHARGING")
    assert rows(panel) == ["CHARGING", "RCD", UNPARSED_TAG]


def test_a_new_tag_does_not_disturb_the_selection(qapp):
    panel = show(qapp, "MODEM", "RCD")
    widget, _ = panel
    widget._items["MODEM"].setSelected(True)
    widget._items["RCD"].setSelected(True)
    widget.sync(
        ["MODEM", "RCD", "CHARGING"],
        {"MODEM": 1, "RCD": 1, "CHARGING": 1},
        {"MODEM": True, "RCD": True, "CHARGING": True},
    )
    assert selected_tags(panel) == {"MODEM", "RCD"}


def test_a_new_tag_does_not_disturb_the_check_states_around_it(qapp):
    panel = show(qapp, "MODEM", "RCD")
    widget, _ = panel
    enabled = {"MODEM": False, "RCD": True, "CHARGING": True}
    widget.sync(["MODEM", "RCD", "CHARGING"], {tag: 1 for tag in enabled}, enabled)
    assert widget._items["MODEM"].checkState() == Qt.CheckState.Unchecked
    assert widget._items["RCD"].checkState() == Qt.CheckState.Checked


def test_each_row_still_shows_its_tag_and_count(qapp):
    panel = show(qapp, "RCD", "MODEM")
    widget, _ = panel
    widget.sync(["RCD", "MODEM"], {"RCD": 7, "MODEM": 3}, {"RCD": True, "MODEM": True})
    assert [widget._list.item(row).text() for row in range(2)] == ["MODEM  (3)", "RCD  (7)"]


def test_the_selection_survives_a_sync_flush(panel):
    # sync() runs every 100 ms while streaming. Losing the selection to it
    # would make the panel unusable during a live session.
    widget, _ = panel
    select(panel, "MODEM", "OCPP")
    widget.sync(TAGS, {tag: 99 for tag in TAGS}, {tag: True for tag in TAGS})
    assert selected_tags(panel) == {"MODEM", "OCPP"}


# ── refreshing the list ──────────────────────────────────────────────────


def test_the_refresh_button_asks_the_window_for_a_refresh(panel):
    widget, _ = panel
    asked = []
    widget.tags_refresh_requested.connect(lambda: asked.append(True))
    widget._refresh_tags_btn.click()
    assert asked == [True]


def test_reset_empties_the_list(panel):
    widget, _ = panel
    widget.reset()
    assert widget._list.count() == 0
    assert widget._items == {}


def test_reset_emits_no_toggles(panel):
    # Rows are destroyed, not unticked: the filter must not be touched.
    widget, emitted = panel
    widget.reset()
    assert emitted == []


def test_the_list_rebuilds_after_a_reset(panel):
    widget, _ = panel
    widget.reset()
    widget.sync(["RCD", "MODEM"], {"RCD": 1, "MODEM": 1}, {"RCD": True, "MODEM": True})
    assert rows(panel) == ["MODEM", "RCD"]
