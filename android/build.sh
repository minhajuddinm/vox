#!/usr/bin/env bash
# Builds Vox.apk with the plain Android SDK tools (no Gradle, no dependencies).
# Needs: JDK 17+, Android build-tools 34 and platform android-34.
# Usage: ANDROID_HOME=/path/to/sdk ./build.sh
set -euo pipefail
cd "$(dirname "$0")"

SDK="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/android-sdk}}"
# Newest installed build-tools (d8 from 34.0.0 crashes on some classes; 35+ is fine).
BT="${BUILD_TOOLS:-$(ls -d "$SDK"/build-tools/*/ | sort -V | tail -1)}"
BT="${BT%/}"
JAR="$SDK/platforms/android-34/android.jar"
[ -f "$JAR" ] || { echo "android.jar not found at $JAR"; exit 1; }

rm -rf build && mkdir -p build/gen build/classes build/dex

echo "> resources"
"$BT/aapt2" compile --dir res -o build/res.zip
"$BT/aapt2" link -I "$JAR" --manifest AndroidManifest.xml \
  --min-sdk-version 26 --target-sdk-version 34 \
  -A assets --java build/gen -o build/base.apk build/res.zip

echo "> java"
javac -nowarn -Xlint:-options -source 8 -target 8 -encoding UTF-8 \
  -bootclasspath "$JAR:$BT/core-lambda-stubs.jar" -d build/classes \
  $(find src build/gen -name '*.java')

echo "> dex"
"$BT/d8" --release --min-api 26 --lib "$JAR" --output build/dex \
  $(find build/classes -name '*.class')

echo "> package"
cp build/base.apk build/unsigned.apk
(cd build/dex && zip -q -u ../unsigned.apk classes.dex)
"$BT/zipalign" -f 4 build/unsigned.apk build/aligned.apk

KS="vox.keystore"
# Keystore password: the ANDROID_KEYSTORE_PASS secret in CI (KS_PASS). The old default keeps existing keys working.
PASS="${KS_PASS:-voxvox}"
if [ -f "$KS" ]; then
  [ -n "${KS_PASS:-}" ] || echo "Warning: no KS_PASS (the ANDROID_KEYSTORE_PASS secret): the key is opened with the old default password." >&2
  echo "> signing with the existing $KS"
else
  case "${GITHUB_REF:-}" in
    refs/tags/v*) echo "No $KS on a tag build: refusing to sign a release with a throw-away key." >&2; exit 1 ;;
  esac
  echo "> no $KS: generating a throw-away key. This APK cannot update an installed Vox signed with another key."
  keytool -genkeypair -keystore "$KS" -storepass "$PASS" -keypass "$PASS" -alias vox \
    -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=Vox"
fi
# The password goes through the environment, not the command line (where other processes could read it).
export KS_PASS="$PASS"
"$BT/apksigner" sign --ks "$KS" --ks-pass env:KS_PASS --key-pass env:KS_PASS \
  --out build/Vox.apk build/aligned.apk
"$BT/apksigner" verify build/Vox.apk
echo "Built android/build/Vox.apk"
