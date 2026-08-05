# Tag Multi-Select Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a check state applied to any row of a multi-row tag selection apply to the whole selection, so hiding ten tags costs one click.

**Architecture:** Everything lives in `ui/tag_panel.py`. The list is switched to `ExtendedSelection`, and `_on_item_changed` propagates the changed row's new state across `selectedItems()` when the changed row belongs to that selection. `main_window.py` and `log_buffer.py` are untouched: one `tag_toggled` per changed tag is already the shape `All`/`None` produces, and `MainWindow._on_tag_toggled` already collapses any number of toggles into one re-render at the next 100 ms flush.

**Tech Stack:** PySide6 (Qt Widgets), pytest, Qt offscreen platform for headless widget tests.

## Global Constraints

- Python `>=3.11`; dependencies limited to `PySide6>=6.6` and `pyserial>=3.5` — this feature adds none.
- `black` line-length 100, target py311; `ruff` line-length 100. Both must pass clean.
- **No git commits.** The user's `CLAUDE.md` forbids them and the branch plan keeps this feature uncommitted, so the usual per-task commit step is deliberately absent below.
- Widget tests set `QT_QPA_PLATFORM=offscreen` before importing PySide6, and reuse the one `QApplication` via a session fixture — as `tests/test_log_reader.py` does.
- `TagPanel` holds no filter state: it stays a projection of `LogBuffer`, emitting `tag_toggled(tag, on)` per change. Selection is view state and belongs to the panel alone.

---

### Task 1: Propagate a check state across the selection

**Files:**
- Modify: `src/ohme_log_viewer/ui/tag_panel.py` (`_build_ui`, `_on_item_changed`, `__init__`)
- Test: `tests/test_tag_panel.py` (create)

**Interfaces:**
- Consumes: `TagPanel.sync(tags, counts, enabled)`, `TagPanel.tag_toggled(str, bool)`, `_TAG_ROLE` — all already present.
- Produces: `TagPanel._list` in `QListWidget.SelectionMode.ExtendedSelection`; `_on_item_changed` propagating across `self._list.selectedItems()`; re-entrancy flag `self._propagating: bool`.

- [ ] **Step 1: Write the failing tests**

```python
"""Headless tests for the tag panel's selection behaviour.

Driven by real QTest clicks and keystrokes on Qt's offscreen platform: the
behaviour under test is Qt's own — when itemChanged fires relative to the
selection collapsing — so stubbing Qt would test nothing.
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
    """A panel showing TAGS, all enabled, with its signal recorded."""
    widget = TagPanel()
    widget.sync(TAGS, {tag: 1 for tag in TAGS}, {tag: True for tag in TAGS})
    widget.resize(200, 300)
    widget.show()
    emitted: list[tuple[str, bool]] = []
    widget.tag_toggled.connect(lambda tag, on: emitted.append((tag, on)))
    return widget, emitted


def select(panel, *tags):
    """Select exactly *tags*, leaving the last one current."""
    widget, _ = panel
    widget._list.clearSelection()
    for tag in tags:
        widget._items[tag].setSelected(True)
    widget._list.setCurrentItem(
        widget._items[tags[-1]], QItemSelectionModel.SelectionFlag.NoUpdate
    )
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
    return {
        tag: widget._items[tag].checkState() == Qt.CheckState.Checked for tag in TAGS
    }


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
    widget.sync(TAGS, {tag: 1 for tag in TAGS}, {"CHARGING": True, "MODEM": False, "OCPP": True, "RCD": True})
    emitted.clear()
    select(panel, "MODEM", "OCPP")
    click_checkbox(panel, "OCPP")  # OCPP goes off; MODEM is already off
    assert emitted == [("OCPP", False)]


def test_a_single_row_selection_behaves_as_before(panel):
    _, emitted = panel
    select(panel, "MODEM")
    click_checkbox(panel, "MODEM")
    assert emitted == [("MODEM", False)]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_tag_panel.py -q`
Expected: FAIL — the propagation tests report only the clicked tag (e.g. `[('MODEM', False)]` where three entries were expected), because the list is still single-select and `_on_item_changed` emits for one row.

- [ ] **Step 3: Switch the list to multi-select and propagate**

In `__init__`, alongside `self._suppress_signals = False`:

```python
        # Guards the propagation write below: each setCheckState re-enters
        # _on_item_changed, which must not emit a second time for that row.
        self._propagating = False
```

In `_build_ui`, after `self._list = QListWidget()`:

```python
        self._list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
```

Replace `_on_item_changed`:

