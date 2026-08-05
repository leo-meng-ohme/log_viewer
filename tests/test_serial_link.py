import pytest

from ohme_log_viewer.serial_link import SerialLink, SerialLinkError


class FakeSerial:
    """Stands in for serial.Serial. Yields one scripted chunk per read()."""

    def __init__(self, chunks=None, raise_on_read=None, raise_on_write=None):
        self._chunks = list(chunks or [])
        self._raise_on_read = raise_on_read
        self._raise_on_write = raise_on_write
        self.written = b""
        self.closed = False

    @property
    def in_waiting(self):
        return len(self._chunks[0]) if self._chunks else 0

    def read(self, _size):
        if self._raise_on_read is not None:
            raise self._raise_on_read
        if not self._chunks:
            return b""  # timeout with nothing available
        return self._chunks.pop(0)

    def write(self, data):
        if self._raise_on_write is not None:
            raise self._raise_on_write
        self.written += data
        return len(data)

    def close(self):
        self.closed = True


def link_with(fake):
    link = SerialLink(serial_factory=lambda **kwargs: fake)
    link.open("/dev/fake")
    return link


def test_open_configures_the_port_as_115200_8n1():
    captured = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return FakeSerial()

    link = SerialLink(read_timeout_s=0.3, serial_factory=factory)
    link.open("/dev/fake")

    assert captured["port"] == "/dev/fake"
    assert captured["baudrate"] == 115200
    assert captured["bytesize"] == 8
    assert captured["parity"] == "N"
    assert captured["stopbits"] == 1
    assert captured["timeout"] == 0.3
    assert link.is_open is True


def test_open_failure_is_reported_as_serial_link_error():
    def factory(**kwargs):
        raise OSError("no such port")

    link = SerialLink(serial_factory=factory)
    with pytest.raises(SerialLinkError, match="no such port"):
        link.open("/dev/nope")
    assert link.is_open is False


def test_write_line_appends_crlf():
    fake = FakeSerial()
    link_with(fake).write_line("extract_logs")
    assert fake.written == b"extract_logs\r\n"


def test_read_line_strips_the_eol():
    link = link_with(FakeSerial([b"I/MODEM [t] up\r\n"]))
    assert link.read_line() == "I/MODEM [t] up"


def test_read_line_reassembles_a_line_split_across_chunks():
    link = link_with(FakeSerial([b"I/MODEM [t] li", b"nk up\r\n"]))
    assert link.read_line() == "I/MODEM [t] link up"


def test_read_line_returns_buffered_lines_one_at_a_time():
    link = link_with(FakeSerial([b"first\r\nsecond\r\n"]))
    assert link.read_line() == "first"
    assert link.read_line() == "second"


def test_read_line_returns_none_on_timeout_without_a_complete_line():
    link = link_with(FakeSerial([b"partial, no newline yet"]))
    assert link.read_line() is None


def test_read_line_gives_up_on_a_stream_that_never_completes_a_line():
    """A newline-less stream must not block the call or grow the buffer forever.

    port.read() never returns empty here, so only the call's own deadline can
    end it. Without that, the reader thread would never regain control to check
    for interruption and Disconnect would hang.
    """

    class EndlessNoise:
        in_waiting = 5

        def read(self, _size):
            return b"noise"

        def write(self, data):
            return len(data)

        def close(self):
            pass

    link = SerialLink(read_timeout_s=0.05, serial_factory=lambda **kwargs: EndlessNoise())
    link.open("/dev/fake")
    assert link.read_line() is None


def test_read_line_keeps_the_partial_line_for_the_next_call():
    fake = FakeSerial([b"par", b""])
    link = link_with(fake)
    assert link.read_line() is None
    fake._chunks.append(b"tial\r\n")
    assert link.read_line() == "partial"


def test_undecodable_bytes_are_replaced_rather_than_raising():
    link = link_with(FakeSerial([b"I/MODEM \xff\xfe up\r\n"]))
    result = link.read_line()
    assert result is not None
    assert "I/MODEM" in result


def test_read_error_is_reported_as_serial_link_error():
    link = link_with(FakeSerial(raise_on_read=OSError("device disconnected")))
    with pytest.raises(SerialLinkError, match="device disconnected"):
        link.read_line()


def test_write_error_is_reported_as_serial_link_error():
    link = link_with(FakeSerial(raise_on_write=OSError("write failed")))
    with pytest.raises(SerialLinkError, match="write failed"):
        link.write_line("extract_logs")


def test_operations_on_a_closed_link_raise():
    link = SerialLink(serial_factory=lambda **kwargs: FakeSerial())
    with pytest.raises(SerialLinkError, match="not connected"):
        link.read_line()
    with pytest.raises(SerialLinkError, match="not connected"):
        link.write_line("x")


def test_close_closes_the_port_and_is_idempotent():
    fake = FakeSerial()
    link = link_with(fake)
    link.close()
    link.close()
    assert fake.closed is True
    assert link.is_open is False


def test_reopening_discards_the_previous_partial_buffer():
    first = FakeSerial([b"leftover partial"])
    link = SerialLink(serial_factory=lambda **kwargs: first)
    link.open("/dev/fake")
    assert link.read_line() is None  # buffers the partial text

    second = FakeSerial([b"clean\r\n"])
    link._serial_factory = lambda **kwargs: second
    link.open("/dev/fake")
    assert link.read_line() == "clean"
