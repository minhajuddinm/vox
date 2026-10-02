"""Keep listening in the engine: the double press, Esc, the tray menu, the setting, the microphone and the pill state.
The session itself is tested in test_listen.py. Needs the Windows runtime packages; skipped where they are missing."""
import time

import pytest

for _mod in ("pynput", "pystray", "sounddevice", "pyperclip", "psutil"):
    pytest.importorskip(_mod)

import engine as engine_mod  # noqa: E402
import notes  # noqa: E402
import session  # noqa: E402
import vox_core as core  # noqa: E402

Key = engine_mod.keyboard.Key


class FakeIcon:
    icon = None


class FakeListening:
    """Stands in for listen.Listening: remembers how the engine used it."""
    made = []

    def __init__(self, host, cfg, target, buffer=None, focus=None, after=None):
        self.host, self.cfg, self.target, self.buffer, self.after = host, cfg, target, buffer, after
        self.exe = "notepad.exe"
        self.calls = []
        FakeListening.made.append(self)

    def audio(self, *a):
        pass

    def start(self):
        self.calls.append("start")

    def stop(self):
        self.calls.append("stop")

    def cancel(self):
        self.calls.append("cancel")

    def replay(self, pcm):
        self.calls.append(("replay", len(pcm)))


@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    FakeListening.made = []
    e = object.__new__(engine_mod.Engine)
    e.recording = e.busy = e.hands_free = e.note_mode = False
    e.cfg, e.target, e.pending, e.streaming = {"keep_history": False}, "", None, None
    e.pressed, e.last_tap_t, e.press_t, e.combo_was_down = set(), 0.0, 0.0, False
    e.hotkey = [engine_mod.KEY_ALIASES["ctrl"], engine_mod.KEY_ALIASES["cmd"]]
    e.messages, e.states, e.flashes, e.opened = [], [], [], []
    e.state, e.level, e.flash_kind, e.flash_until = "idle", 0.0, "", 0.0
    e.icon, e.overlay = FakeIcon(), None
    e.set_state = e.states.append
    e.sync = type("S", (), {"triggered": 0, "trigger": lambda self: setattr(self, "triggered", self.triggered + 1)})()
    e.notify = lambda m, private=False: e.messages.append(m)
    e.flash = e.flashes.append
    e._rec_lock = engine_mod.threading.Lock()
    e._close_stream = lambda: e.opened.append("closed")
    monkeypatch.setattr(engine_mod.listen_mod, "Listening", FakeListening)
    monkeypatch.setattr(engine_mod, "open_window", lambda: None)
    monkeypatch.setattr(core, "endpoint_error", lambda cfg: "")
    monkeypatch.setattr(core, "key_missing", lambda cfg: False)
    monkeypatch.setattr(core, "warm", lambda cfg: None)
    return e


class Stream:
    def __init__(self, **kw):
        self.kw, self.started = kw, False

    def start(self):
        self.started = True


@pytest.fixture
def mic(monkeypatch):
    monkeypatch.setattr(engine_mod.sd, "InputStream", Stream)


# ------------------------------------------------------------------ the double press
def press(e, key=Key.cmd):
    e.pressed = {key}
    e.on_combo_down()


def test_a_double_press_starts_a_listening_session_not_a_hands_free_dictation(eng, monkeypatch):
    calls = []
    monkeypatch.setattr(eng, "start", lambda: calls.append("start") or setattr(eng, "recording", True))
    monkeypatch.setattr(eng, "start_listening", lambda: calls.append("listen"))
    eng.kb = type("K", (), {"tap": lambda self, k: None})()
    press(eng, Key.ctrl)                          # a hold or the first tap starts a dictation
    eng.on_combo_up()                             # released at once: a tap, cancelled
    assert calls == ["start"] and eng.last_tap_t > 0
    press(eng, Key.ctrl)                          # the second press within the gap
    assert calls == ["start", "listen"] and not eng.hands_free


def test_a_slow_second_press_is_just_another_dictation(eng, monkeypatch):
    calls = []
    monkeypatch.setattr(eng, "start", lambda: calls.append("start"))
    monkeypatch.setattr(eng, "start_listening", lambda: calls.append("listen"))
    eng.last_tap_t = time.time() - 5
    press(eng, Key.ctrl)
    assert calls == ["start"]


def test_while_listening_one_press_does_nothing_and_a_double_press_stops(eng):
    lis = FakeListening(eng, {}, "note")
    eng.listening = lis
    press(eng, Key.ctrl)
    assert lis.calls == []                        # Ctrl+Win alone may be part of another shortcut
    press(eng, Key.ctrl)
    assert lis.calls == ["stop"]