```python
    def _on_item_changed(self, item: QListWidgetItem) -> None:
        if self._suppress_signals or self._propagating:
            return

        on = item.checkState() == Qt.CheckState.Checked
        selected = self._list.selectedItems()
        if item not in selected:
            # A row nobody selected speaks only for itself, exactly as before.
            self.tag_toggled.emit(item.data(_TAG_ROLE), on)
            return

        self.tag_toggled.emit(item.data(_TAG_ROLE), on)
        for other in self._apply_to(selected, item):
            self.tag_toggled.emit(other.data(_TAG_ROLE), on)

    def _apply_to(
        self, selected: Sequence[QListWidgetItem], source: QListWidgetItem
    ) -> list[QListWidgetItem]:
        """Give every row in *selected* the check state of *source*.

        Returns the rows that actually changed, so the caller emits nothing for
        a row that was already in the target state — the buffer must not see
        writes that change nothing.
        """
        state = source.checkState()
        changed: list[QListWidgetItem] = []
        self._propagating = True
        try:
            for item in selected:
                if item is source or item.checkState() == state:
                    continue
                item.setCheckState(state)
                changed.append(item)
        finally:
            self._propagating = False
        return changed
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_tag_panel.py -q`
Expected: PASS, 6 tests.

- [ ] **Step 5: Run the whole suite and the linters**

Run: `.venv/bin/pytest -q && .venv/bin/ruff check src/ tests/ && .venv/bin/black --check src/ tests/`
Expected: all pass. `sync()` emitting nothing is already covered by the existing suite via `MainWindow`; a regression there would show up as extra `tag_toggled` entries.

---

### Task 2: Keep the selection after a propagated click

Qt collapses a multi-row selection to the clicked row *after* `_on_item_changed`
returns. Without this task the selection the user built is consumed by the click
that uses it, so flipping the same group back on means re-selecting it.

**Files:**
- Modify: `src/ohme_log_viewer/ui/tag_panel.py` (`_on_item_changed`, new `_keep_selected`)
- Test: `tests/test_tag_panel.py`

**Interfaces:**
- Consumes: `_apply_to` and `self._items` from Task 1.
- Produces: `TagPanel._keep_selected(tags: Sequence[str]) -> None`, re-selecting on the next event-loop turn.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_selection_survives_a_propagated_click(qapp, panel):
    widget, _ = panel
    select(panel, "MODEM", "OCPP", "RCD")
    click_checkbox(panel, "MODEM")
    qapp.processEvents()  # the restore lands on the next event-loop turn
    assert {i.data(Qt.ItemDataRole.UserRole) for i in widget._list.selectedItems()} == {
        "MODEM",
        "OCPP",
        "RCD",
    }


def test_restoring_the_selection_emits_no_further_toggles(qapp, panel):
    _, emitted = panel
    select(panel, "MODEM", "OCPP")
    click_checkbox(panel, "MODEM")
    emitted.clear()
    qapp.processEvents()
    assert emitted == []


