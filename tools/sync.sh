#!/bin/sh
# Push the working tree to the retro box and run the suites there.
# evdev and GStreamer only exist on that machine, so that is where tests run.
set -e
BOX="${FP_BOX:-retro@192.168.1.132}"
KEY="${FP_KEY:-$HOME/.ssh/id_ed25519_retro}"
rsync -a --delete --exclude '.git' --exclude '__pycache__' --exclude 'state' \
      -e "ssh -i $KEY" ./ "$BOX:~/fourth-player/"
