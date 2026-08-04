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
