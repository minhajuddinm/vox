"""A cheap drift guard: the flash lengths are kept by hand in Python (engine.py FLASH_SECONDS) and in Java
(BubbleView SENT_MS / ERROR_MS). They must stay equal."""
import os
import re

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")


def test_bubble_flash_lengths_match_the_pill():
    for mod in ("pynput", "pystray", "sounddevice", "pyperclip", "psutil"):
        pytest.importorskip(mod)
    import engine
    src = open(os.path.join(ROOT, "android", "src", "com", "minhaj", "vox", "BubbleView.java"), encoding="utf-8").read()
    m = re.search(r"SENT_MS\s*=\s*(\d+)\s*,\s*ERROR_MS\s*=\s*(\d+)", src)
    assert m, "BubbleView.java no longer declares SENT_MS, ERROR_MS in one line"
    assert int(m.group(1)) / 1000 == engine.FLASH_SECONDS["sent"]
    assert int(m.group(2)) / 1000 == engine.FLASH_SECONDS["error"]


def test_every_flash_fits_under_the_pills_flash_cap():
    """overlay_mode drops a flash whose deadline is more than MAX_FLASH_SECONDS ahead: a real one must fit under it."""
    for mod in ("pynput", "pystray", "sounddevice", "pyperclip", "psutil"):
        pytest.importorskip(mod)
    import engine
    import overlay_mode
    assert max(engine.FLASH_SECONDS.values()) < overlay_mode.MAX_FLASH_SECONDS
