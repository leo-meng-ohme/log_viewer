"""Application window: port selection, tag filter, log pane, status bar.

This class owns the single receive pipeline. Its ``_on_line_received`` slot is
the only place that sequences noise filtering, parsing and buffering — the
buffer never parses and the reader never interprets.

Streaming is always continuous, so there is no continuous-mode control: the
reader loops from Connect to Disconnect and nothing can fall out of sync with
it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSplitter,
    QStatusBar,
    QWidget,
)

from ..log_buffer import MAX_LINES, LogBuffer
from ..log_line import LogLine, is_noise, parse
from ..reader import LogReader
from ..serial_link import SerialLink, SerialLinkError, list_usb_ports
from .log_pane import LogPane
from .tag_panel import TagPanel

#: Display batching interval. A burst of lines costs one repaint, not one each.
_FLUSH_INTERVAL_MS = 100


class MainWindow(QMainWindow):
    """Top-level window."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Ohme Log Viewer")
        self.resize(1100, 700)

        self._link = SerialLink()
        self._buffer = LogBuffer()
        self._reader: LogReader | None = None
        self._pending: list[LogLine] = []
        self._received = 0
        self._last_error: str | None = None

        self._build_ui()
        self._refresh_ports()
        self._update_status()

        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(_FLUSH_INTERVAL_MS)
        self._flush_timer.timeout.connect(self._flush)
        self._flush_timer.start()

    # ── UI ───────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        central = QWidget()
        outer = QHBoxLayout(central)
        outer.setContentsMargins(6, 6, 6, 6)

        self._tag_panel = TagPanel()
        self._tag_panel.tag_toggled.connect(self._on_tag_toggled)

        # Same cap for both, so the view and the buffer never disagree about
        # which lines still exist.
        self._pane = LogPane(max_lines=MAX_LINES)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._tag_panel)
        splitter.addWidget(self._pane)
        splitter.setStretchFactor(1, 1)
        outer.addWidget(splitter)

        self.setCentralWidget(central)
        self._build_toolbar()

        self.setStatusBar(QStatusBar())

    def _build_toolbar(self) -> None:
        bar = self.addToolBar("Main")
        bar.setMovable(False)

        bar.addWidget(QLabel(" Port: "))
        self._port_combo = QComboBox()
        self._port_combo.setMinimumWidth(320)
        bar.addWidget(self._port_combo)

        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self._refresh_ports)
        bar.addWidget(self._refresh_btn)

        self._connect_btn = QPushButton("Connect")
        self._connect_btn.clicked.connect(self._on_connect_clicked)
        bar.addWidget(self._connect_btn)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.clicked.connect(self._on_clear_clicked)
        bar.addWidget(self._clear_btn)

    def _refresh_ports(self) -> None:
        previous = self._port_combo.currentData()
        self._port_combo.clear()
        for port in list_usb_ports():
            self._port_combo.addItem(port.label, port.device)
        if previous is not None:
            index = self._port_combo.findData(previous)
            if index >= 0:
                self._port_combo.setCurrentIndex(index)

    # ── connection ───────────────────────────────────────────────────────

    @property
    def _is_streaming(self) -> bool:
        return self._reader is not None

    def _on_connect_clicked(self) -> None:
        if self._is_streaming:
            self._disconnect()
        else:
            self._connect()

    def _connect(self) -> None:
        device = self._port_combo.currentData()
        if device is None:
            self.statusBar().showMessage("No serial port selected")
            return

        try:
            self._link.open(device)
        except SerialLinkError as exc:
            self._report_error(f"Could not open {device}: {exc}")
            return

        self._last_error = None

        self._reader = LogReader(self._link)
        self._reader.line_received.connect(self._on_line_received)
        self._reader.error.connect(self._on_reader_error)
        self._reader.finished.connect(self._on_reader_finished)
        self._reader.start()

        self._connect_btn.setText("Disconnect")
        self._port_combo.setEnabled(False)
        self._refresh_btn.setEnabled(False)
        self._update_status()

    def _disconnect(self) -> None:
        reader, self._reader = self._reader, None
        if reader is not None:
            reader.stop()
            reader.wait(5000)
            reader.deleteLater()
        self._link.close()

        self._connect_btn.setText("Connect")
        self._port_combo.setEnabled(True)
        self._refresh_btn.setEnabled(True)
        self._update_status()

    def _on_reader_error(self, message: str) -> None:
        # The buffer survives, so what was captured before the failure stays
        # readable and filterable.
        self._report_error(message)
        self._disconnect()

    def _report_error(self, message: str) -> None:
        """Show *message* in the pane and keep it in the status bar.

        Held in ``_last_error`` rather than pushed straight to the status bar,
        because ``_update_status`` runs on every 100 ms flush and would
        otherwise overwrite it before it could be read.
        """
        self._last_error = message
        self._pane.append_error(message)
        self._update_status()

    def _on_reader_finished(self) -> None:
        if self._is_streaming:
            self._disconnect()

    # ── receive pipeline ─────────────────────────────────────────────────

    def _on_line_received(self, raw: str) -> None:
        if is_noise(raw):
            return
        line = parse(raw)
        self._received += 1
        if self._buffer.append(line):
            self._pending.append(line)

    def _flush(self) -> None:
        if self._pending:
            self._pane.append_lines(self._pending)
            self._pending.clear()
        self._sync_tags()
        self._update_status()

    def _sync_tags(self) -> None:
        tags = self._buffer.tags()
        self._tag_panel.sync(
            tags,
            {tag: self._buffer.count_for(tag) for tag in tags},
            {tag: self._buffer.is_tag_enabled(tag) for tag in tags},
        )

    def _on_tag_toggled(self, tag: str, on: bool) -> None:
        self._buffer.set_tag_enabled(tag, on)
        # Full re-render, so re-enabling a tag brings its earlier lines back.
        # The pending batch is dropped first, otherwise those lines would be
        # appended a second time after already being rendered here.
        self._pending.clear()
        self._pane.render(self._buffer.visible())
        self._update_status()

    def _on_clear_clicked(self) -> None:
        self._buffer.clear()
        self._pending.clear()
        self._pane.clear()
        self._received = 0
        # Tags and their check states deliberately survive a clear.
        self._sync_tags()
        self._update_status()

    def _update_status(self) -> None:
        if self._is_streaming:
            state = f"Streaming from {self._port_combo.currentData()}"
        else:
            state = "Disconnected"
        # Shown is read from the pane rather than counted here, so it stays
        # exact across both block-cap eviction and full re-renders.
        text = f"{state}   |   received: {self._received}   shown: {self._pane.line_count()}"
        if self._last_error is not None:
            text = f"{text}   |   last error: {self._last_error}"
        self.statusBar().showMessage(text)

    # ── lifecycle ────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:  # type: ignore[override]
        """Stop the reader thread before the process exits."""
        self._flush_timer.stop()
        self._disconnect()
        event.accept()
