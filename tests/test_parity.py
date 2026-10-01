"""Checks vox_core against spec/golden.txt. The Android app runs the same file (ParityTest.java), so the two
implementations of the cleanup helpers cannot drift apart without a test failing."""
import os

import pytest

import notes
import providers
import sync
import vox_core as core

GOLDEN = os.path.join(os.path.dirname(__file__), "..", "spec", "golden.txt")


def unesc(s):
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            out.append({"n": "\n", "t": "\t", "\\": "\\"}.get(s[i + 1], s[i + 1]))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def cases():
    with open(GOLDEN, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.rstrip("\n")
            if line and not line.startswith("#"):
                kind, *fields = [unesc(x) for x in line.split("\t")]
                yield pytest.param(kind, fields, id=f"{n}-{kind}")


def items(field, sep="|"):
    return [x for x in field.split(sep) if x]


def remote_wins(has_local, local_updated, remote_updated, remote_deleted):
    """True when notes.apply_remote lets a note from the relay replace the local copy (run against a temporary notes.db)."""
    remote = {"id": "n1", "created_at": 1.0, "updated_at": remote_updated, "title": "remote", "text": "remote",
              "deleted": remote_deleted}
    if has_local:
        notes.apply_remote(dict(remote, updated_at=local_updated, title="local", text="local", deleted=False))
    return notes.apply_remote(remote)


def merged_value(base, local, remote):
    """sync.merge3 on one profile field whose value on each side is a string, or "~" when the field is absent there;
    the result in the same notation."""
    def side(v):
        return {} if v == "~" else {"k": v}
    return sync.merge3(side(base), side(local), side(remote)).get("k", "~")


@pytest.mark.parametrize("kind,f", cases())
def test_golden(kind, f, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))   # the notes rows use a real (temporary) notes.db
    if kind == "sanitize":
        assert core.sanitize(f[0]) == f[1]
    elif kind == "looks_valid":
        assert core.looks_valid(f[0], f[1]) == (f[2] == "true")
    elif kind == "replace":
        repl = dict(p.split("=>", 1) for p in items(f[1], ";"))
        assert core.apply_replacements(f[0], repl) == f[2]
    elif kind == "whisper":
        assert core.whisper_prompt(items(f[0])) == f[1]
    elif kind == "terms":
        cfg = {"people": items(f[0]), "dictionary": f[1].split("|") if f[1] else []}
        assert "|".join(core.dictionary_terms(cfg)) == f[2]
    elif kind == "prompt":
        assert core.system_prompt(f[0], items(f[1]), f[2]) == f[3]
    elif kind == "spoken":
        assert core.apply_spoken_commands(f[0]) == f[1]
    elif kind == "promptctx":
        assert core.system_prompt(f[0], [t for t in f[1].split("|") if t], f[2], f[3]) == f[4]
    elif kind == "promptstrength":
        assert core.system_prompt(f[1], [t for t in f[2].split("|") if t], f[3], f[4], f[0]) == f[5]
    elif kind == "context":
        assert core.clean_context(f[0]) == f[1]
    elif kind == "level":
        assert "%.3f" % core.level_from_rms(float(f[0])) == f[1]
    elif kind == "models":
        assert providers.classify(f[0]) == f[1]
    elif kind == "silence":
        assert core.is_silence_hallucination(f[0]) == (f[1] == "true")
    elif kind == "gate":
        assert core.needs_cleanup(f[0], f[1], f[2] == "true", f[3]) == (f[4] == "true")
    elif kind == "title":
        assert notes.auto_title(f[0]) == f[1]
    elif kind == "ftsq":
        assert notes.fts_query(f[0]) == f[1]
    elif kind == "remotewins":
        assert remote_wins(f[0] == "true", float(f[1]), float(f[2]), f[3] == "true") == (f[4] == "true")
    elif kind == "merge3":
        assert merged_value(f[0], f[1], f[2]) == f[3]
    elif kind == "profilefields":
        assert "|".join(sync.PROFILE_KEY_FIELDS if f[0] == "keys" else sync.PROFILE_FIELDS) == f[1]
    elif kind == "devname":
        assert sync.device_name({"device_name": f[0]}) == f[1]
    elif kind == "permanent":
        assert sync.SyncError("x", int(f[0])).permanent == (f[1] == "true")
    elif kind == "proxyurl":   # relay_url, role, address a role is sent to through the relay (blank when no relay address)
        assert providers.proxy_url(f[0], f[1]) == f[2]
    elif kind == "retry":   # status (0 = no answer), request timeout, via the relay, whether the same request is sent again
        assert core.retryable(int(f[0]), f[1] == "true", f[2] == "true") == (f[3] == "true")
    elif kind == "fidelity":   # strength, raw, cleaned, whether the cleanup kept enough of the spoken words
        assert core.fidelity_ok(f[1], f[2], f[0]) == (f[3] == "true")
    elif kind == "tokens":   # text, its word tokens joined by |
        assert "|".join(core.word_tokens(f[0])) == f[1]
    elif kind == "recall":   # raw, cleaned, share of raw's words still in cleaned (3 decimals)
        assert "%.3f" % core.word_recall(f[0], f[1]) == f[2]
    elif kind == "cleanstrength":   # the stored setting, the strength it means
        assert core.clean_strength(f[0]) == f[1]
    elif kind == "fallback":   # raw words, the text used when the fidelity guard rejects the cleanup
        assert core.fallback_text(f[0]) == f[1]
    else:
        pytest.fail(f"unknown case kind {kind}")
