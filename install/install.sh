#!/bin/sh
# Install fourth-player for the current user.
#
# Idempotent: safe to run again after a pull. It installs packages, puts the
# privileged helper in place, links the Kodi add-on, and runs the tests, then
# tells you what is still yours to do (the router, the DNS, the proxy).
set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LIBEXEC=/usr/local/libexec/fourth-player-clocks

say() { printf '\n== %s\n' "$1"; }

say "packages"
MISSING=""
for pkg in $(grep -v '^#' "$REPO/install/packages.txt" | tr -d '\r'); do
  [ -n "$pkg" ] || continue
  dpkg -s "$pkg" >/dev/null 2>&1 || MISSING="$MISSING $pkg"
done
if [ -n "$MISSING" ]; then
  echo "installing:$MISSING"
  sudo apt-get update -qq
  # shellcheck disable=SC2086
  sudo apt-get install -y $MISSING
else
  echo "all present"
fi

say "the GPU clock helper"
sudo install -D -m 0755 "$REPO/system/fourth-player-clocks" "$LIBEXEC"
sudo install -D -m 0440 "$REPO/system/fourth-player-sudoers" /etc/sudoers.d/fourth-player
# A malformed sudoers file locks the machine out of sudo entirely, so check it
# and take it straight back out if it does not parse.
if ! sudo visudo -cf /etc/sudoers.d/fourth-player >/dev/null; then
  sudo rm -f /etc/sudoers.d/fourth-player
  echo "the sudoers rule did not parse and was removed; clocks stay on auto" >&2
fi

say "uinput"
if [ ! -e /dev/uinput ]; then
  sudo modprobe uinput || true
  echo uinput | sudo tee /etc/modules-load.d/uinput.conf >/dev/null
fi
if [ ! -w /dev/uinput ]; then
  printf 'KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", GROUP="input", OPTIONS+="static_node=uinput"\n' \
    | sudo tee /etc/udev/rules.d/60-fourth-player-uinput.rules >/dev/null
  sudo udevadm control --reload-rules
  sudo udevadm trigger --sysname-match=uinput
  echo "granted access to /dev/uinput (log out and back in if it still fails)"
else
  echo "already writable"
fi

say "the service"
mkdir -p "$HOME/.config/systemd/user" "$HOME/.local/state/fourth-player"
sed "s|%h/fourth-player|$REPO|" "$REPO/system/fourth-player.service" \
  > "$HOME/.config/systemd/user/fourth-player.service"
systemctl --user daemon-reload
echo "enable it with: systemctl --user enable --now fourth-player"

say "the Kodi add-on"
if [ -d "$HOME/.kodi/addons" ]; then
  ln -sfn "$REPO/addons/script.fourthplayer" "$HOME/.kodi/addons/script.fourthplayer"
  echo "linked into ~/.kodi/addons"
else
  echo "no ~/.kodi/addons -- skipping (this machine has no Kodi)"
fi

say "config"
[ -f "$HOME/.config/fourth-player/config.json" ] || python3 -m fourthplayer write-config

say "checks"
cd "$REPO"
python3 -m fourthplayer check || true
sh tests/run.sh >/dev/null && echo "tests pass"

cat <<'NOTE'

Still yours to do, because none of it lives on this machine:
  * forward the WebRTC UDP ports on the router      (docs/NETWORK.md)
  * point a hostname at the box and set public_url  (docs/NETWORK.md)
  * add the HAProxy backend for the page and signalling
NOTE
