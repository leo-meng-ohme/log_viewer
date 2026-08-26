from ohme_log_viewer.log_buffer import UNPARSED_TAG, LogBuffer
from ohme_log_viewer.log_line import parse


def line(tag: str, message: str = "msg", level: str = "I"):
    return parse(f"{level}/{tag} [2026-07-01T10:11:11.950Z] {message}")


def messages(buffer: LogBuffer) -> list[str]:
    return [item.message for item in buffer.visible()]


def test_appended_line_is_visible_by_default():
    buffer = LogBuffer()
    assert buffer.append(line("MODEM", "one")) is True
    assert messages(buffer) == ["one"]


def test_tags_are_discovered_in_arrival_order():
    buffer = LogBuffer()
    buffer.append(line("MODEM"))
    buffer.append(line("OCPP"))
    buffer.append(line("MODEM"))
    assert buffer.tags() == ["MODEM", "OCPP"]


def test_counts_are_kept_per_tag():
    buffer = LogBuffer()
    buffer.append(line("MODEM"))
    buffer.append(line("MODEM"))
    buffer.append(line("OCPP"))
    assert buffer.count_for("MODEM") == 2
    assert buffer.count_for("OCPP") == 1
    assert buffer.count_for("NOPE") == 0


def test_disabling_a_tag_hides_its_lines_and_reports_new_ones_hidden():
    buffer = LogBuffer()
    buffer.append(line("MODEM", "shown"))
    buffer.set_tag_enabled("MODEM", False)
    assert buffer.append(line("MODEM", "hidden")) is False
    assert messages(buffer) == []
    assert buffer.is_tag_enabled("MODEM") is False


def test_filtering_is_retroactive():
    buffer = LogBuffer()
    buffer.append(line("MODEM", "early"))
    buffer.append(line("OCPP", "other"))
    buffer.set_tag_enabled("MODEM", False)
    assert messages(buffer) == ["other"]
    buffer.set_tag_enabled("MODEM", True)
    assert messages(buffer) == ["early", "other"]


def test_unparsed_lines_group_under_the_unparsed_tag():
    buffer = LogBuffer()
    buffer.append(parse("MainMenu> "))
    assert buffer.tags() == [UNPARSED_TAG]
    assert buffer.count_for(UNPARSED_TAG) == 1
    buffer.set_tag_enabled(UNPARSED_TAG, False)
    assert messages(buffer) == []


def test_clear_empties_lines_but_keeps_tags_and_their_enabled_state():
    buffer = LogBuffer()
    buffer.append(line("MODEM"))
    buffer.append(line("OCPP"))
    buffer.set_tag_enabled("OCPP", False)

    buffer.clear()

    assert messages(buffer) == []
    assert buffer.total() == 0
    assert buffer.tags() == ["MODEM", "OCPP"]
    assert buffer.is_tag_enabled("OCPP") is False
    assert buffer.count_for("MODEM") == 0


def test_oldest_lines_are_evicted_at_the_cap():
    buffer = LogBuffer(max_lines=3)
    for index in range(5):
        buffer.append(line("MODEM", str(index)))
    assert messages(buffer) == ["2", "3", "4"]
    assert buffer.total() == 3


def test_setting_an_unknown_tag_is_ignored():
    buffer = LogBuffer()
    buffer.set_tag_enabled("NEVER_SEEN", False)
    assert buffer.tags() == []


def test_lines_yields_everything_stored_ignoring_the_filter():
    buffer = LogBuffer()
    buffer.append(line("MODEM", "one"))
    buffer.append(line("OCPP", "two"))
    buffer.set_tag_enabled("MODEM", False)
    assert [item.message for item in buffer.lines()] == ["one", "two"]


# ── refreshing the tag list ──────────────────────────────────────────────


def test_refresh_tags_forgets_tags_with_no_lines_left():
    buffer = LogBuffer()
    buffer.append(parse("I/MODEM [t] one"))
    buffer.clear()  # keeps tags by design; refresh is how you get rid of them
    buffer.refresh_tags()
    assert buffer.tags() == []


def test_refresh_tags_rebuilds_from_the_lines_still_stored():
    buffer = LogBuffer()
    buffer.append(parse("I/MODEM [t] one"))
    buffer.append(parse("I/OCPP  [t] two"))
    buffer.refresh_tags()
    assert sorted(buffer.tags()) == ["MODEM", "OCPP"]
    assert buffer.count_for("MODEM") == 1
    assert buffer.count_for("OCPP") == 1


def test_refresh_tags_re_enables_every_tag():
    buffer = LogBuffer()
    buffer.append(parse("I/MODEM [t] one"))
    buffer.set_tag_enabled("MODEM", False)
    buffer.refresh_tags()
    assert buffer.is_tag_enabled("MODEM") is True
    assert [line.message for line in buffer.visible()] == ["one"]


def test_refresh_tags_counts_only_what_is_stored():
    # Counts otherwise total everything received, which on a long session runs
    # ahead of what the capped buffer still holds.
    buffer = LogBuffer(max_lines=2)
    for index in range(5):
        buffer.append(parse(f"I/MODEM [t] line {index}"))
    assert buffer.count_for("MODEM") == 5
    buffer.refresh_tags()
    assert buffer.count_for("MODEM") == 2


def test_refresh_tags_drops_the_unparsed_group_once_it_is_empty():
    buffer = LogBuffer()
    buffer.append(parse("not a log line"))
    buffer.append(parse("I/MODEM [t] one"))
    assert UNPARSED_TAG in buffer.tags()
    buffer.clear()
    buffer.append(parse("I/MODEM [t] two"))
    buffer.refresh_tags()
    assert buffer.tags() == ["MODEM"]
