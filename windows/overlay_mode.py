"""Which picture the pill shows. A pure function (no Tk), so every branch is tested (tests/test_overlay_mode.py)."""

MAX_FLASH_SECONDS = 5.0   # no flash shows longer than this from now, whatever its deadline (the longest is 1.8 s)


def overlay_mode(state, flash_kind, flash_until, now, meeting_active):
    """"rec" | "busy" | "sent" | "error" | "meet" | None (None: the pill is hidden).

    Recording and sending always show themselves. Only while idle: a running flash ("sent" or "error", until
    `flash_until`, never more than MAX_FLASH_SECONDS ahead) shows over the meeting timer, and the timer returns once
    the flash is over."""
    if state != "idle":
        return state
    if flash_kind and now < flash_until <= now + MAX_FLASH_SECONDS:
        return flash_kind
    return "meet" if meeting_active else None


def pill_clock(seconds):
    """Elapsed time as the pill shows it: "5:07", or "1:02:05" from one hour."""
    s = int(seconds)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"