def test_two_presses_far_apart_do_not_stop_a_session(eng):
    lis = FakeListening(eng, {}, "note")
    eng.listening = lis
    press(eng, Key.ctrl)
    eng.last_tap_t = time.time() - 5
    press(eng, Key.ctrl)
    assert lis.calls == []


def test_esc_cancels_a_session_and_is_ignored_otherwise(eng):
    eng.on_press(Key.esc)
    lis = FakeListening(eng, {}, "note")
    eng.listening = lis
    eng.on_press(Key.esc)
    assert lis.calls == ["cancel"]                # nothing more is sent and no note is saved (listen.Listening.cancel)


def test_esc_leaves_a_session_that_is_already_saving_alone(eng):
    lis = FakeListening(eng, {}, "note")
    eng.listening, eng.busy = lis, True
    eng.on_press(Key.esc)
    assert lis.calls == []


def test_a_busy_session_ignores_the_hotkey(eng):
    lis = FakeListening(eng, {}, "note")
    eng.listening, eng.busy = lis, True           # finishing: cleaning and saving
    press(eng, Key.ctrl)
    press(eng, Key.ctrl)
    assert lis.calls == []


def test_notes_and_retries_wait_while_a_session_runs(eng):
    eng.listening = FakeListening(eng, {}, "note")
    eng.toggle_note()
    assert not eng.note_mode
    eng.pending = (b"x", "a.exe", False)
    eng.retry_last()
    assert eng.states == [] and eng.pending is not None


# ------------------------------------------------------------------ starting and stopping
def test_start_listening_opens_the_microphone_and_shows_the_pill(eng, mic, tmp_path):
    eng.cfg["listen_target"] = "type"
    eng.start_listening()
    lis = eng.listening
    assert lis is FakeListening.made[0] and lis.target == "type" and lis.calls == ["start"]
    assert isinstance(eng.stream, Stream) and eng.stream.started and eng.stream.kw["callback"] == lis.audio
    assert eng.states == ["listen"] and eng.target == "notepad.exe"
    assert lis.buffer is not None and lis.buffer.path.startswith(session.buffer_dir())
    lis.buffer.close()


@pytest.mark.parametrize("value", ["", "chat", None])
def test_an_unusable_listen_target_means_note(eng, mic, value):
    eng.cfg["listen_target"] = value
    eng.start_listening()
    assert eng.listening.target == "note"
    eng.listening.buffer.close()


def test_start_listening_refuses_in_the_cases_a_dictation_would(eng, mic, monkeypatch):
    monkeypatch.setattr(core, "key_missing", lambda cfg: True)
    eng.start_listening()
    assert eng.listening is None and eng.messages == ["Add your API key in Vox > Settings"] and eng.flashes == ["error"]
    monkeypatch.setattr(core, "key_missing", lambda cfg: False)
    for name in ("recording", "busy"):
        setattr(eng, name, True)
        eng.start_listening()
        setattr(eng, name, False)
    assert eng.listening is None and FakeListening.made == []


def test_a_microphone_error_is_reported_and_leaves_no_audio_file_behind(eng, monkeypatch):
    def broken(**kw):
        raise OSError("no microphone")

    monkeypatch.setattr(engine_mod.sd, "InputStream", broken)
    eng.start_listening()
    assert eng.listening is None and eng.messages == ["Microphone error: no microphone"] and eng.flashes == ["error"]
    assert session.recoverable(session.buffer_dir()) == []


def test_stopping_asks_the_session_to_end_and_the_session_drives_the_state(eng):
    lis = FakeListening(eng, {}, "note")
    eng.listening = lis
    eng.stop_listening()
    assert lis.calls == ["stop"] and eng.listening is lis      # still there until it has saved
    eng.listen_state("busy")
    assert eng.busy and eng.states == ["busy"]
    eng.listen_state("idle")
    assert eng.listening is None and not eng.busy and eng.states == ["busy", "idle"]


def test_close_mic_closes_the_stream(eng):
    eng.close_mic()
    assert eng.opened == ["closed"]


def test_the_tray_toggle_starts_and_stops(eng, mic):
    eng.toggle_listening()
    lis = eng.listening
    assert lis.calls == ["start"]
    eng.toggle_listening()
    assert lis.calls == ["start", "stop"]
    lis.buffer.close()


def test_the_tray_cannot_start_the_type_target_because_it_would_not_know_the_app(eng, mic):
    eng.cfg["listen_target"] = "type"
    eng.toggle_listening()
    assert eng.listening is None and FakeListening.made == [] and "double" in eng.messages[0].lower()
    eng.cfg["listen_target"] = "note"
    eng.toggle_listening()
    assert eng.listening.target == "note"
    eng.listening.buffer.close()


