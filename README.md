# fourth-player

**Friends outside your network join the game on your machine, from a browser
link, with their own controllers.** They see the screen, they take a player
slot, you all play together. Nothing to install on their side and no account
anywhere — a link, a PIN, and a controller.

It is a standalone server. It does not need Sunshine, Moonlight, Steam or a
login, and it works on any Linux machine with an X display. It couples to
[kodi-retrobox](https://github.com/seastwood/kodi-retrobox) through one small
Kodi add-on, and that coupling is optional.

---

## How it works

Two paths, and they are deliberately asymmetric.

**Video goes out once.** The screen is captured and encoded a single time, and a
`tee` hands the same encoded bytes to every guest. A fourth guest costs
bandwidth and nothing else. On the hardware this was built for that is not an
optimisation — it is the difference between working and not.

**Input comes back per guest.** Every guest gets their *own* virtual gamepad,
created through `/dev/uinput`, which the kernel presents exactly like a
controller plugged into the front of the machine. That is what makes them a
second player rather than a second hand on the same pad — and it means anything
that reads controllers, RetroArch included, needs no idea this is happening.

```
screen ─► capture + encode (once) ─► tee ─┬─► guest 1
                                          ├─► guest 2
                                          └─► guest 3

guest 1 ─┐
guest 2 ─┼─► input router ─► one uinput pad each ─► /dev/input/eventN ─► the game
guest 3 ─┘
```

## Why there are always three pads

The pads exist for the whole session, not just while somebody is holding them,
so an empty session still shows three controllers in a player picker. That is
deliberate, and it is the lesser of two evils:

- **A pad has to exist before the game starts.** kodi-retrobox's player picker
  enumerates input devices when it launches. A pad created after that is a pad
  that game will never assign to a player.
- **A returning guest has to find the same pad.** Creating devices on demand
  would give a guest who reloads their browser a *new* event node, which the
  game sees as a different controller.

Use `--slots 1` (or `slots` in the config) if you only ever expect one guest and
the spare rows are in the way.

## A guest can only ever move a gamepad

The single most important property here, and it is structural rather than
enforced. Each guest is wired to one `uinput` device that declares gamepad
capabilities **and nothing else** — no keyboard codes, no relative axes. There
is no keyboard path, no mouse path, no clipboard and no file transfer anywhere
in the server.

A guest who completely compromises their own browser tab still cannot type a
character on your machine, because the device they are attached to cannot
express a keystroke. `tests/test_pads.py` asserts this directly.

The price is real: games that need a keyboard and mouse stay local-only. That
was a deliberate trade and it should stay one. If remote desktop control is ever
wanted it must arrive as a separate, explicitly-armed mode — never as a
permission flag on an ordinary guest session.

## Quick start

```sh
git clone https://github.com/seastwood/fourth-player
cd fourth-player
install/install.sh                 # packages, helper, udev, service, add-on
python3 -m fourthplayer check      # says what this machine is still missing

systemctl --user enable --now fourth-player
python3 -m fourthplayer start --minutes 60
```

That prints a link and a PIN, and puts a QR code on the television. Anything
else you need is in `python3 -m fourthplayer --help`.

```
$ python3 -m fourthplayer status
session open, 58m 12s left
  link: https://play.example.com/j/DXmJ0Yh5FsjGqdMZGCBFvAU_dvyGS1qFXtoCV7xOxsU
  PIN:  570397
  guests: 1/3
    slot 0  Player 2   connected  8134 frames  /dev/input/event20
```

### If the picture lags

Frame rate costs more than resolution here. The defaults are 720p30 at 6 Mb/s
because halving the frame rate halves the encode load, and on this class of
hardware that is what actually shortens the delay:

```sh
python3 -m fourthplayer serve --fps 60              # if the host has headroom
python3 -m fourthplayer serve --bitrate 3000        # for a thin connection
python3 -m fourthplayer serve --width 960 --height 540
python3 -m fourthplayer serve --software            # no working GPU encoder
```

Everything is also settable in `~/.config/fourth-player/config.json`.

### Running out of time

Sessions warn their guests at five minutes, two minutes and thirty seconds, and
the host can push the deadline back at any point:

```sh
python3 -m fourthplayer extend --minutes 30
```

Expiry tears everything down off the event loop and releases every pad *before*
the devices disappear, so a game sees the buttons come up rather than losing a
controller mid-press.

For the internet half — the router, the DNS and the reverse proxy — see
[docs/NETWORK.md](docs/NETWORK.md). That is the part most likely to go wrong and
the part this repository cannot do for you.

## What it costs to run

Measured on the machine it was written for, an AMD Phenom II X6 with a Radeon
RX 470, capturing a 1080p desktop:

| Output | Frame rate | CPU |
|---|---|---|
| 720p60, hardware encode | ~54 fps | ~23% of one core |
| 900p60, hardware encode | ~44 fps | ~18% |
| 1080p60, hardware encode | ~36 fps | ~15% |
| 1080p60, software x264 | ~57 fps | 237% (2.4 cores) |

So **720p60 is the default**, and raising it above that buys a sharper picture
and loses frames. The defaults in `config.py` are these measurements, not taste.

One trap worth knowing on AMD: the GPU idles at its lowest DPM state and a
*video encode* load does not wake it — the demand never shows up in
`gpu_busy_percent`, so the governor leaves it asleep and encoding runs at about
two thirds speed. A running game is itself a 3D load and ramps the card, so this
only bites between opening a session and starting something. `fourthplayer/gpu.py`
raises the clocks for the life of a session and puts them back afterwards.

## The session

An invite is two factors that travel by different routes: a 256-bit token in the
link and a six-digit PIN. Only digests are stored. Ten wrong PINs destroy the
invite outright, so a link leaked into a group chat costs a re-share and nothing
more, and per-address lockouts escalate 30s → 2min → 10min.

Splitting the two is not about defeating someone holding a photo of your
television — that is the intended way to use this. It is about the half that
outlives the session. The link ends up in chat history and browser autocomplete;
the PIN never leaves the screen.

Sessions carry the duration you chose, checked both at join and by a sweep, so
an open tab cannot outlive it. Kicking a guest burns their credential, so the
freed slot cannot be retaken by the person you just removed.

**A dropped guest releases their buttons.** Pad state is sent as a complete
snapshot 125 times a second rather than as press and release events, so a lost
packet self-heals on the next one and silence is unambiguous. After 250 ms of it,
the pad is opened. Without that, a guest whose connection dies mid-press leaves
their character walking into a wall until somebody notices.

## Layout

```
fourthplayer/
  protocol.py   the 20-byte pad frame, and nothing else
  pads.py       virtual gamepads and the dead-man switch
  invites.py    who is allowed in, for how long, and how that is revoked
  video.py      the one pipeline, and one WebRTC peer per guest
  session.py    the only module that knows about all of the above
  server.py     a public socket and a Unix control socket
  overlay.py    the QR card and the tally light
web/            the guest's page: WebRTC in, Gamepad API out
addons/         the Kodi add-on
tools/          loopback.py and padwatch.py — see below
tests/          run them all with tests/run.sh
```

`protocol`, `pads` and `invites` have no idea a network exists, which is why
their tests run anywhere in milliseconds.

## Testing

```sh
tests/run.sh                       # every suite; no GPU, no network, no root
```

`tests/test_webframe.py` is worth singling out: `web/frame.js` and
`fourthplayer/protocol.py` are two hand-written struct layouts in two languages,
and nothing but a test stops them drifting. It runs the real `frame.js` under
node and decodes the result with the real Python decoder.

Two tools need real hardware and a running session:

```sh
python3 tools/loopback.py --seconds 10   # a guest with no browser
python3 tools/padwatch.py --seconds 12   # what the kernel actually received
```

`loopback.py` stands in for a browser using a second `webrtcbin` — it proves
signalling, ICE, DTLS, SRTP, the video decoding into real H.264, the data
channel and the pads, all headlessly. What it cannot prove is the JavaScript,
which is what `test_webframe.py` and a human with a controller are for.

## Status

Working and tested end to end on one machine: video, input, invites, expiry,
kicking, the overlay and the Kodi add-on. Not yet done:

- **no audio.** Video only. The pipeline has no Opus track yet, and this is
  the biggest remaining gap.
- **ICE ports are ephemeral.** `webrtcbin`'s `ice-agent` cannot safely be
  touched from Python on GStreamer 1.24 — reading the property corrupts the
  agent and kills the process at negotiation — so the UDP range cannot be
  bounded from here. `docs/NETWORK.md` says what that means for the firewall.
- **one session at a time**, which is the whole point, but it is a hard limit
  rather than a queue.
- glass-to-glass latency has not been measured with a camera.

## Licence

MIT. See `LICENSE`.
