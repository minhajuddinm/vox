#!/usr/bin/env bash
# Compile and run the Java unit tests. One runner for CI and local use (no device, no Gradle).
#   CI:    .github/workflows/build.yml (android job) calls this with ANDROID_JAR set.
#   Local: J:\Projects\.bin\javatest.cmd sets JAVA_HOME and ANDROID_JAR, then calls this.
# Needs a JDK (javac and java on PATH) and ANDROID_JAR = platforms/android-34/android.jar.
# Compiles the sources listed in android/testsrc.list plus every android/test/**/*.java, then runs
# every *Test class (each has a main; ParityTest gets spec/golden.txt). Prints one line per test
# and stops with a non-zero exit at the first failure.
set -eu

: "${ANDROID_JAR:?set ANDROID_JAR to the path of android.jar (platforms/android-34)}"
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

run_test() {   # run_test <fully.qualified.TestClass> [args...]
  local cls=$1 out
  shift
  if out=$(java -cp "$OUT$SEP$ANDROID_JAR" "$cls" "$@" 2>&1); then
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
    *.ParityTest) run_test "$cls" spec/golden.txt ;;
    *)            run_test "$cls" ;;
  esac
done
