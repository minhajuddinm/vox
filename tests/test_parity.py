"""Checks vox_core against spec/golden.txt. The Android app runs the same file (ParityTest.java), so the two
implementations of the cleanup helpers cannot drift apart without a test failing."""
import os

import pytest

import notes
import providers
import sync
import timing
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


def numbers(field):
    """A | separated list of whole numbers."""
    return [int(x) for x in field.split("|") if x]


def kv(field):
    """A comma separated k=v map of whole numbers (timing marks or stages)."""
    return dict((p.split("=", 1)[0], int(p.split("=", 1)[1])) for p in field.split(",") if p)


def stages_text(st):
    return ",".join("%s=%d" % (k, st[k]) for k in timing.STAGES)


def summary_text(s):
    return "count=%d biggest=%s " % (s["count"], s["biggest"]) + " ".join(
        "%s=%d/%d" % (k, s[k]["median"], s[k]["p90"]) for k in timing.STAGES)


def timing_stages(marks):
    t = timing.Timing()
    for name, at in kv(marks).items():
        t.mark(name, at)
    return t.stages()


def timing_summary(entries, n):
    return timing.summarize([{"stages": kv(e)} for e in entries.split(";") if e], int(n))


def models_text(rows):
    return ";".join("%s+%s n=%d stt=%d llm=%d total=%d" % (r["stt_model"], r["llm_model"], r["count"], r["stt"], r["llm"], r["total"])
                    for r in rows)


def timing_models(entries, n):
    out = []
    for e in entries.split(";") if entries else []:
        stt_model, llm_model, stages = e.split("@", 2)
        out.append({"stages": kv(stages), "stt_model": stt_model, "llm_model": llm_model})
    return timing.by_model(out, int(n))


def history_rows(rows):
    """Golden history rows, ; separated. A row is t@app@words@voice@cleanup@relay@stages (relay 1 or 0, stages a k=v map);
    an empty t, app or words leaves that key out of the row. Three fields is a row without a timing, and a fourth field
    ~ is a row whose timing is not a map."""
    out = []
    for r in rows.split(";") if rows else []:
        p = r.split("@", 6)
        h = {}
        if p[0]:
            h["t"] = int(p[0])
        if p[1]:
            h["app"] = p[1]
        if p[2]:
            h["words"] = int(p[2])
        if len(p) == 4:
            h["timing"] = "x"
        elif len(p) == 7:
            h["timing"] = {"stages": kv(p[6]), "stt_model": p[3], "llm_model": p[4], "provider": "p", "relay": p[5] == "1"}
        out.append(h)
    return out


def view_text(v):
    """The whole Speed card as one line: the summary, the by-model lines and the last dictations (newest first)."""
    s = dict(v["stages"], count=v["count"], biggest=v["biggest"])
    last = "|".join("%d@%s@%d@%s@%s@%d@%s" % (r["t"], r["app"], r["words"], r["stt_model"], r["llm_model"], 1 if r["relay"] else 0,
                                              ",".join("%s=%d" % (k, r["stages"].get(k, 0)) for k in timing.STAGES))
                    for r in v["last"])
    return "%s models=%s last=%s" % (summary_text(s), models_text(v["models"]), last)


SEG_LEVEL = {"t": 8000, "s": 0, "q": 899, "n": 900}


def seg_audio(runs):
    """Golden audio: | separated runs, each a letter (see segcuts in spec/golden.txt) and a length in milliseconds."""
    out = bytearray()
    for r in runs.split("|"):
        out += SEG_LEVEL[r[0]].to_bytes(2, "little", signed=True) * (int(r[1:]) * 16)
    return bytes(out)


def segcuts(params, runs, block):
    mn, mx, pz = [int(x) for x in params.split("|")]
    seg = core.Segmenter(mn / 1000.0, mx / 1000.0, pz / 1000.0)
    pcm = seg_audio(runs)
    pieces = []
    for i in range(0, len(pcm), int(block)):
        pieces += seg.feed(pcm[i:i + int(block)])
    rest = seg.rest()
    assert b"".join(pieces) + rest == pcm   # nothing is lost or repeated
    return "|".join(str(len(p)) for p in pieces) + "/" + str(len(rest))


def relay_devices(field):
    """The `last_seen;name` items of a devices row as the relay's JSON would give them: "~" leaves last_seen out, a
    number is a number, any other text stays text (a relay that sends something odd)."""
    out = []
    for item in items(field):
        seen, name = item.split(";", 1)
        d = {"name": name}
        if seen != "~":
            try:
                d["last_seen"] = float(seen)
            except ValueError:
                d["last_seen"] = seen
        out.append(d)
    return out


