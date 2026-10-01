"""Which picture the pill shows (windows/overlay_mode.py): a pure function, so every branch is tested without Tk.
The drawing itself (overlay.py, Tk) is not tested."""
import overlay_mode as om

NOW = 100.0


def mode(state="idle", kind="", until=0.0, meeting=False):
    return om.overlay_mode(state, kind, until, NOW, meeting)


def test_idle_with_nothing_to_show_is_hidden():
    assert mode() is None


def test_recording_and_busy_show_themselves():
    assert mode("rec") == "rec" and mode("busy") == "busy"


def test_a_running_flash_shows_while_idle():
    assert mode(kind="sent", until=NOW + 0.1) == "sent"
    assert mode(kind="error", until=NOW + 0.1) == "error"


def test_an_expired_flash_shows_nothing():
    assert mode(kind="sent", until=NOW) is None            # the deadline itself is over
    assert mode(kind="error", until=NOW - 1) is None


def test_no_kind_means_no_flash_even_with_a_future_deadline():
    assert mode(kind="", until=NOW + 5) is None


def test_a_flash_is_ignored_while_recording_or_busy():
    assert mode("rec", "sent", NOW + 1) == "rec"
    assert mode("busy", "error", NOW + 1) == "busy"


def test_the_meeting_timer_shows_while_idle():
    assert mode(meeting=True) == "meet"


def test_a_flash_wins_over_the_meeting_timer_and_the_timer_returns_after():
    assert mode(kind="sent", until=NOW + 0.5, meeting=True) == "sent"
    assert mode(kind="sent", until=NOW, meeting=True) == "meet"


def test_recording_and_busy_win_over_the_meeting_timer():
    assert mode("rec", meeting=True) == "rec" and mode("busy", meeting=True) == "busy"
