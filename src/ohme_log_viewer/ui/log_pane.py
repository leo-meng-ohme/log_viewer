"""Read-only, colour-coded view of log lines.

The widget is a projection of LogBuffer, never a store in its own right, so its
block cap is set from the same number as the buffer's ring cap. Leaving it
uncapped would let the view keep every line ever received while the buffer
evicted at 50,000, so the two would silently disagree about what exists and the
view's memory would grow without bound on a long session.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit, QVBoxLayout, QWidget

from ..log_line import LogLine

_FONT_FAMILIES = ("Menlo", "Consolas", "DejaVu Sans Mono", "monospace")
_FONT_SIZE_PT = 10

_BACKGROUND = "#1e1e1e"
_BORDER = "#3c3c3c"

#: Foreground per level letter. Assert and error share red; debug and verbose
#: are dimmed so they recede when a device is running at a chatty level.
_LEVEL_COLOURS = {
    "A": "#f14c4c",
    "E": "#f14c4c",
    "W": "#ffcc66",
    "I": "#d4d4d4",
    "D": "#9a9a9a",
    "V": "#7a7a7a",
}
_UNPARSED_COLOUR = "#9cdcfe"
_ERROR_COLOUR = "#f14c4c"

#: Scroll position tolerance, in LINES, for "the user is at the bottom".
#:
#: The unit matters. Unlike QTextEdit, QPlainTextEdit's vertical scrollbar is
#: measured in blocks — one step is one line, not one pixel. Any non-zero slack
#: is therefore a dead zone several lines deep, and a default mouse-wheel notch
#: is only three lines: the commonest way of scrolling up to read something
#: would land inside it and the next append would yank the view back down.
#: Zero means "exactly at the end", which is what following the tail means.
_AT_BOTTOM_SLACK = 0


class LogPane(QWidget):
    """Scrollable coloured log output."""

    def __init__(self, max_lines: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._max_lines = max_lines
        self._build_ui()

    # ── public API ───────────────────────────────────────────────────────

    def line_count(self) -> int:
        """Lines currently displayed.

        Read from the document rather than tracked in a counter, so it stays
        exact across block-cap eviction and full re-renders alike. Each
        appended line ends in a newline, which leaves one trailing empty
        block — hence the -1.
        """
        return self._view.blockCount() - 1

    def append_lines(self, lines: Sequence[LogLine]) -> None:
        """Append a batch of lines in one repaint."""
        if not lines:
            return
        follow = self._is_at_bottom()
        cursor = self._view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        for line in lines:
            self._insert(cursor, self._text_for(line), self._colour_for(line))
        if follow:
            self._scroll_to_bottom()

    def render(self, lines: Iterable[LogLine]) -> int:
        """Replace the contents with *lines*. Returns how many were rendered."""
        materialised = list(lines)
        self._view.clear()
        self.append_lines(materialised)
        self._scroll_to_bottom()
        return len(materialised)

    def append_error(self, message: str) -> None:
        """Append an internal error notice, distinct from device output."""
        follow = self._is_at_bottom()
        cursor = self._view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._insert(cursor, f"[error] {message}", _ERROR_COLOUR)
        if follow:
            self._scroll_to_bottom()

    def append_notice(self, message: str) -> None:
        """Append an internal notice, verbatim, in the neutral info colour."""
        follow = self._is_at_bottom()
        cursor = self._view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._insert(cursor, message, _LEVEL_COLOURS["I"])
        if follow:
            self._scroll_to_bottom()

    def clear(self) -> None:
        self._view.clear()

    # ── internals ────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._view = QPlainTextEdit()
        self._view.setReadOnly(True)
        self._view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._view.setUndoRedoEnabled(False)
        # +1 for the trailing empty block, so the pane holds exactly as many
        # lines as the buffer does.
        self._view.setMaximumBlockCount(self._max_lines + 1)

        font = QFont()
        font.setFamilies(list(_FONT_FAMILIES))
        font.setPointSize(_FONT_SIZE_PT)
        self._view.setFont(font)

        self._view.setStyleSheet(
            f"QPlainTextEdit {{ background-color: {_BACKGROUND};"
            f" border: 1px solid {_BORDER}; color: {_LEVEL_COLOURS['I']}; }}"
        )
        layout.addWidget(self._view)

    @staticmethod
    def _text_for(line: LogLine) -> str:
        return line.raw

    @staticmethod
    def _colour_for(line: LogLine) -> str:
        if line.level is None:
            return _UNPARSED_COLOUR
        return _LEVEL_COLOURS.get(line.level, _LEVEL_COLOURS["I"])

    @staticmethod
    def _insert(cursor: QTextCursor, text: str, colour: str) -> None:
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(colour))
        cursor.setCharFormat(fmt)
        cursor.insertText(text + "\n")

    def _is_at_bottom(self) -> bool:
        """True if the view is scrolled to the end.

        Appending must not yank the view away from a user who has scrolled up
        to read something.
        """
        bar = self._view.verticalScrollBar()
        return bar.value() >= bar.maximum() - _AT_BOTTOM_SLACK

    def _scroll_to_bottom(self) -> None:
        bar = self._view.verticalScrollBar()
        bar.setValue(bar.maximum())
