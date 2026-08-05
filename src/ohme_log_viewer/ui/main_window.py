"""Application window: port selection, tag filter, log pane, status bar.

This class owns the single receive pipeline. Its ``_on_line_received`` slot is
the only place that sequences noise filtering, parsing and buffering — the
buffer never parses and the reader never interprets.

Streaming is always continuous, so there is no continuous-mode control: the
reader loops from Connect to Disconnect and nothing can fall out of sync with
it.

The connection has three states, not two — Disconnected, Retrying and
Streaming — because a device under development is absent or rebooting as often
as it is present. Connect enters Retrying and attempts once a second; a session
that fails returns there rather than ending. Only Disconnect, or a refusal that
retrying could not fix, leaves for Disconnected. ``_is_trying`` is derived from
the retry timer rather than tracked separately, so the flag and the state cannot
disagree.
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

from ..auth import DeviceAuthenticator
from ..log_buffer import MAX_LINES, LogBuffer
from ..log_line import LogLine, is_noise, parse
from ..reader import LogReader
from ..serial_link import SerialLink, SerialLinkError, list_usb_ports
from ..version import __version__
from .log_pane import LogPane
from .tag_panel import TagPanel

#: Display batching interval. A burst of lines costs one repaint, not one each.
_FLUSH_INTERVAL_MS = 100

#: Gap between connection attempts while retrying.
_RETRY_INTERVAL_MS = 1000

#: How long to wait for the reader thread to exit before giving up on it.
#:
#: This blocks the GUI thread, so it is sized to the reader's own worst case
#: rather than picked for comfort. Every operation the loop performs is
#: bounded: a read is capped at the link's 0.3 s timeout, and the inter-cycle
#: wait is 50 ms slices that check for interruption between each. A reader
#: that is going to stop at all has therefore stopped well inside 1.5 s;
#: anything slower is pathological and waiting longer will not rescue it.
_READER_STOP_TIMEOUT_MS = 1500


class MainWindow(QMainWindow):
    """Top-level window."""

    def __init__(
        self,
        link_factory=SerialLink,
        authenticator_factory=DeviceAuthenticator,
        retry_interval_ms: int = _RETRY_INTERVAL_MS,
    ) -> None:
        super().__init__()
        self.setWindowTitle(f"Ohme Log Viewer v{__version__}")
        self.resize(1100, 700)

        # Both are per-connection, so they are factories rather than instances:
        # see _attempt for why neither may outlive its session.
        self._link_factory = link_factory
        self._authenticator_factory = authenticator_factory

        # One link per connection, created in _attempt. Never a long-lived
        # instance shared between sessions: a reader that outlives its session
        # would otherwise be holding the very object the next session opens.
        self._link: SerialLink | None = None
        self._buffer = LogBuffer()
        self._reader: LogReader | None = None
        self._pending: list[LogLine] = []
        self._rerender_pending = False
        self._received = 0
        self._last_error: str | None = None
        self._attempts = 0
        # Readers that would not stop. Held so neither Qt nor Python frees a
        # thread object while its OS thread is still running.
        self._orphaned_readers: list[LogReader] = []

        # Single-shot rather than repeating: each attempt arms the next one only
        # if it failed, so a timer that is armed always means "an attempt is
        # pending" and never fires into an established session. Built before the
        # UI, because _update_status reads it to describe the current state.
        self._retry_timer = QTimer(self)
        self._retry_timer.setSingleShot(True)
        self._retry_timer.setInterval(retry_interval_ms)
        self._retry_timer.timeout.connect(self._attempt)

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
        self._tag_panel.tags_refresh_requested.connect(self._on_tags_refresh_requested)

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

    @property
    def _is_trying(self) -> bool:
        """True while an attempt is pending, i.e. between failures."""
        return self._retry_timer.isActive()

    def _on_connect_clicked(self) -> None:
        if self._is_streaming or self._is_trying:
            self._stop()
        else:
            self._start()

    def _start(self) -> None:
        """Begin a session, retrying every interval until the port opens."""
        if self._port_combo.currentData() is None:
            # Via _report_error, not showMessage: the flush timer rewrites the
            # status text every 100 ms and would erase it before it was read.
            # Not retried — no port is selected to retry against.
            self._report_error("No serial port selected")
            return

        self._attempts = 0
        self._last_error = None
        # Held so the button reads Disconnect from the first attempt onwards:
        # the user asked for one control that stops whatever is going on, and
        # for that the retrying state has to look like a session in progress.
        self._set_busy_ui(True)
        self._attempt()

    def _attempt(self) -> None:
        """One connection attempt. Arms the next if this one fails."""
        device = self._port_combo.currentData()
        if device is None:
            # The combo cannot change while busy, so this means the port list
            # was rebuilt under us. Nothing to attempt.
            self._stop()
            return

        self._attempts += 1

        # A fresh link per attempt. If a previous reader had to be parked it
        # still holds the *previous* link, which was closed, so it can neither
        # read this session's port nor write its own extract_logs to it — and it
        # cannot race this session on a shared receive buffer.
        link = self._link_factory()
        try:
            link.open(device)
        except SerialLinkError as exc:
            self._report_once(f"Could not open {device}: {exc}")
            self._retry_timer.start()
            self._update_status()
            return

        self._link = link

        # A fresh authenticator too. It caches the charger ID it was told and
        # the token it fetched, both of which belong to the device that was
        # connected — reusing them after a swap would sign for the wrong unit.
        self._reader = LogReader(link, authenticator=self._authenticator_factory())
        self._reader.line_received.connect(self._on_line_received)
        self._reader.error.connect(self._on_reader_error)
        self._reader.auth_failed.connect(self._on_auth_failed)
        self._reader.finished.connect(self._on_reader_finished)
        self._reader.start()
        self._update_status()

    def _stop(self) -> None:
        """Stop streaming and stop retrying. The only way out of both."""
        self._retry_timer.stop()
        self._teardown()
        self._set_busy_ui(False)
        self._update_status()

    def _set_busy_ui(self, busy: bool) -> None:
        self._connect_btn.setText("Disconnect" if busy else "Connect")
        self._port_combo.setEnabled(not busy)
        self._refresh_btn.setEnabled(not busy)

    def _teardown(self) -> None:
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
                # lines into the buffer, or trigger a second teardown, long
                # after the UI said "Disconnected". Parked means inert.
                reader.line_received.disconnect(self._on_line_received)
                reader.error.disconnect(self._on_reader_error)
                reader.auth_failed.disconnect(self._on_auth_failed)
                reader.finished.disconnect(self._on_reader_finished)
                self._orphaned_readers.append(reader)
                self._report_error(
                    f"Reader thread did not stop within "
                    f"{_READER_STOP_TIMEOUT_MS / 1000:g}s; leaving it parked"
                )

        # Closed even for a parked reader: the link drops its port reference,
        # so the orphan's next call raises instead of touching the device.
        link, self._link = self._link, None
        if link is not None:
            link.close()

    def _on_reader_error(self, message: str) -> None:
        """A failure worth another attempt: tear down and go back to trying.

        The buffer survives, so what was captured before the failure stays
        readable and filterable — a device that reboots mid-diagnosis does not
        cost you the log that led up to it.
        """
        self._report_once(message)
        self._teardown()
        self._retry_timer.start()
        self._update_status()

    def _on_auth_failed(self, message: str) -> None:
        """A refusal: stop. Retrying it would only repeat the refusal."""
        self._report_error(message)
        self._stop()

    def _report_error(self, message: str) -> None:
        """Show *message* in the pane and keep it in the status bar.

        Held in ``_last_error`` rather than pushed straight to the status bar,
        because ``_update_status`` runs on every 100 ms flush and would
        otherwise overwrite it before it could be read.
        """
        self._last_error = message
        self._pane.append_error(message)
        self._update_status()

    def _report_once(self, message: str) -> None:
        """Report *message* unless it is the one already showing.

        A missing port fails identically every second. Reported unconditionally
        that is a line of pane per second, burying whatever preceded it; the
        status bar carries the attempt count instead, so nothing is lost by
        staying quiet about the repeat.
        """
        if message == self._last_error:
            return
        self._report_error(message)

    def _on_reader_finished(self) -> None:
        # Only reached without a preceding error signal when the reader stopped
        # on its own, which means it was interrupted — and the only interrupter
        # is _stop, which has already torn everything down.
        if self._is_streaming:
            self._teardown()
            self._retry_timer.start()
            self._update_status()

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

    def _on_tags_refresh_requested(self) -> None:
        """Rebuild the tag list from the lines currently held.

        The buffer rediscovers its tags, the panel drops its rows so the next
        flush rebuilds them, and the pane is re-rendered because every tag comes
        back enabled — lines hidden by the old filter have to reappear.
        """
        self._buffer.refresh_tags()
        self._tag_panel.reset()
        self._rerender_pending = True
        self._sync_tags()
        self._update_status()

    def _on_clear_clicked(self) -> None:
        self._buffer.clear()
        self._pending.clear()
        self._rerender_pending = False
        self._pane.clear()
        self._received = 0
        # Clear is the user saying "start from what I can see now", and the
        # pane's [error] notice goes with it, so the status bar must not keep
        # reporting an error whose line has just been wiped.
        self._last_error = None
        # Tags and their check states deliberately survive a clear.
        self._sync_tags()
        self._update_status()

    def _update_status(self) -> None:
        if self._is_streaming:
            state = f"Streaming from {self._port_combo.currentData()}"
        elif self._is_trying:
            state = f"Retrying {self._port_combo.currentData()} " f"(attempt {self._attempts})"
        else:
            state = "Disconnected"
        # Read from the pane rather than counted here, so it stays exact across
        # both block-cap eviction and full re-renders. That makes it a count of
        # everything on display, [error] notices included — hence "in view"
        # rather than "shown", which would read as a count of log lines and be
        # wrong by one every time an error is reported.
        text = f"{state}   |   received: {self._received}   in view: {self._pane.line_count()}"
        if self._last_error is not None:
            text = f"{text}   |   last error: {self._last_error}"
        self.statusBar().showMessage(text)

    # ── lifecycle ────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:  # type: ignore[override]
        """Stop the reader thread before the process exits."""
        self._flush_timer.stop()
        self._stop()
        event.accept()
