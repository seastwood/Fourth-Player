#!/bin/sh
# Let this account read the login screen, so Fourth Player can show it.
#
#   install/greeter-access.sh [user]
#
# Needs root once. What it grants is narrow: LightDM will run a small script
# as root whenever a greeter starts, and that script lets one named local user
# connect to the greeter's display. Not root, not the network, and gone when
# that greeter is.
#
# Its own script rather than lines inside install.sh, because it is worth
# being able to run on its own -- the installer skips it whenever nobody is
# there to be asked, and then this is what the message points at.
set -eu

REPO="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
WHO="${1:-$(id -un)}"
CONF=/etc/lightdm/lightdm.conf.d/90-fourth-player.conf
BIN=/usr/local/bin/fourth-player-greeter-access

if ! id -u "$WHO" >/dev/null 2>&1; then
  echo "there is no user called \"$WHO\"" >&2
  exit 1
fi
if [ ! -d /etc/lightdm/lightdm.conf.d ]; then
  echo "no LightDM here, so there is no greeter to be let into" >&2
  exit 1
fi

sudo install -m 755 "$REPO/bin/fourth-player-greeter-access" "$BIN"
# The user is written in rather than left to a default. LightDM runs the
# script as root, and root cannot tell which account this console belongs to.
sudo sh -c "sed 's|@USER@|$WHO|' '$REPO/bin/fourth-player-greeter.conf' > '$CONF'"
sudo chmod 644 "$CONF"

echo "installed for $WHO; it takes effect the next time LightDM starts."
echo "to start it now:  sudo systemctl restart lightdm   (ends your session)"
