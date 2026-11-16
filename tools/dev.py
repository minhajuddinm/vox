"""One entry point for working on Vox: set up, test, lint, check, run.

    python tools/dev.py setup            create .venv (Python 3.13) and install the app, test and lint packages
    python tools/dev.py test [ARGS]      pytest (ARGS go to pytest, e.g. -k paste or tests/test_paste.py)
    python tools/dev.py test --changed   only the test files that match what changed against main (fast loop)
    python tools/dev.py java             the Android Java tests (android/run-tests.sh; needs a JDK and ANDROID_JAR)
    python tools/dev.py lint [--wide] [--fix]   ruff: the CI rules, or a wider bug-finding set with --wide
    python tools/dev.py docs             the docs checklist for your change, then the docs checker
    python tools/dev.py check            what CI's tests job checks: lint, pytest, shared UI parts, docs
    python tools/dev.py run [ARGS]       start the Windows app from source (ARGS go to vox_app.py)
    python tools/dev.py selfcheck        import every module the app loads (catches a missing package)

Uses the repo's .venv when there is one, otherwise the Python running this script. VOX_PYTHON overrides both;
VOX_BASH names the bash for `java` (Git Bash; the `bash` on PATH may be WSL). Tests never touch the real profile:
tests/conftest.py gives each test its own empty APPDATA. Details: documentation/16-developer-guide.md.
"""
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV = os.path.join(ROOT, ".venv")
WIDE_RULES = "F,E9,B,PLE,RUF100"   # B: likely bugs (late-bound loop variables, mutable defaults); PLE: pylint errors


def py():
    if os.environ.get("VOX_PYTHON"):
        return os.environ["VOX_PYTHON"]
    exe = os.path.join(VENV, "Scripts", "python.exe") if os.name == "nt" else os.path.join(VENV, "bin", "python")
    return exe if os.path.exists(exe) else sys.executable


def run(*cmd, **kw):
    print("+", " ".join(cmd), flush=True)
    return subprocess.call(list(cmd), cwd=kw.pop("cwd", ROOT), **kw)


def changed_files():
    out = subprocess.run(["git", "diff", "--name-only", "main...HEAD"], cwd=ROOT, capture_output=True, text=True).stdout
    out += subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout
    names = set()
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        names.add(line[3:] if re.match(r"^[ MADRCU?!]{2} ", line) else line)
    return sorted(n.split(" -> ")[-1] for n in names)


def tests_for(files):
    """Test files whose name contains the stem of a changed module (vox_core.py -> test_vox_core*.py), plus any
    changed test file. A change to conftest.py, spec/ or ui-shared/ runs everything (returns None)."""
    tests = set(f for f in os.listdir(os.path.join(ROOT, "tests")) if f.startswith("test_") and f.endswith(".py"))
    picked = set()
    for f in files:
        f = f.replace("\\", "/")
        if f.endswith("conftest.py") or f.startswith(("spec/", "ui-shared/")):
            return None
        stem = os.path.splitext(os.path.basename(f))[0]
        if f.startswith("tests/") and stem.startswith("test_"):
            picked.add(os.path.basename(f))
        elif f.endswith(".py"):
            picked |= {t for t in tests if stem.lower() in t[5:].lower()}
    return sorted(os.path.join("tests", t) for t in picked)


def find_bash():
    for b in (os.environ.get("VOX_BASH"), r"C:\Program Files\Git\bin\bash.exe", shutil.which("bash")):
        if b and os.path.exists(b):
            return b
    return None


def cmd_setup(_):
    if not os.path.exists(VENV):
        if run(sys.executable, "-m", "venv", VENV):
            return 1
    p = py()
    return (run(p, "-m", "pip", "install", "-q", "-r", "windows/requirements.lock")
            or run(p, "-m", "pip", "install", "-q", "-r", "tests/requirements.txt"))


def cmd_test(args):
    if "--changed" in args:
        args = [a for a in args if a != "--changed"]
        picked = tests_for(changed_files())
        if picked == []:
            print("No test file matches the changed files; run `dev.py test` for the whole suite.")
            return 0
        args = (picked or []) + args
    return run(py(), "-m", "pytest", "-q", *args)


def cmd_java(args):
    bash = find_bash()
    if not bash:
        print("No bash found. Install Git for Windows or set VOX_BASH.", file=sys.stderr)
        return 2
    if not os.environ.get("ANDROID_JAR"):
        print("Set ANDROID_JAR to platforms/android-34/android.jar and put a JDK 17 on PATH "
              "(documentation/10-build-test-release.md).", file=sys.stderr)
        return 2
    return run(bash, "android/run-tests.sh", *args)


def cmd_lint(args):
    extra = ["--fix"] if "--fix" in args else []
    sel = ["--select", WIDE_RULES] if "--wide" in args else []
    return run(py(), "-m", "ruff", "check", *sel, *extra, ".")


def cmd_docs(_):
    return run(py(), "documentation/tools/docs_todo.py") or run(py(), "documentation/tools/check_docs.py")


def cmd_check(_):
    steps = [("lint", [py(), "-m", "ruff", "check", "."]),
             ("pytest", [py(), "-m", "pytest", "-q"]),
             ("shared UI parts", [py(), "tools/sync_ui.py", "--check"]),
             ("docs", [py(), "documentation/tools/check_docs.py"])]
    failed = [name for name, c in steps if run(*c)]
    print("\ncheck:", "FAILED: " + ", ".join(failed) if failed else "all OK")
    return 1 if failed else 0


def cmd_run(args):
    return run(py(), "windows/vox_app.py", *args)


def cmd_selfcheck(_):
    return run(py(), "windows/vox_app.py", "--selfcheck")


COMMANDS = {"setup": cmd_setup, "test": cmd_test, "java": cmd_java, "lint": cmd_lint, "docs": cmd_docs,
            "check": cmd_check, "run": cmd_run, "selfcheck": cmd_selfcheck}


def main(argv):
    if not argv or argv[0] not in COMMANDS:
        print(__doc__)
        return 0 if argv[:1] in ([], ["-h"], ["--help"]) else 2
    return COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
