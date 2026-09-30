#!/usr/bin/env bash
# Compile EVERY Android source (android/src/**) against the real android.jar, to type-check the code that the
# unit tests do not reach (DictationService, VoxAccessibilityService, MainActivity, ...). No device, no Gradle,
# no build-tools: nothing is packaged and nothing is written outside a temporary folder.
#   Local: J:\Projects\.bin\javatest.cmd compile   (sets JAVA_HOME and ANDROID_JAR, then calls this)
#   Any:   ANDROID_JAR=<sdk>/platforms/android-34/android.jar bash android/compile-check.sh   (JDK on PATH)
# The real build gets R.java from aapt2, which is not installed here. So this script writes a stub R.java whose
# fields are exactly the R.<type>.<name> references found in the sources (distinct values, so switch labels such
# as `case R.id.x:` never collide). Consequence: a typo in a resource name cannot fail the compile (the stub is
# built from the uses); it only earns a non-fatal warning when nothing under RES_DIR matches, and the real build
# (android/build.sh, CI) is the authority. Not modelled: the int[] arrays of R.styleable (the app has none).
# Env (all optional except ANDROID_JAR; relative paths are relative to the repository root):
#   ANDROID_JAR  path to platforms/android-34/android.jar
#   SRC_DIR      sources to compile (default android/src)
#   RES_DIR      resources, for the warning above (default android/res)
# Prints "compile-check: OK (N files)" and exits 0; otherwise prints javac's output and exits non-zero.
set -eu

: "${ANDROID_JAR:?set ANDROID_JAR to the path of android.jar (platforms/android-34)}"
# Make it absolute before the cd below (a relative path would break). Under Git Bash, cygpath also turns a
# POSIX-style path into the Windows-style one the Windows JDK needs.
if command -v cygpath >/dev/null 2>&1; then
  ANDROID_JAR=$(cygpath -ma "$ANDROID_JAR")
else
  case "$ANDROID_JAR" in /*) ;; *) ANDROID_JAR=$PWD/$ANDROID_JAR ;; esac
fi
[ -f "$ANDROID_JAR" ] || { echo "ANDROID_JAR not found: $ANDROID_JAR" >&2; exit 2; }
command -v javac >/dev/null 2>&1 || { echo "javac not found on PATH (a JDK 17 is needed)" >&2; exit 2; }

cd "$(dirname "$0")/.."   # repository root
SRC_DIR=${SRC_DIR:-android/src}
RES_DIR=${RES_DIR:-android/res}
[ -d "$SRC_DIR" ] || { echo "SRC_DIR not found: $SRC_DIR" >&2; exit 2; }

TMP=$(mktemp -d)
if command -v cygpath >/dev/null 2>&1; then TMP=$(cygpath -m "$TMP"); fi   # the Windows JDK needs a Windows-style path
trap 'rm -rf "$TMP"' EXIT
OUT=$TMP/classes
GEN=$TMP/gen
mkdir -p "$OUT" "$GEN"

srcs=()
while IFS= read -r f; do srcs+=("$f"); done < <(find "$SRC_DIR" -name '*.java' | LC_ALL=C sort)
[ "${#srcs[@]}" -gt 0 ] || { echo "no .java sources under $SRC_DIR" >&2; exit 2; }

# Package of the app: the `package` line of MainActivity.java, else com.minhaj.vox.
pkg=com.minhaj.vox
main=$(find "$SRC_DIR" -name MainActivity.java | head -n 1)
if [ -n "$main" ]; then
  p=$(sed -n 's/^package[[:space:]]\{1,\}\([A-Za-z0-9_.]\{1,\}\)[[:space:]]*;.*/\1/p' "$main" | head -n 1)
  if [ -n "$p" ]; then pkg=$p; fi
fi

# Every distinct R.<type>.<name> used by the sources, sorted so that each type is one contiguous block.
# android.R.* (the framework's own resources, found in android.jar) is not part of the app's R.
refs=$(cat "${srcs[@]}" | sed 's/android\.R\./android_R./g' | grep -oE '\bR\.[a-z]+\.[A-Za-z0-9_]+' | LC_ALL=C sort -u || true)

rdir=$GEN/${pkg//./\/}
mkdir -p "$rdir"
{
  echo "package $pkg;"
  echo "public final class R {"
  n=$((0x7f010000))
  prev=
  while IFS= read -r ref; do
    [ -n "$ref" ] || continue
    rest=${ref#R.}
    type=${rest%%.*}
    name=${rest#*.}
    if [ "$type" != "$prev" ]; then
      if [ -n "$prev" ]; then echo "  }"; fi
      echo "  public static final class $type {"
      prev=$type
    fi
    n=$((n + 1))
    echo "    public static final int $name = $n;"
  done <<<"$refs"
  if [ -n "$prev" ]; then echo "  }"; fi
  echo "}"
} > "$rdir/R.java"

# Non-fatal hint: a referenced resource with no file or XML entry under RES_DIR (aapt2 would fail on it).
if [ -d "$RES_DIR" ]; then
  while IFS= read -r ref; do
    [ -n "$ref" ] || continue
    rest=${ref#R.}
    type=${rest%%.*}
    name=${rest#*.}
    if compgen -G "$RES_DIR/$type*/$name.*" >/dev/null; then continue; fi
    if grep -rqE "name=\"$name\"|@\+id/$name\b" "$RES_DIR"; then continue; fi
    echo "compile-check: warning: $ref has no matching resource under $RES_DIR" >&2
  done <<<"$refs"
fi

# --release 8 checks the language level and the java.* APIs against Java 8 (what the app is built for);
# android.jar on the class path supplies the android.* and org.json classes.
if ! javac --release 8 -Xlint:-options -encoding UTF-8 -cp "$ANDROID_JAR" -d "$OUT" "${srcs[@]}" "$rdir/R.java"; then
  echo "compile-check: FAILED" >&2
  exit 1
fi
echo "compile-check: OK (${#srcs[@]} files)"
