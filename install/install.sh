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

# The service runs with PrivateTmp=yes, so it is given an empty /tmp of its
# own. A checkout under there exists for you and not for it: the unit points
# at a directory the service cannot see and dies at startup with 200/CHDIR,
# which names no cause and sends you looking at the program. Refuse here, where
# the reason is still in front of you.
case "$REPO" in
  /tmp/*|/var/tmp/*)
    cat >&2 <<END
This checkout is at $REPO.

The service is sandboxed with PrivateTmp, which gives it an empty /tmp -- so it
would never find this directory, and would fail at startup with a message that
does not say why. Move the checkout under your home directory and run this
again.
END
    exit 1;;
esac

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

say "the command"
# So that `fourth-player admin add <name>` -- which is what every message in
# this program tells you to run -- is a command that exists. The service does
# not need it; a person at the console does.
mkdir -p "$HOME/.local/bin"
ln -sfn "$REPO/bin/fourth-player" "$HOME/.local/bin/fourth-player"
if command -v fourth-player >/dev/null 2>&1; then
  echo "fourth-player is on your PATH"
else
  echo "linked into ~/.local/bin, which is not on your PATH yet."
  echo "add it with: echo 'export PATH=\"$HOME/.local/bin:$PATH\"' >> ~/.profile"
fi

say "the service"
mkdir -p "$HOME/.config/systemd/user" "$HOME/.local/state/fourth-player"
sed "s|%h/fourth-player|$REPO|" "$REPO/system/fourth-player.service" \
  > "$HOME/.config/systemd/user/fourth-player.service"
systemctl --user daemon-reload
# Enabled here rather than left as an instruction. "enable it with" is a line
# somebody reads once, and the first reboot after that is where it is found
# out -- which is exactly how this console spent a day not starting on its own.
if systemctl --user enable fourth-player >/dev/null 2>&1; then
  echo "enabled: it will start with your session from now on"
else
  echo "could not enable it; run: systemctl --user enable --now fourth-player"
fi

say "the Kodi add-on"
if [ -d "$HOME/.kodi/addons" ]; then
  ln -sfn "$REPO/addons/script.fourthplayer" "$HOME/.kodi/addons/script.fourthplayer"
  echo "linked into ~/.kodi/addons"
  # The menu tile, put where every other tile on that menu lives. Cut from the
  # same drawing as the home screen icon, so the row on the television and the
  # icon on a phone are recognisably the same thing. Copied rather than
  # linked: the menu is read by Kodi, which does not need this repository to
  # still be where it was.
  if [ -f "$REPO/media/menu-tile.png" ]; then
    mkdir -p "$HOME/.kodi/media/consoles"
    cp "$REPO/media/menu-tile.png" "$HOME/.kodi/media/consoles/_fourthplayer.png"
    echo "menu tile in place"
  fi
  # Kodi reads its add-on list once, at startup. Until it rescans, the add-on
  # is on disk and unknown -- and a menu entry pointing at it answers with
  # "you need to install this add-on", which sounds like a packaging fault
  # rather than a stale cache.
  if pgrep -x kodi.bin >/dev/null 2>&1; then
    if [ -x /usr/bin/kodi-send ]; then
      kodi-send --action="UpdateLocalAddons" >/dev/null 2>&1 || true
      echo "asked the running Kodi to rescan its add-ons"
    fi
    echo "if the menu entry still offers to install it, restart Kodi once --"
    echo "a rescan does not always take for a brand new add-on"
  fi
  # kodi-retrobox builds its home menu from its own game library and carries
  # a Fourth Player entry that appears once this add-on is on disk. Its timer
  # would do this within ten minutes; asking now means the entry is there when
  # somebody goes looking for it, which is immediately after installing.
  # add-kodi-menu.py is for everyone else and steps aside here on purpose.
  if [ -x "$HOME/.local/bin/kodi_menu.py" ]; then
    if "$HOME/.local/bin/kodi_menu.py" >/dev/null 2>&1; then
      echo "rebuilt kodi-retrobox's menu, so FOURTH PLAYER is on it now"
    fi
  fi
else
  echo "no ~/.kodi/addons -- skipping (this machine has no Kodi)"
fi

say "config"
[ -f "$HOME/.config/fourth-player/config.json" ] || python3 -m fourthplayer write-config

say "checks"
cd "$REPO"
python3 -m fourthplayer check || true
# Both streams. The suites log through Python's logging, which writes to
# stderr, so a passing run still printed a page of warnings about paused
# pipelines and sessions declining to save -- which reads, to somebody
# installing this for the first time, as a broken install.
LOG=/tmp/fourth-player-install-tests.log
if sh tests/run.sh >"$LOG" 2>&1; then
  echo "tests pass"
else
  echo "TESTS FAILED. The whole run is in $LOG; the end of it:" >&2
  tail -25 "$LOG" >&2
  exit 1
fi

cat <<'NOTE'

Still yours to do, because none of it lives on this machine:
  * forward the WebRTC UDP ports on the router      (docs/NETWORK.md)
  * point a hostname at the box and set public_url  (docs/NETWORK.md)
  * add the HAProxy backend for the page and signalling
NOTE
