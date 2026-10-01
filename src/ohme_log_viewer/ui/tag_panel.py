"""Auto-discovered, checkable list of log tags.

Tags appear as soon as a line carrying them arrives, checked by default, and the
list is kept in alphabetical order — a tag discovered mid-session is inserted
where it belongs rather than appended, so a name stays where the eye last found
it. A device tag can be arbitrarily long, so the list has a fixed width and
elides long names with the full text in a tooltip — sizing the column to the
longest tag would squeeze the log pane, which is the part worth the space.

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
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyleFactory,
    QVBoxLayout,
    QWidget,
)

from ..log_buffer import UNPARSED_TAG

_PANEL_WIDTH_PX = 200

#: Item data role holding the full, unelided tag.
_TAG_ROLE = Qt.ItemDataRole.UserRole

#: Style put on the list where the platform's own cannot draw a check
#: indicator on every row. See :func:`_repair_check_indicators`.
_FALLBACK_STYLE = "Fusion"


def _sort_key(tag: str) -> tuple[bool, str]:
    """Order tags alphabetically, with the unparsed group always last.

    Case is ignored, so a lower-case tag does not sort away from its
    neighbours. ``(unparsed)`` is pinned to the bottom rather than left to the
    alphabet, where its bracket would put it above every real tag: it is a
    catch-all for lines that did not parse, not a tag the device emitted.
    """
    return (tag == UNPARSED_TAG, tag.casefold())


def _repair_check_indicators(view: QListWidget) -> None:
    """Give *view* a style that can draw a check indicator on every row.

    Qt's native macOS style — 6.11.2 on macOS 26 — paints the indicator of the
    first row only. Every other row reserves the space and draws nothing, so
    from the second tag down there is no tick to read the filter from and
    nothing to aim a click at. The check states themselves are correct: this
    is painting alone, it happens whatever the states are, and no repaint,
    scroll or reinsertion brings the missing indicators back. Four checked
    rows in a bare ``QListWidget`` reproduce it, so there is nothing in this
    panel to fix.

    Fusion draws all of them, and is installed on the list alone so the
    buttons and the rest of the window stay native. Chosen by style rather
    than by platform: a session already launched with ``-style Fusion`` needs
    nothing, and neither does Windows.
    """
    if QApplication.style().objectName() != "macos":
        return
    style = QStyleFactory.create(_FALLBACK_STYLE)
    if style is None:  # Not built into this Qt — leave the native style be.
        return
    # Parented so Qt owns it. setStyle does not take ownership, and a style
    # left to Python would be collected, leaving the view painting through a
    # dangling pointer.
    style.setParent(view)
    view.setStyle(style)


class TagPanel(QWidget):
    """Checkable tag list driving the display filter."""

    tag_toggled: Signal = Signal(str, bool)

    #: The user asked for the list to be rebuilt. Emitted rather than acted on:
    #: which tags exist is the buffer's to say, and this panel is its
    #: projection.
    tags_refresh_requested: Signal = Signal()

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
                    self._list.insertItem(self._row_for(tag), item)
                    self._items[tag] = item
                item.setCheckState(
                    Qt.CheckState.Checked if enabled.get(tag, True) else Qt.CheckState.Unchecked
                )
                item.setText(f"{tag}  ({counts.get(tag, 0)})")
        finally:
            self._suppress_signals = False

    def reset(self) -> None:
        """Drop every row, leaving the next :meth:`sync` to rebuild the list.

        Rows are destroyed rather than unticked, so no ``tag_toggled`` is
        emitted and the filter is left entirely to the caller — which has just
        rebuilt it and will pass the new state on the following sync.
        """
        self._suppress_signals = True
        try:
            # Both together: _items would otherwise hold rows Qt has deleted.
            self._list.clear()
            self._items.clear()
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

    def _row_for(self, tag: str) -> int:
        """Return the row *tag* belongs at to keep the list in order.

        A new row is inserted in place rather than the list being re-sorted,
        because ``sync`` runs on every 100 ms flush: re-sorting there would move
        rows under the user's cursor and put the selection and check states at
        risk on a code path that usually has nothing to do. Inserting touches
        the list only when a genuinely new tag is discovered.
        """
        key = _sort_key(tag)
        for row in range(self._list.count()):
            if _sort_key(self._list.item(row).data(_TAG_ROLE)) > key:
                return row
        return self._list.count()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        layout.addWidget(QLabel("Tags"))

        self._list = QListWidget()
        _repair_check_indicators(self._list)
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

        # Its own row: three buttons across 200 px would elide their own labels.
        self._refresh_tags_btn = QPushButton("Refresh tags")
        self._refresh_tags_btn.setToolTip(
            "Rebuild the list from the lines currently held, dropping tags that "
            "no longer appear in them. Every remaining tag comes back ticked."
        )
        self._refresh_tags_btn.clicked.connect(self.tags_refresh_requested.emit)
        layout.addWidget(self._refresh_tags_btn)

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
