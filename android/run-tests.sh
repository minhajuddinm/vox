#!/usr/bin/env bash
# Compile and run the Java unit tests. One runner for CI and local use (no device, no Gradle).
#   CI:    .github/workflows/build.yml (android job) calls this with ANDROID_JAR set.
#   Local: a wrapper script kept outside the repo sets JAVA_HOME and ANDROID_JAR, then calls this.
# Usage: bash android/run-tests.sh [--integration]
# Needs a JDK (javac and java on PATH) and ANDROID_JAR = platforms/android-34/android.jar.
# Compiles the sources listed in android/testsrc.list plus every android/test/**/*.java, then runs
# every *Test class (each has a main; ParityTest gets spec/golden.txt). Prints one line per test
# and stops with a non-zero exit at the first failure.
#
# --integration also runs RelayIntegrationTest, which starts the real relay (relay/relay.py) and syncs through
# it. Without the flag that test is skipped. It needs Python 3.9 or newer (python3, python or VOX_PYTHON; the
# test starts and stops the relay itself, on a free port, with a temp data folder) and the pinned org.json jar
# below, which is downloaded with curl on first use. The jar goes before android.jar on that test's classpath,
# because android.jar only has stubs of org.json that throw. (RelayIntegrationTest does not call org.json today:
# RelayClient reads JSON with PlainJson. The jar keeps the real classes ahead of the stubs for a test that does.)
# VOX_TOOLS_CACHE names a folder (outside the repository) to keep the jar in between runs; without it the jar is
# fetched again every run.
set -eu

# The org.json jar the integration test runs with: pinned by version and by SHA-256 (computed from the file on
# Maven Central, whose own .sha1 matched). It is checked every time it is used, and a jar that does not match is refused.
ORG_JSON_VERSION=20240303
ORG_JSON_SHA256=3cf6cd6892e32e2b4c1c39e0f52f5248a2f5b37646fdfbb79a66b46b618414ed
ORG_JSON_URL="https://repo1.maven.org/maven2/org/json/json/$ORG_JSON_VERSION/json-$ORG_JSON_VERSION.jar"

INTEGRATION=0
for arg in "$@"; do
  case "$arg" in
    --integration) INTEGRATION=1 ;;
    *) echo "usage: bash android/run-tests.sh [--integration]" >&2; exit 2 ;;
  esac
done

: "${ANDROID_JAR:?set ANDROID_JAR to the path of android.jar (platforms/android-34)}"
# Make it absolute before the cd below (a relative path would break). Under Git Bash, cygpath also turns a
# POSIX-style path into the Windows-style one the Windows JDK needs.
if command -v cygpath >/dev/null 2>&1; then
  ANDROID_JAR=$(cygpath -ma "$ANDROID_JAR")
