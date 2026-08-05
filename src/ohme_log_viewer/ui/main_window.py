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

#: How long to wait for the reader thread to exit before giving up on it.
_READER_STOP_TIMEOUT_MS = 5000


class MainWindow(QMainWindow):
    """Top-level window."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Ohme Log Viewer")
        self.resize(1100, 700)

        # One link per connection, created in _connect. Never a long-lived
        # instance shared between sessions: a reader that outlives its session
        # would otherwise be holding the very object the next session opens.
        self._link: SerialLink | None = None
        self._buffer = LogBuffer()
        self._reader: LogReader | None = None
        self._pending: list[LogLine] = []
        self._rerender_pending = False
        self._received = 0
        self._last_error: str | None = None
        # Readers that would not stop. Held so neither Qt nor Python frees a
        # thread object while its OS thread is still running.
        self._orphaned_readers: list[LogReader] = []

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
            # Via _report_error, not showMessage: the flush timer rewrites the
            # status text every 100 ms and would erase it before it was read.
            self._report_error("No serial port selected")
            return

        # A fresh link per connection. If a previous reader had to be parked it
        # still holds the *previous* link, which _disconnect closed, so it can
        # neither read this session's port nor write its own extract_logs to
        # it — and it cannot race this session on a shared receive buffer.
        link = SerialLink()
        try:
            link.open(device)
        except SerialLinkError as exc:
            self._report_error(f"Could not open {device}: {exc}")
            return

        self._link = link
        self._last_error = None

        self._reader = LogReader(link)
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
            if reader.wait(_READER_STOP_TIMEOUT_MS):
                reader.deleteLater()
            else:
                # Destroying a QThread Qt still considers running is fatal, and
                # letting Python collect the wrapper is no better. Park it and
                # carry on: leaking one stuck thread beats killing the app. The
                # reader's own reads and sleeps are bounded, so reaching this
                # means something pathological — say so rather than hiding it.
                #
                # Cut its signals first. A parked reader is still running and
                # will emit whenever it unblocks; left connected it would push
                # lines into the buffer, or trigger a second _disconnect, long
                # after the UI said "Disconnected". Parked means inert.
                reader.line_received.disconnect(self._on_line_received)
                reader.error.disconnect(self._on_reader_error)
                reader.finished.disconnect(self._on_reader_finished)
                self._orphaned_readers.append(reader)
                self._report_error(
                    f"Reader thread did not stop within "
                    f"{_READER_STOP_TIMEOUT_MS // 1000}s; leaving it parked"
                )

        # Closed even for a parked reader: the link drops its port reference,
        # so the orphan's next call raises instead of touching the device.
        link, self._link = self._link, None
        if link is not None:
            link.close()

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
        if self._rerender_pending:
            # A filter change invalidates the whole view, so the incremental
            # batch is dropped rather than appended on top of the re-render.
            self._pending.clear()
            self._pane.render(self._buffer.visible())
            self._rerender_pending = False
        elif self._pending:
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
        """Record a filter change and let the next flush repaint once.

        Re-enabling a tag must bring its earlier lines back, which costs a
        full re-render of the buffer. Doing that inline made All/None — which
        emit one signal per tag — cost tags × lines: measured at 3.9 s frozen
        for None and 4.3 s for All over a full 50,000-line buffer with 30
        tags. Deferring to the 100 ms flush collapses any number of toggles
        into a single render. A lone toggle repaints up to 100 ms later, which
        is exactly how arriving lines already behave.
        """
        self._buffer.set_tag_enabled(tag, on)
        self._rerender_pending = True

    def _on_clear_clicked(self) -> None:
        self._buffer.clear()
        self._pending.clear()
        self._rerender_pending = False
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
