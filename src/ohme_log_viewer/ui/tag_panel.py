"""Auto-discovered, checkable list of log tags.

Tags appear as soon as a line carrying them arrives, checked by default. A
device tag can be arbitrarily long, so the list has a fixed width and elides
long names with the full text in a tooltip — sizing the column to the longest
tag would squeeze the log pane, which is the part worth the space.
"""

from __future__ import annotations

from typing import Sequence

from PySide6.QtCore import Qt, Signal
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
        self._build_ui()

    # ── public API ───────────────────────────────────────────────────────

    def sync(
        self,
        tags: Sequence[str],
        counts: dict[str, int],
        enabled: dict[str, bool],
    ) -> None:
        """Add any new tags and refresh every count. Safe to call repeatedly."""
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
                    Qt.CheckState.Checked
                    if enabled.get(tag, True)
                    else Qt.CheckState.Unchecked
                )
                item.setText(f"{tag}  ({counts.get(tag, 0)})")
        finally:
            self._suppress_signals = False

    def set_all(self, on: bool) -> None:
        """Check or uncheck every tag, emitting one signal per actual change."""
        state = Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
        for item in self._items.values():
            if item.checkState() != state:
                item.setCheckState(state)  # emits itemChanged -> tag_toggled

    # ── internals ────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        layout.addWidget(QLabel("Tags"))

        self._list = QListWidget()
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
        if self._suppress_signals:
            return
        tag = item.data(_TAG_ROLE)
        self.tag_toggled.emit(tag, item.checkState() == Qt.CheckState.Checked)
