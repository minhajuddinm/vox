"""The extra shortcuts of the Windows app: the pure parse and the conflict rules (no keyboard, no audio).

The dictation shortcut (`hotkey`, a list of modifier names) and the note shortcut (`note_hotkey`, session.py) existed
before; this module adds four optional ones, all written like the note shortcut ("shift+alt+z"):

  hands_free_hotkey  default ctrl+cmd+space  starts hands-free dictation, or ends it (sends what was said). While the
                                             dictation shortcut is already held (Ctrl+Win), adding its last key latches
                                             the dictation that started, so it may contain the dictation shortcut.
  paste_last_hotkey  default shift+alt+z     pastes the last dictation again into the focused window.
  copy_last_hotkey   default off             puts the last dictation on the clipboard.
  command_hotkey     default off             edit by voice (experimental): modifiers only (for example ctrl+cmd+alt),
                                             hold to say how the selected text should change. May contain the
                                             dictation shortcut: adding the extra key just after the dictation started
                                             turns that recording into an instruction.

`hotkey_style` (classic | hold_or_tap) says what a quick tap of the dictation shortcut does: classic throws the tap
away (a second tap starts keep listening), hold_or_tap latches hands-free dictation until the next press.
"""
from collections import namedtuple

STYLES = ("classic", "hold_or_tap")
HOLD_SECONDS = 0.3   # a press of the dictation shortcut shorter than this is a tap (engine.TAP_SECONDS)

# setting -> (how messages name it, its default, modifiers only)
SHORTCUTS = {
    "hands_free_hotkey": ("the hands-free shortcut", "ctrl+cmd+space", False),
    "paste_last_hotkey": ("the paste-last shortcut", "shift+alt+z", False),
    "copy_last_hotkey": ("the copy-last shortcut", "", False),
    "command_hotkey": ("the edit-by-voice shortcut", "", True),
}
# shortcuts that may hold the dictation shortcut (they extend it); the others would start a dictation first
EXTENDS_DICTATION = ("hands_free_hotkey", "command_hotkey")

_MODS = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "cmd": "Win"}   # in the order they are written
_MOD_NAMES = {"control": "ctrl", "ctl": "ctrl", "option": "alt", "win": "cmd", "windows": "cmd", "super": "cmd"}
_DICTATION_KEYS = {"ctrl", "ctrl_l", "ctrl_r", "cmd", "alt", "alt_r", "shift", "space"}   # engine.KEY_ALIASES

Chord = namedtuple("Chord", "text mods vk label")   # vk: Windows virtual-key code of the main key; None: modifiers only


def _vk(key):
    """Virtual-key code of a main key: a letter, a digit, F1-F12 or space; None otherwise."""
    if len(key) == 1 and key.isascii() and key.isalnum():
        return ord(key.upper())
    if key == "space":
        return 0x20
    if key.startswith("f") and key[1:].isdigit() and key[1:] == str(int(key[1:])) and 1 <= int(key[1:]) <= 12:
        return 0x6F + int(key[1:])
    return None


def _key_label(key):
    return "Space" if key == "space" else key.upper()


def parse(text, mods_only=False):
    """A shortcut from its setting: (Chord, "") when usable, (None, "") when empty (off), (None, problem) otherwise.
    A keyed shortcut is Ctrl, Alt or Win (Shift may join) plus one letter, digit, F1-F12 or Space. A modifiers-only
    one (`mods_only`) is two or more of Ctrl, Alt, Shift, Win with at least one that is not Shift."""
    if text is None or (isinstance(text, str) and not text.strip()):
        return None, ""
    if mods_only:
        hint = "Use two or more of Ctrl, Alt, Shift and Win, for example ctrl+cmd+alt."
    else:
        hint = "Use Ctrl, Alt or Win plus one letter, digit, F key or space, for example shift+alt+z."
    parts = [_MOD_NAMES.get(p.strip(), p.strip()) for p in text.lower().split("+")] if isinstance(text, str) else []
    mods = {p for p in parts if p in _MODS}
    keys = [p for p in parts if p not in _MODS]
    if not parts or len(set(parts)) != len(parts) or not mods - {"shift"}:
        return None, hint
    ordered = tuple(m for m in _MODS if m in mods)
    if mods_only:
        if keys or len(mods) < 2:
            return None, hint
        return Chord("+".join(ordered), ordered, None, " + ".join(_MODS[m] for m in ordered)), ""
    if len(keys) != 1 or _vk(keys[0]) is None:
        return None, hint
    label = " + ".join([_MODS[m] for m in ordered] + [_key_label(keys[0])])
    return Chord("+".join(ordered + (keys[0],)), ordered, _vk(keys[0]), label), ""


