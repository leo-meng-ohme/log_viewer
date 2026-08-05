from ohme_log_viewer.log_line import LogLine, parse


def test_parses_a_normal_info_line():
    line = parse("I/MODEM [2026-07-01T10:11:11.950Z] Registered on network")
    assert line.level == "I"
    assert line.tag == "MODEM"
    assert line.timestamp == "2026-07-01T10:11:11.950Z"
    assert line.message == "Registered on network"
    assert line.source is None


def test_parses_a_short_padded_tag_without_padding_artefacts():
    # A 3-char tag gets 1 pad space plus the mandatory separator space.
    line = parse("I/OTA   [2026-07-01T10:11:11.950Z] Download started")
    assert line.tag == "OTA"
    assert line.message == "Download started"


def test_parses_a_long_unpadded_tag():
    raw = "W/CHARGING_SESSION_MGR [2026-07-01T10:11:11.950Z] Session aborted"
    line = parse(raw)
    assert line.tag == "CHARGING_SESSION_MGR"
    assert line.level == "W"
    assert line.message == "Session aborted"


def test_parses_every_level_letter():
    for letter in ("A", "E", "W", "I", "D", "V"):
        line = parse(f"{letter}/TAG  [2026-07-01T10:11:11.950Z] hello")
        assert line.level == letter, letter
        assert line.tag == "TAG"


def test_parses_assert_line_with_source_info_and_strips_timestamp():
    # Assert lines enable DIR|FUNC|LINE, and the empty thread-info field
    # leaves a trailing space inside the bracket group.
    raw = "A/SAFETY [2026-07-01T10:11:11.950Z ] (bsp_rcd.c check_rcd:142)RCD self-test failed"
    line = parse(raw)
    assert line.level == "A"
    assert line.tag == "SAFETY"
    assert line.timestamp == "2026-07-01T10:11:11.950Z"
    assert line.source == "bsp_rcd.c check_rcd:142"
    assert line.message == "RCD self-test failed"


def test_non_assert_parenthesised_text_stays_in_the_message():
    # Only assert lines carry source info, so this must not be mistaken for it.
    line = parse("I/OCPP  [2026-07-01T10:11:11.950Z] (retry 3) connecting")
    assert line.source is None
    assert line.message == "(retry 3) connecting"


def test_accepts_the_device_fallback_timestamp():
    line = parse("E/RTC   [0000-00-00T00:00:00.000Z] clock not set")
    assert line.timestamp == "0000-00-00T00:00:00.000Z"
    assert line.message == "clock not set"


def test_strips_ansi_escapes_before_parsing():
    line = parse("\x1b[32mI/MODEM [2026-07-01T10:11:11.950Z] link up\x1b[0m")
    assert line.tag == "MODEM"
    assert line.message == "link up"
    assert "\x1b" not in line.raw


def test_unparseable_line_keeps_its_text_under_a_null_tag():
    line = parse("MainMenu> ")
    assert line.level is None
    assert line.tag is None
    assert line.timestamp is None
    assert line.message == "MainMenu> "
    assert isinstance(line, LogLine)


def test_strips_trailing_eol():
    line = parse("I/MODEM [2026-07-01T10:11:11.950Z] link up\r\n")
    assert line.message == "link up"


from ohme_log_viewer.log_line import is_noise


def test_the_completion_sentinel_is_noise():
    assert is_noise("LOG_EXTRACT_DONE") is True
    assert is_noise("LOG_EXTRACT_DONE\r\n") is True


def test_blank_lines_are_noise():
    # Firmware emits a bare CRLF to flush a line left dangling by a
    # chunk-boundary-unaligned read.
    assert is_noise("") is True
    assert is_noise("\r\n") is True
    assert is_noise("   ") is True


def test_legacy_banner_lines_are_noise():
    for banner in (
        "========== LOG EXTRACTION START ==========",
        "========== LOG EXTRACTION END ==========",
        "No archived logs found, checking ring buffer...",
        "Ring buffer is empty",
        "Ring buffer has been cleared.",
        "Found 3 archived log file(s), reading in chronological order",
        "--- Archive 1/3: log_20260701.txt (512 bytes) ---",
        "--- Ring buffer: 78 bytes ---",
    ):
        assert is_noise(banner) is True, banner


def test_real_log_lines_are_not_noise():
    assert is_noise("I/MODEM [2026-07-01T10:11:11.950Z] link up") is False


def test_device_warnings_and_errors_are_not_noise():
    # These indicate a real problem on the device and must stay visible.
    assert is_noise("Warning: Could not delete log_20260701.txt") is False
    assert is_noise("Error reading archive") is False


# ── chunk padding ────────────────────────────────────────────────────────
#
# The CLI framework transmits its whole 512-byte TX buffer for every chunk of a
# continued command, so each extract_logs chunk arrives padded with NULs. They
# carry no text and have no glyph, and there is no newline among them to make
# them a line of their own.

_PADDING = "\x00" * 512


def test_a_chunk_of_pure_padding_is_noise():
    assert is_noise(_PADDING) is True


def test_padding_around_framing_text_still_leaves_it_noise():
    assert is_noise(_PADDING + "LOG_EXTRACT_DONE" + _PADDING) is True


def test_padding_before_a_log_line_does_not_stop_it_parsing():
    line = parse(_PADDING + "I/MODEM [2026-07-01T10:11:11.950Z] Registered")
    assert line.level == "I"
    assert line.tag == "MODEM"
    assert line.message == "Registered"


def test_padding_after_a_log_line_is_not_part_of_its_message():
    line = parse("I/MODEM [2026-07-01T10:11:11.950Z] link up" + _PADDING)
    assert line.message == "link up"
    assert "\x00" not in line.raw


def test_other_control_bytes_carrying_no_text_are_dropped_too():
    assert is_noise("\x01\x02\x7f") is True


def test_a_tab_inside_a_message_survives():
    line = parse("I/OCPP  [2026-07-01T10:11:11.950Z] key\tvalue")
    assert line.message == "key\tvalue"


def test_a_replacement_character_is_kept():
    # U+FFFD is a byte that did not decode — evidence of real corruption on the
    # wire, not padding. Hiding it would hide the problem.
    assert is_noise("�") is False