# ------------------------------------------------------------------ the note and the setting
def test_save_note_stores_it_syncs_and_says_so(eng):
    eng.save_note("Buy milk and eggs today.", "buy milk and eggs today", 12.0)
    assert [n["text"] for n in notes.search("milk")] == ["Buy milk and eggs today."]
    assert eng.sync.triggered == 1 and eng.messages == ["Note saved: Buy milk and eggs today."]


def test_the_listen_target_is_saved_and_the_other_settings_are_kept(eng):
    saved = core.load_config()
    saved["user_context"] = "changed by the window a moment ago"
    core.save_config(saved)
    eng.cfg = dict(saved)
    eng.set_listen_target("type")
    assert core.load_config()["listen_target"] == "type" and eng.cfg["listen_target"] == "type"
    assert core.load_config()["user_context"] == "changed by the window a moment ago"


def test_the_tray_menu_has_the_listen_entries_with_the_current_target_checked(eng, monkeypatch):
    monkeypatch.setattr(engine_mod.keyboard, "Controller", lambda: object())
    monkeypatch.setattr(engine_mod.core, "load_config", lambda: {})
    monkeypatch.setattr(engine_mod.Engine, "_mtime", lambda self: 0)
    monkeypatch.setattr(engine_mod.Engine, "_hotkey", lambda self: set())
    e = engine_mod.Engine()
    items = {i.text: i for i in e.icon.menu.items}
    assert items["Keep listening: Note"].checked is True and items["Keep listening: Type"].checked is False
    e.cfg["listen_target"] = "type"
    assert items["Keep listening: Note"].checked is False and items["Keep listening: Type"].checked is True
    assert "Start listening" in items and not items["Start listening"].checked


# ------------------------------------------------------------------ recovery
def leave_a_session(pcm=b"\x10\x27" * 3200):
    buf = session.SessionBuffer(session.buffer_dir(), "note")
    buf.append(pcm)
    buf.close()
    return buf.path


def test_a_left_over_session_can_be_recovered_into_a_note_and_its_file_goes_with_it(eng):
    assert not eng.can_recover()
    path = leave_a_session()
    assert eng.can_recover()
    eng.recover_listening()
    lis = FakeListening.made[0]
    assert lis.target == "note" and lis.buffer is None and lis.calls == ["start", ("replay", 6400)]
    assert eng.listening is lis and eng.busy and not eng.can_recover()
    lis.after()
    assert not session.recoverable(session.buffer_dir()) and not __import__("os").path.exists(path)


def test_nothing_to_recover_does_nothing(eng):
    eng.recover_listening()
    assert FakeListening.made == []


def test_quit_waits_for_a_running_session_to_save(eng, monkeypatch):
    order = []
    lis = FakeListening(eng, {}, "note")
    eng.listening = lis
    eng.meeting = type("M", (), {"active": False, "processing": False})()
    eng.sync = type("S", (), {"stop": lambda self: None})()
    eng.relay = type("R", (), {"stop": lambda self: None})()
    eng.icon = type("I", (), {"stop": lambda self: None})()
    lis.stop = lambda: (order.append("stop"), setattr(eng, "listening", None))
    monkeypatch.setattr(engine_mod.os, "_exit", lambda code: order.append("exit"))
    eng.quit()
    assert order == ["stop", "exit"]


# ------------------------------------------------------------------ the note hotkey (task E5)
KeyCode = engine_mod.keyboard.KeyCode


def n_key(char="\x0e"):
    """The N key as the Windows hook reports it: with Ctrl held its char is a control character, its vk stays 0x4E."""
    return KeyCode.from_vk(0x4E, char=char)


@pytest.fixture
def noted(eng, monkeypatch):
    """An engine with the default note hotkey whose start/stop calls are recorded."""
    eng.note_hotkey = session.parse_note_hotkey("ctrl+alt+n")[0]
    eng.note_key_down = False
    eng.calls = []
    monkeypatch.setattr(eng, "start_listening", lambda target=None: eng.calls.append(("start", target)))
    monkeypatch.setattr(eng, "stop_listening", lambda *_: eng.calls.append("stop"))
    return eng


def chord(e, *keys):
    for k in keys:
        e.on_press(k)


def let_go(e, *keys):
    for k in keys:
        e.on_release(k)


def test_the_note_hotkey_starts_a_note_and_the_same_hotkey_ends_it(noted):
    chord(noted, Key.ctrl_l, Key.alt_l, n_key())
    assert noted.calls == [("start", "note")]
    let_go(noted, n_key("n"), Key.alt_l, Key.ctrl_l)              # the char differs on release: the vk matches
    noted.listening = FakeListening(noted, {}, "note")
    chord(noted, Key.ctrl_l, Key.alt_l, n_key())
    assert noted.calls == [("start", "note"), "stop"]


