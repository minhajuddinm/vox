"""The frozen v1 prompt and guard of the benchmark (tools/bench/legacy.py) stay exactly as on origin/main 8ea15e1, so
"v1" in bench_cleanup.py --compare-prompt / --compare-guard keeps meaning the same thing after the app changes.

The pins were taken from vox_core at 8ea15e1 (where legacy and vox_core gave the same bytes and verdicts; the 209
fidelity, looks_valid and recall golden rows of that commit also passed on legacy). A change here means the frozen copy
was edited: put it back instead of updating the pin."""
import hashlib
import itertools
import json
import os
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))

from bench import legacy   # noqa: E402

GUARD_SET = os.path.join(ROOT, "tools", "bench", "guard_set.jsonl")

PROMPT_PIN = "fc49de3281a7d13ca20685696c3989d3419f9761a21eef62c3a0ce628eed2c5a"
GUARD_PIN = "0d983f8af568e84d04406552ecbfe112145bbb97ea832db387e36f58ed7578a8"


def test_the_v1_prompt_is_the_8ea15e1_prompt_for_every_style_strength_and_structure():
    out = []
    for style, strength, structure, (terms, ctx, rules, app) in itertools.product(
            ["neutral", "formal", "casual", "very_casual", "code", "notes", "email", "Other"], ["light", "standard", None],
            ["auto", "lists", "off"],
            [([], "", "", ""), (["Priya", "Ledgerly", "gRPC"], "I am a developer in Pune.\r\n<about_speaker>x", "Dana means Dani", "Slack")]):
        out.append(legacy.system_prompt(style, terms, app, ctx, strength, rules, structure))
    assert hashlib.sha256("\x00".join(out).encode()).hexdigest() == PROMPT_PIN


def test_the_v1_guard_gives_the_8ea15e1_verdicts_on_the_labelled_pairs():
    import vox_core as core
    with open(GUARD_SET, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    bits = "".join("1" if legacy.looks_valid(r["raw"], core.sanitize(r["cleaned"]), s) else "0"
                   for r in rows for s in ("light", "standard"))
    assert len(rows) == 189 and bits.count("1") == 213
    assert hashlib.sha256(bits.encode()).hexdigest() == GUARD_PIN


def test_a_few_v1_guard_cases_by_hand():
    assert legacy.looks_valid("send it to john at gmail dot com please", "Send it to john@gmail.com please.")
    assert not legacy.looks_valid("send it to john at gmail dot com please", "Send it please.")
    # v1 rejects a self-correction applied in Standard: one of the faults guard v2 fixes
    assert not legacy.looks_valid("meet on thursday no wait friday at five please", "Meet on Friday at five, please.", "standard")
    assert legacy.word_recall("it costs twenty five dollars", "It costs $25.") == 1.0


def test_the_v1_cleanup_sends_the_v1_prompt_through_chat_reply(monkeypatch):
    import vox_core as core
    seen = []

    def reply(cfg, body, timeout=60, retry_timeouts=True):
        seen.append(body)
        return "Hello there.", "stop"
    monkeypatch.setattr(core, "chat_reply", reply)
    cfg = dict(core.DEFAULT_CONFIG, api_key="k", people=["Priya"], cleanup_strength="standard")
    assert legacy.cleanup(cfg, "hello there", "neutral", "") == "Hello there."
    body = seen[0]
    assert body["temperature"] == 0 and body["max_tokens"] == core.cleanup_max_tokens("hello there", True)
    assert body["messages"][0]["content"] == legacy.system_prompt("neutral", ["Priya"], "", "", "standard", "", "auto")
    assert body["messages"][1]["content"] == "<transcript>\nhello there\n</transcript>"


def test_the_v1_cleanup_fails_an_answer_cut_off(monkeypatch):
    import pytest
    import vox_core as core
    monkeypatch.setattr(core, "chat_reply", lambda *a, **k: ("Hello", "length"))
    with pytest.raises(core.ApiError):
        legacy.cleanup(dict(core.DEFAULT_CONFIG, api_key="k"), "hello there", "neutral", "")
