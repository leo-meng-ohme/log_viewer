"""Auto-discovered, checkable list of log tags.

Tags appear as soon as a line carrying them arrives, checked by default. A
device tag can be arbitrarily long, so the list has a fixed width and elides
long names with the full text in a tooltip — sizing the column to the longest
tag would squeeze the log pane, which is the part worth the space.

The list is multi-select, and a check state applied to any row of the selection
applies to the whole selection: on a device with thirty tags, narrowing the view
was otherwise a click per tag. The selection deliberately outlives the click
that used it, so a group of tags stays a working set that can be flipped off and
back on. Nothing on screen advertises the gesture beyond the list's tooltip —
that was the accepted price of adding no widgets to a 200 px panel.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

_PANEL_WIDTH_PX = 200

#: Item data role holding the full, unelided tag.
_TAG_ROLE = Qt.ItemDataRole.UserRole


class TagPanel(QWidget):
    """Checkable tag list driving the display filter."""

    tag_toggled: Signal = Signal(str, bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items: dict[str, QListWidgetItem] = {}
        self._suppress_signals = False
        # Guards the propagation write: every setCheckState re-enters
        # itemChanged, and a row must not emit its change twice.
        self._propagating = False
        self._build_ui()

    # ── public API ───────────────────────────────────────────────────────

    def sync(
        self,
        tags: Sequence[str],
        counts: dict[str, int],
        enabled: dict[str, bool],
    ) -> None:
        """Add any new tags, refresh every count, and mirror *enabled*.

        Safe to call repeatedly — it updates rows in place and never
        duplicates one. Check state is mirrored from *enabled* on every call,
        not just when a row is created: the buffer owns filter state and this
        panel is a projection of it, so mirroring keeps the two from drifting.
        The caller must therefore pass its authoritative map.

        Emits nothing. Writing check states fires Qt's ``itemChanged``, which
        is suppressed here — otherwise every flush would re-apply the filter.
        """
        self._suppress_signals = True
        try:
            for tag in tags:
                item = self._items.get(tag)
                if item is None:
                    item = QListWidgetItem()
                    item.setData(_TAG_ROLE, tag)
                    item.setToolTip(tag)
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    self._list.addItem(item)
                    self._items[tag] = item
                item.setCheckState(
                    Qt.CheckState.Checked if enabled.get(tag, True) else Qt.CheckState.Unchecked
                )
                item.setText(f"{tag}  ({counts.get(tag, 0)})")
        finally:
            self._suppress_signals = False

    def set_all(self, on: bool) -> None:
        """Check or uncheck every tag, emitting one signal per actual change.

        Writes with propagation suppressed and emits afterwards, rather than
        letting ``itemChanged`` do the emitting. Going through that path would
        make All/None correct only by accident: a row that happened to be
        selected would propagate across the selection mid-loop, and the result
        would come out right purely because the loop then skips the rows that
        propagation already flipped. This way the two features cannot interact
        at all, whatever is selected.
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

    # ── internals ────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        layout.addWidget(QLabel("Tags"))

        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        # The only affordance the gesture has. Set on the list rather than each
        # row, whose tooltip carries its own unelided tag name.
        self._list.setToolTip(
            "Select several tags — shift-click for a run, ⌘-click to add one, or "
            "drag — then tick or untick any one of them to apply it to the whole "
            "selection. Space does the same. The selection stays put, so the "
            "same group can be flipped back."
        )
        self._list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._list.setFixedWidth(_PANEL_WIDTH_PX)
        self._list.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self._list, stretch=1)

        buttons = QHBoxLayout()
        all_btn = QPushButton("All")
        all_btn.clicked.connect(lambda: self.set_all(True))
        buttons.addWidget(all_btn)

        none_btn = QPushButton("None")
        none_btn.clicked.connect(lambda: self.set_all(False))
        buttons.addWidget(none_btn)
        layout.addLayout(buttons)

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
        self._keep_selected([row.data(_TAG_ROLE) for row in selected])

    def _apply_to(
        self, selected: Sequence[QListWidgetItem], source: QListWidgetItem
    ) -> list[QListWidgetItem]:
        """Give every row in *selected* the check state of *source*.

        Returns only the rows that actually changed. A row already in the
        target state emits nothing: the buffer owns the filter and must not see
        writes that change none of it.
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

    def _keep_selected(self, tags: Sequence[str]) -> None:
        """Put the selection back once Qt has finished collapsing it.

        Clicking a row that is already selected clears the selection down to
        that row — Qt does this on mouse release, after this slot has returned,
        so restoring inline would simply be undone. A zero-delay timer runs once
        the click is fully handled.

        Without this the selection would be consumed by the very click that used
        it, and flipping the same group back would mean picking it out again.
        Re-selecting emits no ``itemChanged``, so the filter cannot be disturbed
        by the restore.
        """
        QTimer.singleShot(0, lambda: self._reselect(tags))

    def _reselect(self, tags: Sequence[str]) -> None:
        for tag in tags:
            item = self._items.get(tag)
            if item is not None:
                item.setSelected(True)
