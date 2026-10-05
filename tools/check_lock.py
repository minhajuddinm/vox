"""Checks that this Python holds exactly the packages of the given lock files, at their versions (release builds).

    python tools/check_lock.py windows/requirements.lock tools/build-requirements.lock

A lock line is `name==version`, optionally with `; sys_platform == "win32"` (or `!=`), and `#` starts a comment.
Fails (exit 1, one line per problem) when a locked package is missing or at another version, or when a package that
no lock names is installed (pip itself excepted): then something floated in, for example a new dependency of a
locked package. Standard library only. The locks carry no hashes yet (they could not be made offline); once a
maintainer adds `--hash` lines, pip checks them by itself. See documentation/10-build-test-release.md.
"""
import importlib.metadata
import re
import sys

IGNORED = {"pip"}
_MARKER = re.compile(r'^sys_platform\s*(==|!=)\s*"([^"]*)"$')


def norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def parse(text, platform=None):
    """{normalised name: version} for the lines of a lock that apply on `platform` (default: this one).
    Raises ValueError for a line it does not understand."""
    platform = sys.platform if platform is None else platform
    out = {}
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("--hash"):
            continue
        line = line.rstrip("\\").strip()
        spec, _, marker = line.partition(";")
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*==\s*([^\s\\]+)", spec.strip())
        if not m:
            raise ValueError(f"line {n}: not name==version: {raw.strip()}")
        if marker.strip():
            mm = _MARKER.match(marker.strip())
            if not mm:
                raise ValueError(f"line {n}: only sys_platform markers are understood: {raw.strip()}")
            if (platform == mm.group(2)) != (mm.group(1) == "=="):
                continue
        out[norm(m.group(1))] = m.group(2)
    return out


def installed():
    return {norm(d.metadata["Name"]): d.version for d in importlib.metadata.distributions() if d.metadata["Name"]}


def problems(locked, have):
    out = []
    for name, version in sorted(locked.items()):
        if name not in have:
            out.append(f"missing: {name}=={version}")
        elif have[name] != version:
            out.append(f"wrong version: {name} {have[name]} (locked {version})")
    for name in sorted(set(have) - set(locked) - IGNORED):
        out.append(f"not in any lock: {name}=={have[name]}")
    return out


def main(argv=None):
    paths = sys.argv[1:] if argv is None else argv
    if not paths:
        print(__doc__.strip().splitlines()[2].strip())
        return 2
    locked = {}
    for p in paths:
        with open(p, encoding="utf-8") as f:
            locked.update(parse(f.read()))
    bad = problems(locked, installed())
    for line in bad:
        print(line)
    if bad:
        return 1
    print(f"lock ok: {len(locked)} packages, nothing else installed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
