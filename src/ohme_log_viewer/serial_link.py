"""Line-buffered serial transport, written directly against pyserial.

Reads are chunk-based and a chunk boundary can fall mid-line, so incoming
bytes accumulate in a buffer and only complete lines are handed out. Callers
therefore never see half a log line.
"""

from __future__ import annotations

from dataclasses import dataclass

import serial
from serial.tools import list_ports

#: Fixed device parameters (115200 8N1).
_BAUD = 115200
_BYTESIZE = 8
_PARITY = "N"
_STOPBITS = 1
_EOL = b"\r\n"

_WRITE_TIMEOUT_S = 1.0


class SerialLinkError(Exception):
    """Any failure opening, reading from or writing to the port."""


@dataclass(frozen=True)
class PortInfo:
    """A serial port offered to the user."""

    device: str
    label: str


def list_usb_ports() -> list[PortInfo]:
    """Enumerate serial ports, annotating USB devices with their VID:PID."""
    found: list[PortInfo] = []
    for port in list_ports.comports():
        label = port.description or port.device
        if port.vid is not None and port.pid is not None:
            label = f"{label} ({port.vid:04X}:{port.pid:04X})"
        found.append(PortInfo(device=port.device, label=label))
    return found


class SerialLink:
    """Open/close a port and exchange whole lines with the device."""

    def __init__(self, read_timeout_s: float = 0.3, serial_factory=serial.Serial) -> None:
        self._read_timeout_s = read_timeout_s
        self._serial_factory = serial_factory
        self._port = None
        self._buf = bytearray()

    @property
    def is_open(self) -> bool:
        return self._port is not None

    def open(self, device: str, baud: int = _BAUD) -> None:
        """Open *device*. Raises SerialLinkError if it cannot be opened."""
        self.close()
        try:
            self._port = self._serial_factory(
                port=device,
                baudrate=baud,
                bytesize=_BYTESIZE,
                parity=_PARITY,
                stopbits=_STOPBITS,
                timeout=self._read_timeout_s,
                write_timeout=_WRITE_TIMEOUT_S,
            )
        except (serial.SerialException, OSError, ValueError) as exc:
            self._port = None
            raise SerialLinkError(str(exc)) from exc
        # A partial line left over from a previous session would corrupt the
        # first line of this one.
        self._buf.clear()

    def close(self) -> None:
        """Close the port if open. Safe to call repeatedly."""
        if self._port is None:
            return
        try:
            self._port.close()
        except (serial.SerialException, OSError):
            pass  # already gone; nothing useful to do while tearing down
        self._port = None

    def write_line(self, text: str) -> None:
        """Send *text* followed by CRLF."""
        port = self._require_port()
        try:
            port.write(text.encode("utf-8") + _EOL)
        except (serial.SerialException, OSError) as exc:
            raise SerialLinkError(str(exc)) from exc

    def read_line(self) -> str | None:
        """Return the next complete line, or None if the read timed out.

        Any bytes read that do not yet form a complete line stay buffered for
        the following call.
        """
        port = self._require_port()
        while True:
            newline = self._buf.find(b"\n")
            if newline >= 0:
                raw = bytes(self._buf[:newline])
                del self._buf[: newline + 1]
                return raw.decode("utf-8", errors="replace").rstrip("\r")

            try:
                pending = port.in_waiting or 1
                chunk = port.read(pending)
            except (serial.SerialException, OSError) as exc:
                raise SerialLinkError(str(exc)) from exc

            if not chunk:
                return None  # timed out; keep whatever is buffered
            self._buf.extend(chunk)

    def _require_port(self):
        if self._port is None:
            raise SerialLinkError("not connected")
        return self._port