def test_holding_the_note_hotkey_does_not_toggle_again(noted):
    chord(noted, Key.ctrl_l, Key.alt_l, n_key(), n_key(), n_key())   # key repeat
    assert noted.calls == [("start", "note")]
    let_go(noted, n_key("n"))
    chord(noted, n_key())                                          # a new press with the modifiers still down
    assert len(noted.calls) == 2


def test_the_main_key_without_its_modifiers_does_nothing_and_is_not_remembered(noted):
    chord(noted, n_key("n"))
    chord(noted, Key.ctrl_l, n_key())                              # Ctrl+N alone: not the combo
    assert noted.calls == [] and noted.pressed == {Key.ctrl_l}
    let_go(noted, n_key("n"), Key.ctrl_l)
    assert noted.pressed == set() and not noted.note_key_down


def test_the_modifiers_may_be_the_right_hand_ones(noted):
    chord(noted, Key.ctrl_r, Key.alt_r, n_key())
    assert noted.calls == [("start", "note")]


def test_an_f_key_is_found_by_its_virtual_key_code(noted):
    noted.note_hotkey = session.parse_note_hotkey("ctrl+f9")[0]
    chord(noted, Key.ctrl_l, Key.f9)
    assert noted.calls == [("start", "note")]


def test_while_a_session_is_finishing_the_note_hotkey_waits(noted):
    noted.listening, noted.busy = FakeListening(noted, {}, "note"), True
    chord(noted, Key.ctrl_l, Key.alt_l, n_key())
    assert noted.calls == []


def test_with_the_setting_empty_the_keys_are_ordinary_keys(noted):
    noted.note_hotkey = None
    chord(noted, Key.ctrl_l, Key.alt_l, n_key())
    assert noted.calls == []


def test_the_note_hotkey_is_not_the_dictation_shortcut(noted, monkeypatch):
    started = []
    monkeypatch.setattr(noted, "start", lambda: started.append(1))
    chord(noted, Key.ctrl_l, Key.alt_l, n_key())
    assert started == []                                           # Ctrl+Alt is no part of Ctrl+Win


def test_the_note_hotkey_starts_a_note_whatever_the_listen_target_is(eng, mic):
    eng.cfg["listen_target"] = "type"
    eng.start_listening("note")
    assert eng.listening.target == "note"
    eng.listening.buffer.close()


def test_the_hotkey_toggle_starts_a_note_then_stops_it(eng, mic):
    eng.cfg["listen_target"] = "type"
    eng.toggle_note_listening()
    lis = eng.listening
    assert lis.target == "note" and lis.calls == ["start"]
    eng.toggle_note_listening()
    assert lis.calls == ["start", "stop"]
    lis.buffer.close()


def test_the_engine_reads_the_note_hotkey_from_the_settings_and_drops_a_conflicting_one(eng):
    eng.cfg.update({"note_hotkey": "Ctrl+Alt+N", "hotkey": ["ctrl_l", "cmd"]})
    assert eng._note_hotkey().text == "ctrl+alt+n"
    eng.cfg["hotkey"] = ["ctrl", "alt"]                            # Ctrl+Alt would start a dictation first
    assert eng._note_hotkey() is None
    eng.cfg.update({"note_hotkey": "", "hotkey": ["ctrl_l", "cmd"]})
    assert eng._note_hotkey() is None


def test_the_parse_rule_knows_the_same_dictation_key_names_as_the_engine():
    assert session._DICTATION_KEYS == set(engine_mod.KEY_ALIASES)
    assert set(session._MODS) <= set(engine_mod.KEY_ALIASES)


def test_the_tray_has_a_note_entry_with_the_hotkey_only_while_it_is_set(eng, monkeypatch):
    monkeypatch.setattr(engine_mod.keyboard, "Controller", lambda: object())
    monkeypatch.setattr(engine_mod.core, "load_config", lambda: {"note_hotkey": "ctrl+alt+n"})
    monkeypatch.setattr(engine_mod.Engine, "_mtime", lambda self: 0)
    monkeypatch.setattr(engine_mod.Engine, "_hotkey", lambda self: set())
    e = engine_mod.Engine()
    item = next(i for i in e.icon.menu.items if "(Ctrl + Alt + N)" in i.text)
    assert item.text == "Start a note (Ctrl + Alt + N)" and item.visible
    e.listening = FakeListening(e, {}, "note")
    assert item.text == "Stop listening (Ctrl + Alt + N)"
    e.note_hotkey = None
    assert not item.visible


def test_a_saved_session_that_cannot_be_read_is_left_alone_and_no_session_is_started(eng, monkeypatch):
    path = leave_a_session()

    def unreadable(p):
        raise OSError("sharing violation")

    monkeypatch.setattr(session, "load_pcm", unreadable)
    eng.recover_listening()
    assert FakeListening.made == [] and eng.listening is None
    assert __import__("os").path.exists(path)
    assert eng.messages and "kept" in eng.messages[-1]
