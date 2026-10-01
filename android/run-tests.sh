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
# it, and ProxyUploadIntegrationTest (ApiClient's speech upload through the relay's proxy to a stub server). Without the flag that test is skipped. It needs Python 3.9 or newer (python3, python or VOX_PYTHON; the
# test starts and stops the relay itself, on a free port, with a temp data folder). Nothing is downloaded: the
# sync client reads JSON with the pure PlainJson class, so the test needs no org.json jar.
set -eu

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

cd "$(dirname "$0")/.."   # repository root: paths in testsrc.list and spec/golden.txt are relative to it

# Classpath separator: ';' for the Windows JDK (Git Bash), ':' elsewhere.
SEP=:
case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) SEP=';' ;; esac

OUT=$(mktemp -d)
if command -v cygpath >/dev/null 2>&1; then OUT=$(cygpath -m "$OUT"); fi   # the Windows JDK needs a Windows-style path
trap 'rm -rf "$OUT"' EXIT

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
    *.RelayIntegrationTest|*.ProxyUploadIntegrationTest)
      if [ "$INTEGRATION" = 1 ]; then
        run_test "$OUT$SEP$ANDROID_JAR" "$cls"
      else
        echo "SKIP ${cls##*.}: needs the real relay (bash android/run-tests.sh --integration)"
      fi ;;
    *)            run_test "$OUT$SEP$ANDROID_JAR" "$cls" ;;
  esac
done
echo "$count tests run"