def dictation_mods(hotkey):
    """The dictation shortcut (the `hotkey` setting, for example ["ctrl_l", "cmd"]) as a set of modifier names; the
    default Ctrl + Win when the setting is missing or unusable. A custom "space" stays "space"."""
    keys = {k.split("_")[0] for k in hotkey if k in _DICTATION_KEYS} if isinstance(hotkey, (list, tuple)) else set()
    return keys or {"ctrl", "cmd"}


def _names(mods):
    return " + ".join(_MODS.get(m, m.capitalize()) for m in mods)


def check(cfg):
    """{setting: (Chord or None, problem)} for every shortcut of SHORTCUTS, judged together with the dictation and
    note shortcuts in `cfg`. The engine uses only the chords without a problem; Settings shows the problems.

    Refused: an unusable text; a shortcut holding the dictation shortcut unless it may extend it; one that is the same
    as the dictation shortcut, the note shortcut or a shortcut listed before it; a modifiers-only one whose keys are all
    part of another shortcut (it would start whenever that one is pressed)."""
    import session   # the note shortcut's own parse (a sibling module; imported here so the pure part stays light)
    dictation = dictation_mods(cfg.get("hotkey"))
    taken = []   # (Chord, the name of its setting as the user sees it)
    note, _ = session.note_hotkey(cfg)
    if note:
        taken.append((Chord(note.text, note.mods, note.vk, note.label), "the note shortcut"))
    out = {}
    for name, (title, default, mods_only) in SHORTCUTS.items():
        chord, problem = parse(cfg.get(name, default), mods_only)
        if chord and not problem:
            problem = _conflict(name, chord, dictation, taken)
        if problem:
            chord = None
        elif chord:
            taken.append((chord, title))
        out[name] = (chord, problem)
    return out


def _conflict(name, chord, dictation, taken):
    mods = set(chord.mods)
    if chord.vk == 0x20 and "space" in dictation:
        return "Space is part of your dictation shortcut. Pick another key."
    if dictation <= mods and name not in EXTENDS_DICTATION:
        return ("That includes your dictation shortcut (%s), which would start a dictation first. Pick another "
                "combination." % _names(m for m in _MODS if m in dictation))
    if chord.vk is None and mods == dictation:
        return "That is your dictation shortcut. Add a key to it, for example %s + Alt." % _names(m for m in _MODS if m in dictation)
    if chord.vk is None and mods < dictation:
        return "Those keys are part of your dictation shortcut. Pick another combination."
    for other, title in taken:
        if (other.vk, set(other.mods)) == (chord.vk, mods):
            return "Already used by %s." % title
        if chord.vk is None and other.vk is not None and mods <= set(other.mods):
            return "Those keys are part of %s. Pick another combination." % title
    return ""


def style(cfg):
    """The `hotkey_style` setting; anything unknown means classic (today's behaviour)."""
    s = cfg.get("hotkey_style")
    return s if s in STYLES else "classic"


def tap_action(style_name, held_seconds, command=False):
    """What letting go of the dictation shortcut does to the recording in progress (not hands-free):
    "stop" (send it), "cancel" (a tap in classic: thrown away, a second tap may follow) or "latch" (a tap in
    hold_or_tap: keeps recording hands-free). An instruction for edit by voice is never latched."""
    if held_seconds >= HOLD_SECONDS:
        return "stop"
    return "latch" if style_name == "hold_or_tap" and not command else "cancel"