def health_answer(field):
    """The `k=v;k=v` fields of a relaycheck row as the relay's JSON would give them: s:text is a string, n:number a number,
    b:true / b:false a boolean; an empty field is no usable answer (None)."""
    if not field:
        return None
    out = {}
    for item in field.split(";"):
        key, value = item.split("=", 1)
        kind, text = value.split(":", 1)
        out[key] = text if kind == "s" else (float(text) if "." in text else int(text)) if kind == "n" else text == "true"
    return out


def relaycheck_text(r):
    return f"{'true' if r['ok'] else 'false'};{'true' if r['reachable'] else 'false'};{'true' if r['token_ok'] else 'false'};{r['relay_version']};{r['notes']}"


def devices_text(rows):
    return "|".join(f"{r['state']};{'true' if r['this'] else 'false'};{r['ago']};{r['name']}" for r in rows)
# The floating bubble exists only on Android (BubbleLogic.java). The reference below is the rule written down once more
# in Python, so the golden rows are an executable spec that a second implementation also meets; the Java side runs the
# very same rows in ParityTest.
def bubble_clamp(x, y, screen_w, screen_h, bubble_w, bubble_h):
    return (min(max(x, 0), max(0, screen_w - bubble_w)), min(max(y, 0), max(0, screen_h - bubble_h)))


def bubble_show(only_typing, always_show, field_focused, screen_on, service_ready):
    if not service_ready or not screen_on:
        return False
    return always_show or not only_typing or field_focused


def bubble_action(wanted, shown, attached):
    if wanted:
        return "add" if not shown else ("none" if attached else "repair")
    return "remove" if shown else "none"


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
    elif kind == "whisperctx":   # terms, context => the speech-to-text prompt of a piece of a long recording
        assert core.whisper_prompt_with_context(items(f[0]), f[1]) == f[2]
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
    elif kind == "timing_median":
        assert str(timing.median(numbers(f[0]))) == f[1]
    elif kind == "timing_p90":
        assert str(timing.p90(numbers(f[0]))) == f[1]
    elif kind == "timing_biggest":
        assert timing.biggest(kv(f[0])) == f[1]
    elif kind == "timing_format":
        assert timing.format_ms(int(f[0])) == f[1]
    elif kind == "timing_stages":   # marks (ms) => stages
        assert stages_text(timing_stages(f[0])) == f[1]
    elif kind == "timing_summary":   # entries (stages maps separated by ;), n => count, biggest and median/p90 per stage
        assert summary_text(timing_summary(f[0], f[1])) == f[2]
    elif kind == "timing_models":   # entries (voice@cleanup@stages, separated by ;), n => one line per model pair
        assert models_text(timing_models(f[0], f[1])) == f[2]
    elif kind == "timing_view":   # history rows (see history_rows), n, last => the whole Speed card as one line
        assert view_text(timing.speed_view(history_rows(f[0]), int(f[1]), int(f[2]))) == f[3]
    elif kind == "segcuts":   # min_ms|max_ms|pause_ms, runs, block => piece lengths / rest length
        assert segcuts(f[0], f[1], f[2]) == f[3]
    elif kind == "devices":   # now, this device's name, the relay's devices, the rows the card shows
        assert devices_text(sync.devices_view(relay_devices(f[2]), float(f[0]), f[1])) == f[3]
    elif kind == "relaycheck":   # HTTP status of /health (0 = no answer), the answer's fields, ok;reachable;token_ok;relay_version;notes
        assert relaycheck_text(sync.relay_check(int(f[0]), health_answer(f[1]), "dev", "failure")) == f[2]
    elif kind == "bubbleclamp":   # x, y, screen w, screen h, bubble w, bubble h, expected "x,y"
        x, y = bubble_clamp(*[int(v) for v in f[:6]])
        assert f"{x},{y}" == f[6]
    elif kind == "bubbleshow":   # only typing, always show, field focused, screen on, service ready, expected
        assert bubble_show(*[v == "true" for v in f[:5]]) == (f[5] == "true")
    elif kind == "bubbleaction":   # wanted, shown, window still attached, expected none|add|remove|repair
        assert bubble_action(*[v == "true" for v in f[:3]]) == f[3]
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
    elif kind == "fuzzydict":   # terms, text, the text with the dictionary's spellings applied
        assert core.fuzzy_dictionary(f[1], items(f[0])) == f[2]
    else:
        pytest.fail(f"unknown case kind {kind}")
