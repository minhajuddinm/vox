"""The one-shot benchmark wizard (tools/bench_wizard.py) with fake tools and typed answers: no microphone, no network,
nothing outside the test profile."""
import os
import sys
from datetime import datetime

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))

import bench_clips as clips    # noqa: E402
import bench_wizard as wiz     # noqa: E402


class Tool:
    def __init__(self, name, calls, code=0, effect=None):
        self.name, self.calls, self.code, self.effect = name, calls, code, effect

    def main(self, argv):
        self.calls.append((self.name, list(argv)))
        print(f"{self.name} printed this")
        if self.effect:
            self.effect()
        return self.code


def answers(*values):
    it = iter(values)
    return lambda prompt: next(it)


def add_clip(folder, n):
    for i in range(n):
        clips.append_row(folder, {"id": f"clip-{i:03d}", "ref_verbatim": "hello there", "ref_intended": "Hello there."})


def fixed_now():
    return datetime(2026, 10, 5, 12, 0, 0)


def run(ask, record_effect=None, codes=(0, 0, 0)):
    calls, said = [], []
    tools = (Tool("record", calls, codes[0], record_effect), Tool("stt", calls, codes[1]), Tool("cleanup", calls, codes[2]))
    code = wiz.main(ask=ask, say=said.append, tool_set=tools, now=fixed_now)
    return code, calls, said


def run_dir():
    return os.path.join(wiz.core.data_dir(), "bench", "run-20261005-120000")


def test_full_run_chains_the_tools_with_the_tuning_round_arguments():
    folder = clips.clips_dir()
    code, calls, said = run(answers("", "", "", "", "n", "", ""), record_effect=lambda: add_clip(folder, 3))
    assert code == 0
    assert [c[0] for c in calls] == ["record", "stt", "cleanup", "cleanup"]
    assert calls[0][1] == ["--target", "50"]
    assert calls[1][1][:2] == ["--provider", "groq"] and calls[1][1][-1].endswith("stt.json")
    for (_, argv), strength in zip(calls[2:], ("light", "standard")):
        assert argv[:3] == ["--provider", "groq", "--clips"]
        assert argv[3:9] == ["--compare-prompt", "v1,v3", "--compare-guard", "v1,v2", "--strength", strength]
        assert argv[-1].endswith(f"cleanup-{strength}.json")
    summary = open(os.path.join(run_dir(), "summary.txt"), encoding="utf-8").read()
    assert "record printed this" in summary and summary.count("cleanup printed this") == 2
    assert any("bench done" in s for s in said)


def test_recorded_clips_skip_recording_by_default_and_the_sweep_is_optional():
    add_clip(clips.clips_dir(), 50)
    code, calls, _ = run(answers("", "openai", "", "y", "n", "y"))
    assert code == 0
    assert [c[0] for c in calls] == ["stt", "stt", "cleanup"]
    assert calls[0][1][:2] == ["--provider", "openai"]
    assert "--trim-pad-ms" in calls[1][1] and "--prompt-tokens" in calls[1][1]
    assert calls[2][1][calls[2][1].index("--strength") + 1] == "standard"


def test_no_clips_means_nothing_to_measure():
    code, calls, said = run(answers("n"))
    assert code == 1 and calls == []
    assert any("nothing to measure" in s for s in said)


def test_a_failed_step_is_reported_and_the_run_continues():
    add_clip(clips.clips_dir(), 5)
    code, calls, said = run(answers("n", "", "", "n", "", ""), codes=(0, 2, 0))
    assert code == 1
    assert [c[0] for c in calls] == ["stt", "cleanup", "cleanup"]
    assert any("stopped (code 2)" in s for s in said)


def test_answers_are_asked_again_until_they_make_sense():
    assert wiz.yes(answers("maybe", "N"), "Go?") is False
    assert wiz.number(answers("zero", "-3", "12"), "How many?", 50) == 12
    assert wiz.number(answers(""), "How many?", 50) == 50
