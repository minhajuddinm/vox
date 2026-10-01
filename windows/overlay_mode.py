"""Which picture the pill shows. A pure function (no Tk), so every branch is tested (tests/test_overlay_mode.py)."""


def overlay_mode(state, flash_kind, flash_until, now, meeting_active):
    """"rec" | "busy" | "sent" | "error" | "meet" | None (None: the pill is hidden).

    Recording and sending always show themselves. Only while idle: a running flash ("sent" or "error", until
    `flash_until`) shows over the meeting timer, and the timer returns once the flash is over."""
    if state != "idle":
        return state
    if flash_kind and now < flash_until:
        return flash_kind
    return "meet" if meeting_active else None