else
  case "$ANDROID_JAR" in /*) ;; *) ANDROID_JAR=$PWD/$ANDROID_JAR ;; esac
fi
[ -f "$ANDROID_JAR" ] || { echo "ANDROID_JAR not found: $ANDROID_JAR" >&2; exit 2; }
# The same for the jar cache: a relative folder means relative to where the script was started, not to the repository root.
if [ -n "${VOX_TOOLS_CACHE:-}" ]; then
  case "$VOX_TOOLS_CACHE" in /*|?:*) ;; *) VOX_TOOLS_CACHE=$PWD/$VOX_TOOLS_CACHE ;; esac
fi

cd "$(dirname "$0")/.."   # repository root: paths in testsrc.list and spec/golden.txt are relative to it

# Classpath separator: ';' for the Windows JDK (Git Bash), ':' elsewhere.
SEP=:
case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) SEP=';' ;; esac

OUT=$(mktemp -d)
if command -v cygpath >/dev/null 2>&1; then OUT=$(cygpath -m "$OUT"); fi   # the Windows JDK needs a Windows-style path
trap 'rm -rf "$OUT"' EXIT

sha256_of() {   # sha256_of <file>: its SHA-256 as 64 hex characters
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d ' ' -f 1; else shasum -a 256 "$1" | cut -d ' ' -f 1; fi
}

# Sets ORG_JSON_JAR to the pinned org.json jar, downloaded if it is not in the cache folder yet. A download that does
# not match the pinned SHA-256 is deleted, and a jar in the cache that does not match is refused, never used.
fetch_org_json() {
  command -v sha256sum >/dev/null 2>&1 || command -v shasum >/dev/null 2>&1 || { echo "sha256sum or shasum is needed to check the org.json jar" >&2; exit 2; }
  local dir=${VOX_TOOLS_CACHE:-$OUT/tools} jar part
  mkdir -p "$dir"
  jar=$dir/json-$ORG_JSON_VERSION.jar
  if [ ! -f "$jar" ]; then
    part=$jar.part.$$
    echo "downloading org.json $ORG_JSON_VERSION from repo1.maven.org" >&2
    curl -fsSL --retry 2 -o "$part" "$ORG_JSON_URL" || { rm -f "$part"; echo "could not download $ORG_JSON_URL" >&2; exit 2; }
    [ "$(sha256_of "$part")" = "$ORG_JSON_SHA256" ] || { rm -f "$part"; echo "the downloaded org.json jar does not match the pinned SHA-256; refusing it" >&2; exit 2; }
    mv "$part" "$jar"
  fi
  [ "$(sha256_of "$jar")" = "$ORG_JSON_SHA256" ] || { echo "$jar does not match the pinned SHA-256 of org.json $ORG_JSON_VERSION; refusing to use it (delete it to download it again)" >&2; exit 2; }
  ORG_JSON_JAR=$jar
  if command -v cygpath >/dev/null 2>&1; then ORG_JSON_JAR=$(cygpath -ma "$jar"); fi   # the Windows JDK needs a Windows-style path
}
if [ "$INTEGRATION" = 1 ]; then fetch_org_json; fi

srcs=()
while IFS= read -r line || [ -n "$line" ]; do
  line=${line%$'\r'}
  case "$line" in ''|'#'*) continue ;; esac
  [ -f "$line" ] || { echo "android/testsrc.list: no such file: $line" >&2; exit 2; }
  srcs+=("$line")
done < android/testsrc.list

tests=()
while IFS= read -r f; do tests+=("$f"); done < <(find android/test -name '*.java' | LC_ALL=C sort)
[ "${#tests[@]}" -gt 0 ] || { echo "no test sources under android/test" >&2; exit 2; }

javac -nowarn -Xlint:-options -source 8 -target 8 -encoding UTF-8 -cp "$ANDROID_JAR" -d "$OUT" \
  "${srcs[@]}" "${tests[@]}"

count=0
run_test() {   # run_test <classpath> <fully.qualified.TestClass> [args...]
  local cp=$1 cls=$2 out
  shift 2
  if out=$(java -cp "$cp" "$cls" "$@" 2>&1); then
    count=$((count + 1))
    echo "PASS ${cls##*.}: $(printf '%s\n' "$out" | tail -n 1)"
  else
    echo "FAIL ${cls##*.}"
    printf '%s\n' "$out"
    exit 1
  fi
}

for f in "${tests[@]}"; do
  case "$f" in *Test.java) ;; *) continue ;; esac
  cls=${f#android/test/}
  cls=${cls%.java}
  cls=${cls//\//.}
  case "$cls" in
    *.ParityTest) run_test "$OUT$SEP$ANDROID_JAR" "$cls" spec/golden.txt ;;
    *.RelayIntegrationTest)
      if [ "$INTEGRATION" = 1 ]; then
        run_test "$OUT$SEP$ORG_JSON_JAR$SEP$ANDROID_JAR" "$cls"   # the real org.json before android.jar's stubs
      else
        echo "SKIP ${cls##*.}: needs the real relay (bash android/run-tests.sh --integration)"
      fi ;;
    *)            run_test "$OUT$SEP$ANDROID_JAR" "$cls" ;;
  esac
done
echo "$count tests run"
