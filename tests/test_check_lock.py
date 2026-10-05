"""bf-e SEC-5/CI5: tools/check_lock.py, which fails a release build when anything beside the locked packages is installed."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import check_lock  # noqa: E402

LOCK = """
# a comment
Foo_Bar==1.2.0   # trailing comment
pefile==2024.8.26 ; sys_platform == "win32"
macholib==1.16.3 ; sys_platform != "win32"
hashed==2.0 \\
    --hash=sha256:00ff
"""


def test_parse_reads_pins_markers_and_hash_lines():
    assert check_lock.parse(LOCK, "win32") == {"foo-bar": "1.2.0", "pefile": "2024.8.26", "hashed": "2.0"}
    assert check_lock.parse(LOCK, "linux") == {"foo-bar": "1.2.0", "macholib": "1.16.3", "hashed": "2.0"}


@pytest.mark.parametrize("bad", ["requests>=2", "requests", 'x==1 ; python_version < "3.10"'])
def test_parse_refuses_what_it_does_not_understand(bad):
    with pytest.raises(ValueError):
        check_lock.parse(bad, "win32")


def test_problems_name_missing_wrong_and_extra_packages():
    locked = {"a": "1", "b": "2", "c": "3"}
    have = {"a": "1", "b": "2.1", "d": "4", "pip": "26"}
    assert check_lock.problems(locked, have) == ["wrong version: b 2.1 (locked 2)", "missing: c==3", "not in any lock: d==4"]
    assert check_lock.problems({"a": "1"}, {"a": "1", "pip": "9"}) == []


def test_main_checks_this_python(tmp_path, capsys):
    have = check_lock.installed()
    exact = tmp_path / "exact.lock"
    exact.write_text("\n".join(f"{k}=={v}" for k, v in have.items() if k != "pip"), encoding="utf-8")
    assert check_lock.main([str(exact)]) == 0 and "lock ok" in capsys.readouterr().out
    short = tmp_path / "short.lock"
    short.write_text("", encoding="utf-8")
    assert check_lock.main([str(short)]) == (1 if set(have) - {"pip"} else 0)


@pytest.mark.parametrize("path", ["windows/requirements.lock", "tools/build-requirements.lock"])
def test_the_committed_locks_parse_on_every_build_platform(path):
    with open(os.path.join(os.path.dirname(__file__), "..", *path.split("/")), encoding="utf-8") as f:
        text = f.read()
    for platform in ("win32", "linux"):
        assert check_lock.parse(text, platform)
