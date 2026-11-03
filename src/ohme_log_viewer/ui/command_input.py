"""Single-line CLI command entry with shell-style history.

Enter submits the line and clears the box. Up and Down walk back and forth
through commands already submitted this session, so a command repeated while
diagnosing — a status query, say — is one keystroke away rather than retyped.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QLineEdit, QWidget


class CommandInput(QLineEdit):
    """Emits ``command_entered`` with each non-blank line the user submits."""

    command_entered: Signal = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setPlaceholderText("CLI command — Enter to send, ↑/↓ for history")
        self._history: list[str] = []
        # Index into _history while browsing it; len(_history) means "not
        # browsing", i.e. editing a fresh line.
        self._cursor = 0
        self.returnPressed.connect(self._on_return_pressed)

    def history(self) -> list[str]:
        return list(self._history)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.key() == Qt.Key.Key_Up:
            self._browse(-1)
        elif event.key() == Qt.Key.Key_Down:
            self._browse(+1)
        else:
            super().keyPressEvent(event)

    def _on_return_pressed(self) -> None:
        command = self.text().strip()
        self.clear()
        if not command:
            return
        # Consecutive repeats collapse, as in a shell, so polling one command
        # does not push everything else out of easy reach.
        if not self._history or self._history[-1] != command:
            self._history.append(command)
        self._cursor = len(self._history)
        self.command_entered.emit(command)

    def _browse(self, step: int) -> None:
        cursor = self._cursor + step
        if cursor < 0 or cursor > len(self._history):
            return
        self._cursor = cursor
        self.setText(self._history[cursor] if cursor < len(self._history) else "")
