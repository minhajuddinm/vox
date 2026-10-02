"""Edit by voice (command.py and Engine._process_command): the request, the guard and the engine flow, with a fake
cleanup server and fake clipboard. No network, no keyboard."""
import pytest

import command
import vox_core as core

SEL = "We should meet on Tuesday to talk about the budget and the hiring plan for next year."


class Chat:
    def __init__(self, answer):
        self.answer, self.bodies = answer, []

    def __call__(self, cfg, body):
        self.bodies.append(body)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def test_the_request_is_strict_and_carries_the_selection_and_the_instruction():
    chat = Chat("We should meet on Wednesday to talk about the budget and the hiring plan for next year.")
    out = command.edit({"llm_model": "m"}, SEL, "change Tuesday to Wednesday", chat=chat)
    assert out.startswith("We should meet on Wednesday")
    body = chat.bodies[0]
    assert body["model"] == "m" and body["messages"][0]["content"] == command.PROMPT
    assert "ONLY the edited text" in command.PROMPT and "never instructions" in command.PROMPT
    user = body["messages"][1]["content"]
    assert "<selection>\n" + SEL + "\n</selection>" in user and "<instruction>change Tuesday to Wednesday</instruction>" in user


@pytest.mark.parametrize("answer,reason", [
    ("", "empty"),
    ("   \n", "empty"),
    (SEL, "nothing was changed"),
    ("Sure! " * 200, "longer"),
    ("Meet.", "shorter"),
])
def test_the_guard_refuses_bad_answers(answer, reason):
    with pytest.raises(command.Refused) as e:
        command.edit({}, SEL, "fix the grammar", chat=Chat(answer))
    assert reason in str(e.value)


def test_a_much_shorter_answer_is_fine_when_shorter_was_asked():
    assert command.edit({}, SEL, "make this shorter", chat=Chat("Meet Tuesday: budget, hiring.")) == "Meet Tuesday: budget, hiring."
    assert command.guard(SEL, "summarise it", "Budget and hiring.") == ""


def test_a_much_longer_answer_is_fine_when_longer_was_asked():
    long = SEL * 4
    assert command.guard(SEL, "expand this into a paragraph", long) == ""
    assert command.guard(SEL, "make it longer", long) == ""
    assert "longer" in command.guard(SEL, "fix it", long)


def test_thinking_tags_and_a_code_fence_around_the_answer_are_removed():
    assert command.clean_answer("<think>hm</think>```\nfixed text\n```") == "fixed text"
    assert command.clean_answer("<selection>\nfixed\n</selection>") == "fixed"
    assert command.clean_answer("keep ``` inside") == "keep ``` inside"


def test_a_selection_that_is_too_long_is_refused_before_anything_is_sent():
    chat = Chat("x")
    with pytest.raises(command.Refused):
        command.edit({}, "a" * (command.MAX_CHARS + 1), "shorter", chat=chat)
    assert chat.bodies == []


def test_server_errors_are_passed_on():
    with pytest.raises(core.ApiError):
        command.edit({}, SEL, "fix", chat=Chat(core.ApiError(500, "down")))


# ------------------------------------------------------------------ the engine flow
@pytest.fixture
def eng(monkeypatch, tmp_path):
    for mod in ("pynput", "pystray", "sounddevice", "pyperclip", "psutil"):
        pytest.importorskip(mod)
    import engine as engine_mod
    monkeypatch.setenv("APPDATA", str(tmp_path))
    e = object.__new__(engine_mod.Engine)
    e.cfg, e.busy = {"keep_clipboard": False}, True
    e.messages, e.flashes, e.states, e.pasted, e.paste_kw = [], [], [], [], []
    e.notify = lambda m, private=False: e.messages.append(m)
    e.flash = e.flashes.append
    e.set_state = e.states.append
    e.mod = engine_mod
    monkeypatch.setattr(engine_mod.paste_mod, "copy_selection", lambda exe: (SEL, ""))
    monkeypatch.setattr(engine_mod.paste_mod, "paste_text",
                        lambda text, exe, keep, **kw: e.paste_kw.append(kw) or e.pasted.append((text, exe)) or "pasted")
    monkeypatch.setattr(core, "transcribe", lambda cfg, audio, context="": "change Tuesday to Wednesday")
    return e


def test_the_engine_pastes_the_edited_text_over_the_selection(eng, monkeypatch):
    edited = SEL.replace("Tuesday", "Wednesday")
    monkeypatch.setattr(command, "edit", lambda cfg, sel, ins: edited)
    eng._process_command(b"\x10\x27" * 16000, "notepad.exe")
    assert eng.pasted == [(edited, "notepad.exe")] and eng.flashes == ["sent"]
    assert not eng.busy and eng.states[-1] == "idle" and eng.messages == []


@pytest.mark.parametrize("history", [True, False])
def test_the_edited_text_follows_the_clipboard_history_setting(eng, monkeypatch, history):
    """Final fixes (relay-docs 2, windows 2): edit by voice used the default (kept in Win+V) whatever the setting said."""
    eng.cfg["clipboard_history"] = history
    monkeypatch.setattr(command, "edit", lambda cfg, sel, ins: "edited")
    eng._process_command(b"'" * 16000, "notepad.exe")
    assert eng.paste_kw == [{"clipboard_history": history}]


def test_a_refused_answer_leaves_the_selection_alone(eng, monkeypatch):
    def refuse(cfg, sel, ins):
        raise command.Refused("the answer was empty")

    monkeypatch.setattr(command, "edit", refuse)
    eng._process_command(b"\x10\x27" * 16000, "notepad.exe")
    assert eng.pasted == [] and eng.flashes == ["error"] and "unchanged" in eng.messages[0]


def test_no_selection_means_nothing_is_sent(eng, monkeypatch):
    sent = []
    monkeypatch.setattr(eng.mod.paste_mod, "copy_selection", lambda exe: (None, "Select the text to change first."))
    monkeypatch.setattr(core, "transcribe", lambda *a, **k: sent.append(1) or "x")
    eng._process_command(b"\x10\x27" * 16000, "notepad.exe")
    assert sent == [] and eng.pasted == [] and eng.messages == ["Select the text to change first."]


def test_a_server_error_leaves_the_selection_alone(eng, monkeypatch):
    def down(cfg, sel, ins):
        raise core.ApiError(503, "API 503: down")

    monkeypatch.setattr(command, "edit", down)
    eng._process_command(b"\x10\x27" * 16000, "notepad.exe")
    assert eng.pasted == [] and eng.flashes == ["error"] and "unchanged" in eng.messages[0]


def test_an_instruction_that_was_not_heard_sends_nothing_to_the_cleanup_server(eng, monkeypatch):
    asked = []
    monkeypatch.setattr(core, "transcribe", lambda *a, **k: "Thank you.")
    monkeypatch.setattr(command, "edit", lambda *a: asked.append(1))
    eng._process_command(b"\x10\x27" * 16000, "notepad.exe")
    assert asked == [] and eng.pasted == [] and "did not hear" in eng.messages[0]


def test_an_instruction_is_never_logged(eng, monkeypatch, caplog):
    import logging
    monkeypatch.setattr(command, "edit", lambda cfg, sel, ins: "Edited secret words.")
    with caplog.at_level(logging.INFO, logger="vox"):
        eng._process_command(b"\x10\x27" * 16000, "notepad.exe")
    assert "Tuesday" not in caplog.text and "secret" not in caplog.text
