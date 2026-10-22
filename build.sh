#!/bin/bash
# Build the VMBox live/installer ISO with Debian live-build.
#
#   sudo ./build.sh
#
# Environment:
#   VMBOX_DIST      Debian release of the image          (default: trixie)
#   VBOX_DIST       release of the Oracle VirtualBox repo (default: VMBOX_DIST)
#   VBOX_PACKAGE    e.g. virtualbox-7.2                   (default: newest)
#   VBOX_KEY_FILE   local copy of oracle_vbox_2016.asc    (default: download)
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"
ROOT=$PWD
LIVE=$ROOT/live
OUT=$ROOT/out
VERSION=$(cat VERSION)
VBOX_KEY_URL=https://www.virtualbox.org/download/oracle_vbox_2016.asc
VBOX_KEY_FPR=B9F8D658297AF3EFC18D5CDFA2F683C52980AECF
export VMBOX_DIST=${VMBOX_DIST:-trixie}

if [ "$(id -u)" -ne 0 ]; then
    exec sudo -E "$0" "$@"
fi
for tool in lb gpg make; do
    command -v "$tool" >/dev/null || { echo "Fehlt: $tool (apt install live-build gnupg make)" >&2; exit 1; }
done

echo "==> VMBox $VERSION für Debian $VMBOX_DIST"

echo "==> Dateien für das Image zusammenstellen"
INC=$LIVE/config/includes.chroot
rm -rf "$INC" "$LIVE/config/includes.binary"
make -s -C "$ROOT" install DESTDIR="$INC"
echo "${VBOX_DIST:-$VMBOX_DIST}" >"$INC/usr/share/vmbox/vbox-dist"
[ -n "${VBOX_PACKAGE:-}" ] && echo "$VBOX_PACKAGE" >"$INC/usr/share/vmbox/vbox-package"
install -D -m 0644 artwork/splash.png "$LIVE/config/includes.binary/.vmbox/splash.png"

echo "==> Oracle-VirtualBox-Signaturschlüssel prüfen"
GNUPGHOME=$(mktemp -d)
export GNUPGHOME
trap 'rm -rf "$GNUPGHOME"' EXIT
if [ -n "${VBOX_KEY_FILE:-}" ]; then
    cp "$VBOX_KEY_FILE" "$GNUPGHOME/key.asc"
else
    curl -fsSL "$VBOX_KEY_URL" -o "$GNUPGHOME/key.asc"
fi
fpr=$(gpg --batch --with-colons --show-keys "$GNUPGHOME/key.asc" | awk -F: '/^fpr/ {print $10; exit}')
if [ "$fpr" != "$VBOX_KEY_FPR" ]; then
    echo "Unerwarteter Schlüssel-Fingerabdruck: $fpr" >&2
    exit 1
fi
install -d "$INC/usr/share/keyrings"
gpg --batch --yes --dearmor -o "$INC/usr/share/keyrings/oracle-virtualbox-2016.gpg" "$GNUPGHOME/key.asc"

echo "==> live-build"
cd "$LIVE"
lb clean
lb config
lb build

ISO=$(find "$LIVE" -maxdepth 1 -name "vmbox-*.iso" | head -n1)
[ -n "$ISO" ] || { echo "Kein ISO erzeugt - siehe live/build.log" >&2; exit 1; }
mkdir -p "$OUT"
mv "$ISO" "$OUT/vmbox-$VERSION-amd64.iso"
(cd "$OUT" && sha256sum "vmbox-$VERSION-amd64.iso" >"vmbox-$VERSION-amd64.iso.sha256")
echo "==> Fertig: $OUT/vmbox-$VERSION-amd64.iso"
