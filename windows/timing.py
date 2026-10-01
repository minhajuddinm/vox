"""Where the time goes in one dictation (pure, standard library only).

A `Timing` records named marks (monotonic milliseconds) while a dictation runs; `stages()` turns them into the six
stage durations the Speed card shows; `summarize()` takes the last N entries and gives the median and the 90th
percentile of each stage and the biggest one. Nothing here touches the network or the disk: timings stay on the
device. The Java twin is android/src/com/minhaj/vox/Timing.java; the shared numbers are pinned by the timing_*
rows of spec/golden.txt (run by tests/test_parity.py and ParityTest).

Stages (all milliseconds, never negative; a stage whose marks are missing is 0):
  start   key_down -> rec_start     the wait between the key (or tap) and the microphone really recording
  rec     rec_start -> key_up       how long the person spoke (not something to speed up)
  stt     stt_start -> stt_done     speech to text, including upload
  llm     llm_start -> llm_done     cleanup; 0 when cleanup was skipped
  insert  llm_done (or stt_done when cleanup was skipped) -> inserted
  total   key_up -> inserted        what the person waits for after letting go
"""
import time

MARKS = ("key_down", "rec_start", "key_up", "stt_start", "stt_done", "llm_start", "llm_done", "inserted")
STAGES = ("start", "rec", "stt", "llm", "insert", "total")
# "biggest" picks among the stages the app can work on. rec is the person speaking and total is the sum of the rest.
BIGGEST_CANDIDATES = ("start", "stt", "llm", "insert")


def _span(a, b):
    """b - a in whole milliseconds, 0 when either is missing or the clock ran backwards."""
    if a is None or b is None:
        return 0
    return max(0, int(b) - int(a))


class Timing:
    def __init__(self, clock=None):
        """`clock` returns seconds (default time.monotonic); marks are stored in whole milliseconds."""
        self._clock = clock or time.monotonic
        self._marks = {}

    def mark(self, name, at_ms=None):
        """Record a mark now (or at `at_ms`). A later mark with the same name replaces the earlier one."""
        if name not in MARKS:
            raise ValueError("unknown timing mark: %r" % (name,))
        self._marks[name] = int(at_ms) if at_ms is not None else int(self._clock() * 1000)

    def get(self, name):
        return self._marks.get(name)

    def has(self, name):
        return name in self._marks

    def stages(self):
        m = self._marks
        llm_ran = "llm_start" in m and "llm_done" in m
        insert_from = m.get("llm_done") if llm_ran else m.get("stt_done")
        return {
            "start": _span(m.get("key_down"), m.get("rec_start")),
            "rec": _span(m.get("rec_start"), m.get("key_up")),
            "stt": _span(m.get("stt_start"), m.get("stt_done")),
            "llm": _span(m.get("llm_start"), m.get("llm_done")) if llm_ran else 0,
            "insert": _span(insert_from, m.get("inserted")),
            "total": _span(m.get("key_up"), m.get("inserted")),
        }

    def entry(self, stt_model="", llm_model="", provider="", relay=False):
        """What goes into the history and into `summarize`."""
        return {"stages": self.stages(), "stt_model": stt_model, "llm_model": llm_model, "provider": provider,
                "relay": bool(relay)}


def median(values):
    """Middle value of a list of non-negative whole numbers; the two middle ones average (rounded down). 0 when empty."""
    xs = sorted(int(v) for v in values)
    n = len(xs)
    if n == 0:
        return 0
    if n % 2:
        return xs[n // 2]
    return (xs[n // 2 - 1] + xs[n // 2]) // 2


def p90(values):
    """90th percentile by nearest rank: the ceil(0.9 * n)-th smallest value. 0 when empty."""
    xs = sorted(int(v) for v in values)
    n = len(xs)
    if n == 0:
        return 0
    return xs[(9 * n + 9) // 10 - 1]


def biggest(stages):
    """Name of the largest of start/stt/llm/insert ("" when all are 0 or missing); a tie goes to the earlier stage."""
    best, best_ms = "", 0
    for name in BIGGEST_CANDIDATES:
        v = stages.get(name, 0)
        if v > best_ms:
            best, best_ms = name, v
    return best


def format_ms(ms):
    """850 -> "850 ms", 1449 -> "1.4 s" (one decimal, halves round up)."""
    ms = max(0, int(ms))
    if ms < 1000:
        return "%d ms" % ms
    tenths = (ms + 50) // 100
    return "%d.%d s" % (tenths // 10, tenths % 10)


def _stages_of(entry):
    st = entry.get("stages") if isinstance(entry, dict) else None
    return st if isinstance(st, dict) else None


def summarize(entries, n=50):
    """Median and p90 of every stage over the newest `n` entries (the list is oldest first, as the history keeps it).

    Returns {stage: {"median": ms, "p90": ms}, ..., "biggest": stage, "count": entries used}. A stage that is 0 in an
    entry did not run (cleanup skipped) or was not measured, so it is left out of that stage's numbers instead of
    dragging the median down. Entries without a "stages" dict are ignored."""
    used = []
    for e in list(entries)[-n:] if n > 0 else []:
        st = _stages_of(e)
        if st is not None:
            used.append(st)
    out = {}
    for name in STAGES:
        vals = [int(st.get(name, 0)) for st in used if int(st.get(name, 0)) > 0]
        out[name] = {"median": median(vals), "p90": p90(vals)}
    out["biggest"] = biggest({name: out[name]["median"] for name in STAGES})
    out["count"] = len(used)
    return out