def test_the_selection_survives_a_sync_flush(qapp, panel):
    widget, _ = panel
    select(panel, "MODEM", "OCPP")
    widget.sync(TAGS, {tag: 99 for tag in TAGS}, {tag: True for tag in TAGS})
    assert {i.data(Qt.ItemDataRole.UserRole) for i in widget._list.selectedItems()} == {
        "MODEM",
        "OCPP",
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_tag_panel.py -k survives -q`
Expected: `test_the_selection_survives_a_propagated_click` FAILS with the selection collapsed to `{'MODEM'}`. `test_the_selection_survives_a_sync_flush` may already pass — `setCheckState`/`setText` do not touch selection — and is kept as a regression guard on a 100 ms-interval code path.

- [ ] **Step 3: Restore the selection on the next turn**

Add the import in `tag_panel.py`: `from PySide6.QtCore import QTimer` (join the existing `Qt, Signal` import).

At the end of the propagating branch of `_on_item_changed`, after the emit loop:

```python
        self._keep_selected([row.data(_TAG_ROLE) for row in selected])
```

And the method:

```python
    def _keep_selected(self, tags: Sequence[str]) -> None:
        """Put the selection back once Qt has finished collapsing it.

        A plain click clears the selection down to the clicked row, and Qt does
        that after this slot returns — so restoring inline would be undone
        immediately. A zero-delay timer runs after the click is fully handled.
        Re-selecting emits no itemChanged, so the filter cannot be disturbed by
        it. Deliberately not done for a click outside the selection: that is an
        ordinary click and must clear the selection as usual.
        """
        QTimer.singleShot(0, lambda: self._reselect(tags))

    def _reselect(self, tags: Sequence[str]) -> None:
        for tag in tags:
            item = self._items.get(tag)
            if item is not None:
                item.setSelected(True)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_tag_panel.py -q`
Expected: PASS, 9 tests.

---

### Task 3: Keep All/None immune to the selection

`set_all` writes check states through `setCheckState`, which now reaches the new
propagation path. If any row it writes happens to be selected, that row would
propagate across the selection and emit a second time — and schedule a pointless
selection restore. `All`/`None` must keep emitting exactly one signal per row it
actually changed.

**Files:**
- Modify: `src/ohme_log_viewer/ui/tag_panel.py` (`set_all`)
- Test: `tests/test_tag_panel.py`

**Interfaces:**
- Consumes: `self._propagating` from Task 1.
- Produces: `set_all(on: bool)` emitting one `tag_toggled` per changed row, unchanged in signature and contract.

- [ ] **Step 1: Write the failing test**

```python
def test_none_emits_once_per_tag_even_with_a_selection_active(panel):
    widget, emitted = panel
    select(panel, "MODEM", "OCPP")
    widget.set_all(False)
    assert sorted(emitted) == [
        ("CHARGING", False),
        ("MODEM", False),
        ("OCPP", False),
        ("RCD", False),
    ]


def test_all_emits_only_for_tags_it_changed(panel):
    widget, emitted = panel
    widget.sync(TAGS, {tag: 1 for tag in TAGS}, {"CHARGING": True, "MODEM": False, "OCPP": False, "RCD": True})
    emitted.clear()
    select(panel, "MODEM", "OCPP")
    widget.set_all(True)
    assert sorted(emitted) == [("MODEM", True), ("OCPP", True)]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_tag_panel.py -k emits -q`
Expected: FAIL — duplicate entries for the selected tags, because writing a selected row re-enters the propagation path.

- [ ] **Step 3: Emit from set_all directly**

```python
    def set_all(self, on: bool) -> None:
        """Check or uncheck every tag, emitting one signal per actual change.

        Writes with propagation suppressed and emits afterwards, rather than
        letting itemChanged do it: a row that happens to be selected would
        otherwise propagate across the selection and emit twice.
        """
        state = Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
        changed = [item for item in self._items.values() if item.checkState() != state]
        self._propagating = True
        try:
            for item in changed:
                item.setCheckState(state)
        finally:
            self._propagating = False
        for item in changed:
            self.tag_toggled.emit(item.data(_TAG_ROLE), on)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_tag_panel.py -q`
Expected: PASS, 11 tests.

---

### Task 4: Make the gesture discoverable and document it

The gesture is invisible by design choice, so the tooltip is the only affordance.

**Files:**
- Modify: `src/ohme_log_viewer/ui/tag_panel.py` (module docstring, `_build_ui`)
- Modify: `README.md` (the **Tag filter** bullet)

- [ ] **Step 1: Add the tooltip and the module note**

In `_build_ui`, after the `setSelectionMode` call:

```python
        self._list.setToolTip(
            "Select several tags (shift- or ⌘-click, or drag), then tick or "
            "untick any one of them to apply it to the whole selection. "
            "Space does the same."
        )
```

Extend the module docstring with a sentence recording why the list is multi-select and that the selection is a working set that survives the click.

- [ ] **Step 2: Update the README bullet**

Extend the **Tag filter** bullet in `README.md` to state that several tags can be selected and flipped together with one tick, and that the selection persists so the same group can be flipped back.

- [ ] **Step 3: Run everything**

Run: `.venv/bin/pytest -q && .venv/bin/ruff check src/ tests/ && .venv/bin/black --check src/ tests/`
Expected: all pass.

- [ ] **Step 4: Check it in the running app**

Launch `.venv/bin/ohme-log-viewer`, connect, shift-click a run of tags and untick one. Confirm the whole run disappears from the pane in one click, the selection stays highlighted, and ticking any of them brings them all back.

---

## Self-Review

**Spec coverage:** multi-select mode (T1), propagation on checkbox click (T1), propagation on Space (T1 — Space reaches the same `itemChanged` path, proven by probe), non-selected row toggles alone (T1), no signal for rows already in the target state (T1), selection survives the click (T2), selection survives `sync()` (T2), `sync()` still silent (T1 step 5), All/None unchanged (T3), tooltip and docs (T4). No spec requirement is unassigned.

**Placeholders:** none — every code and test step carries its actual content.

**Type consistency:** `_apply_to(selected, source) -> list[QListWidgetItem]` defined in T1 and used only there; `_keep_selected(tags)` / `_reselect(tags)` defined and used in T2; `_propagating` introduced in T1 and reused in T3; `_TAG_ROLE` and `self._items` are pre-existing. `Sequence` is already imported in `tag_panel.py`.
