#!/bin/sh
# Push the working tree to the retro box and run the suites there.
# evdev and GStreamer only exist on that machine, so that is where tests run.
set -e

# No default host on purpose: this pushes a working tree somewhere and deletes
# what is not in it, which is not a thing to guess about.
: "${FP_BOX:?set FP_BOX to user@host of the machine to sync to}"
BOX="$FP_BOX"
KEY="${FP_KEY:-$HOME/.ssh/id_ed25519}"
rsync -a --delete --exclude '.git' --exclude '__pycache__' --exclude 'state' \
      -e "ssh -i $KEY" ./ "$BOX:~/fourth-player/"
