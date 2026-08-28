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

**Video and sound are encoded once and handed to each guest separately.** The
screen is captured and encoded a single time — a fourth guest costs bandwidth
and nothing else — and the encoded packets are then pushed into a *separate
pipeline per guest*.

That separation is not tidiness. A GStreamer error belongs to the pipeline
rather than the element that raised it, so while the guests shared one, a
single guest's data channel failing stopped the capture and ended the session
for everybody. It happened repeatedly. Now each guest's failure is contained
in their own pipeline: verified by killing one guest mid-stream and watching
the other carry on. On the hardware this was built for that is not
an optimisation — it is the difference between working and not.

Sound comes from the monitor of whatever sink applications are actually playing
into, rather than a fixed device, so it keeps working when the HDMI output or a
virtual sink changes underneath. It is Opus in 10 ms frames, because every one
of those milliseconds is added to the delay. If the machine cannot give audio at
all, the session starts silent rather than not starting — losing sound is a
disappointment, losing the session is a failure.

**Input comes back per guest.** Every guest gets their *own* virtual gamepad,
created through `/dev/uinput`, which the kernel presents exactly like a
controller plugged into the front of the machine. That is what makes them a
second player rather than a second hand on the same pad — and it means anything
that reads controllers, RetroArch included, needs no idea this is happening.

```
screen ─► H.264 encode (once) ─► tee ─┬─► guest 1
sound  ─► Opus encode  (once) ─► tee ─┼─► guest 2
                                      └─► guest 3

guest 1 ─┐
guest 2 ─┼─► input router ─► one uinput pad each ─► /dev/input/eventN ─► the game
guest 3 ─┘
```

## How many can join

Three by default -- a fourth player for a sofa that already has three on it,
which is where the name comes from. From Kodi (**How many can join?**), or:

```sh
python3 -m fourthplayer slots        # what is it now
python3 -m fourthplayer slots 4
python3 -m fourthplayer start --slots 4 --minutes 120   # just this one
```

It applies to the next session, not the one open now, and the add-on says so
when it is changed mid-session. That is not laziness about plumbing: pads are
created when a session opens and kodi-retrobox's picker reads the input devices
at launch, so a pad that appeared later would be a controller the running game
never sees -- a setting that looked like it worked and did nothing.

Numbers above `max_slots` are clamped rather than refused. The default ceiling
is eight, which is where the picker stops being able to lay the players out and
past which none of this has been run.

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

## How it looks

The guest's page uses the console's own palette and typeface — the values in
kodi-retrobox's `ra_players.py` and the Press Start 2P it sets its menus in —
so a phone joining a game belongs to the same machine as the television rather
than looking like a web app that happens to point at it.

It commits to one dark theme on purpose. This is a games console, not a
document: there is no light-mode version of it, so every colour is painted
explicitly instead of inherited. Prose stays in a normal typeface, because a
paragraph set in Press Start 2P is a wall to be decoded rather than read; the
pixel face is for the title, the labels, the PIN and the buttons.

The font is bundled (`web/fonts/`, SIL Open Font License, notice included)
rather than fetched, so a guest with no route to the wider internet — which is
most of the point of this project — still sees the right thing.

## Guests without a controller

A phone with no pad attached gets an on-screen one, laid out as a Mega Drive /
Genesis controller: d-pad left, **A B C** on an arc to the right, START in the
middle. It appears by itself on a touch device with no gamepad, and there is a
link in the prompt for anyone that guess gets wrong.

Two arrangements, from the same markup:

- **Portrait** — the picture on top, the pad below it. A thumb cannot reach the
  middle of an upright phone, and covering a 16:9 video with buttons wastes the
  half of the screen that is already letterboxed.
- **Landscape** — no room below, so the pad floats over the two bottom corners,
  where the thumbs already are.

The d-pad is one surface read as eight directions rather than four buttons, so
a diagonal is genuinely two directions held at once. Everything is pointer
events with capture, so a direction and a face button work together, and a
physical pad and the on-screen one are *merged* rather than one replacing the
other.

One control in the corner does all of it — it is both the readout and the
menu, because what is switched on is evident from the buttons being on the
screen. It shows the layout in use, or the name of a physical controller when
there is one, or "No on-screen pad" when there is neither; tapping it changes
which. Two layouts, remembered per device:

- **Mega Drive** — d-pad, A B C across, START.
- **Super Nintendo** — d-pad, the X/Y/A/B diamond, LB/RB and LT/RT, SELECT and
  START.

Which buttons exist, what they send and where they sit is data (`LAYOUTS` in
`web/app.js`), so another controller — one with sticks, say — is an entry in
that table rather than new code.

The letters are the fiddly part, and `tests/test_layouts.py` checks them rather
than trusting them. The Gamepad API's standard mapping is Xbox-shaped, so index
1 is the *right* face button — which Nintendo prints as A and Sega prints as C.
Each pad therefore sends the index for the position a button occupies, not the
letter written on it, and the test asserts both halves: that B is the bottom
button and that B is drawn below X.

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

## Letting guests start games

Off by default, and the only thing here that reaches past the picture.

A guest with a pad can already drive whatever is in front of them, which
includes Kodi if Kodi is what is on screen. That is worth knowing before
turning any of this on: the interesting question was never whether a remote
guest can browse the television, it is what they can browse it *into*. So
there is no remote Kodi mode here at all. Guests get a list of games in their
own page and the host does the starting.

The catalogue is the whole security model. A guest sends an opaque id and the
only thing an id can turn into is a row that was already on this machine's
disk — read from the RetroArch playlists `sync_games.py` maintains, with box
art and player counts alongside. No path, no core and no command line ever
crosses the wire in either direction, so the worst a tampered-with page can ask
for is a game that was already in the list. `tests/test_launch.py` holds that
down.

Four settings, from Kodi (**Can guests start games?**) or the command line:

```sh
python3 -m fourthplayer policy            # what is it now
python3 -m fourthplayer policy off        # the default
python3 -m fourthplayer policy approve    # ask; 30 seconds to answer
python3 -m fourthplayer policy idle       # only when nothing is running
python3 -m fourthplayer policy open       # any time, over the top of a game
```

`open` means what it says: a guest can stop the game you are playing and start
a different one without asking. RetroArch is asked to close rather than killed,
so save memory is written, but it is still someone else ending your game.

`approve` puts the request on the television — over a running game, since the
overlay is the same override-redirect window the join card uses — with who
asked, what for, and a countdown.

**Answer it by holding both shoulders on the controller in your hand** --
either the two bumpers or the two triggers, whichever your pad has under your
fingers. Triggers are reported as buttons on some pads and as axes on others;
both count.
That is the only answer reachable mid-game: Kodi is behind a fullscreen
emulator and the overlay is click-through on purpose, so every other route
means quitting the game the request is about. A second and a half, because the
press is read passively and the game sees it too — anything shorter would
approve a stranger's game by playing your own. A hold already under way when
the request lands does not count; the bumpers have to come up first. And
guests' own pads are excluded by name, or the person asking could approve
themselves.

There is deliberately no gesture for refusing, because refusing is what happens
when nobody does anything. The prompt also carries **START IT** and **NO**
buttons for a mouse; the overlay takes clicks only while a request is up, and
goes back to click-through the moment it is answered -- it sits over a
fullscreen game, and a window that swallows a click is a window that swallows a
shot.

The other two routes still work when you can reach them — Kodi offers the
request instead of its usual menu while one is waiting, or:

```sh
python3 -m fourthplayer approve
python3 -m fourthplayer deny --reason "not that one"
```

Silence is a refusal. Nothing starts on a timeout.

Two details worth knowing:

**The player picker always appears.** A guest who cannot claim a slot is a
guest whose controller the game ignores, which from their side looks exactly
like the whole thing being broken. It comes for free rather than by asking:
a session creates one virtual pad per slot the moment it opens, so RetroArch
always sees several pads by the time anything can launch, which is the
condition kodi-retrobox's picker appears under. `tests/test_launch.py` checks
that against `ra_players.py`'s own rule rather than assuming it.

**The game is started outside this service's sandbox.** The server runs with
`ProtectHome=read-only` and a short list of writable paths, which is right for
something listening to the internet and fatal for an emulator that writes saves
across the home directory. A child would inherit all of it, so the game is
handed to the user's service manager as its own transient unit instead.

The catalogue is read, never imported: if kodi-retrobox is not installed the
list is simply empty and none of this is available. The two programs couple
through data files, which is what made splitting them worth doing.

## Quick start

```sh
git clone https://github.com/seastwood/Fourth-Player
cd fourth-player
install/install.sh                 # packages, helper, udev, service, add-on
python3 -m fourthplayer check      # says what this machine is still missing

systemctl --user enable --now fourth-player
python3 -m fourthplayer start --minutes 60
```

The service is what the Kodi add-on drives. Until `install.sh` has been run,
the add-on says so rather than pretending — it has no way to start something
systemd has never heard of.

```sh
systemctl --user status fourth-player      # is it up?
journalctl --user -u fourth-player -f      # what is it doing?
```

That prints a link and a PIN, and puts a QR code on the television. Anything
else you need is in `python3 -m fourthplayer --help`.

```
$ python3 -m fourthplayer status
session open, 58m 12s left
  link: https://play.example.com/j/EXAMPLE-TOKEN-NOT-A-REAL-INVITE
  PIN:  570397
  guests: 1/3
    slot 0  Player 2   connected  8134 frames  /dev/input/event20
```

### If it feels laggy

Delay here comes from buffering far more than from picture quality, and there
are three places it accumulates. Worth knowing which knob does what, because
turning the wrong one costs quality for nothing:

| Setting | What it does | Trade |
|---|---|---|
| `bitrate_kbps` | 1500 by default | **Nothing adapts this.** There is no congestion control — `rtpgccbwe` is not in this distribution — so a bitrate the link cannot carry does not soften the picture, it queues packets and becomes delay. Too low is a soft picture; too high is a laggy one |
| `queue_ms` | 60 | How much encoded video may pile up per guest when the link is tight. This *is* delay. It was 200, which handed out a fifth of a second the moment a connection got busy |
| `jitter_ms` | 30 | How long the guest's browser holds frames before playing them. Lower is less delay and more stutter — this is the "buffer a couple of frames" knob, and it trades the opposite way from the other two |

Keyframes are every two seconds rather than every one: a keyframe is several
times the size of the frames around it, so on a thin link one per second is a
burst per second and every burst is a delay spike. Guests joining are sent one
on demand anyway.

```sh
python3 -m fourthplayer serve --bitrate 1200 --queue 40 --jitter 20
```

### Which codec, and who decides

The guest's browser reports what it can decode when it joins, and the host
picks the best encoding both ends manage — H.265 where it is available at both
ends, H.264 otherwise. A browser that says nothing gets H.264, because guessing
better than that on no information is how a black screen happens.

The picture is encoded once for everybody, so the codec belongs to the session
rather than to each guest — and it moves in two directions, which are not
symmetric:

- **Upwards** — to something better — only while nobody else is connected,
  because there is nobody to disturb.
- **Downwards** whenever somebody arrives who cannot decode what is running.
  Everyone already watching is re-offered the new encoding and loses about a
  second of picture, which is a much smaller thing than a guest who cannot join
  at all.

So an iPhone joining an empty session gets H.265, and a friend on Chrome
arriving later moves everybody to H.264 without anyone having to do anything.
`codec` in the config pins it if you would rather decide yourself.

### Trying H.265

Half the bitrate for the same picture, which on a thin link means a better
picture rather than a faster one — it does not reduce delay by itself. The
Radeon encodes it at the same speed as H.264 here, so it costs nothing to run.

The catch is the guest: **most browsers refuse it.** Safari on recent Apple
hardware accepts it, Firefox does not, Chrome mostly does not — and a browser
that refuses gets a black screen, which the page now says out loud.

```sh
python3 -m fourthplayer serve --codec h265
```

Worth pinning if every guest is on an iPhone; otherwise leave `codec` on
`auto` and it will be chosen when it can be.

### If the picture lags

Frame rate costs more than resolution here. The defaults are 720p30 at 6 Mb/s
because halving the frame rate halves the encode load, and on this class of
hardware that is what actually shortens the delay:

```sh
python3 -m fourthplayer serve --fps 60              # if the host has headroom
python3 -m fourthplayer serve --bitrate 3000        # for a thin connection
python3 -m fourthplayer serve --width 960 --height 540
python3 -m fourthplayer serve --software            # no working GPU encoder
python3 -m fourthplayer serve --no-audio           # picture only
python3 -m fourthplayer serve --audio-device NAME  # a specific monitor source
```

`python3 -m fourthplayer check` lists the monitor sources this machine offers.

Everything is also settable in `~/.config/fourth-player/config.json`.

### How long a session lasts

Thirty minutes, an hour, two, four, a number you type, or none at all -- from
Kodi, or:

```sh
python3 -m fourthplayer start --minutes 90
python3 -m fourthplayer start --unlimited     # runs until you close it
```

No deadline is held as an infinite expiry rather than a flag, so every
comparison that asks whether the session is still alive keeps working without
knowing about it. The one place infinity must not reach is a wire: JSON has no
representation for it, and `JSON.parse("Infinity")` is an error in a browser.
So anything leaving the process sends `null` for the remaining time, and the
page shows "no time limit" instead of counting down. `tests/test_duration.py`
holds that down, including that an unlimited session comes back unlimited after
a restart.

`max_duration_minutes` caps a number of minutes. It does not override a
deliberate decision to have no limit -- that is a different kind of answer, and
Kodi asks a second time before taking it.

### Running out of time

Sessions warn their guests at five minutes, two minutes and thirty seconds, and
the host can push the deadline back at any point:

```sh
python3 -m fourthplayer extend --minutes 30
```

Expiry tears everything down off the event loop and releases every pad *before*
the devices disappear, so a game sees the buttons come up rather than losing a
controller mid-press.

### From the sofa

The Kodi add-on (`script.fourthplayer`) is the whole thing without a keyboard:

- **starts the service** if it is not running, and stops it again
- **opens a session** for 30 minutes to 4 hours, and closes it
- **shows the link, the PIN and a QR code** big enough to point a phone at from
  a sofa, with the clock and the slot count updating underneath
- **watches who is playing** on a screen that refreshes while you look at it —
  who is connected, who has wandered off, how much input each is sending
- **adds time** to a session already running, so running out is not an ambush
- **removes a player**, which burns their credential rather than just
  disconnecting them
- **switches picture quality** between three presets, and offers to restart the
  service so it takes effect

Two things catch people out, and neither is a fault in the add-on:

- **A new add-on only appears after Kodi restarts.** Kodi caches its list. Until
  then it is genuinely absent, not hidden.
- **Kodi will not put it on a custom home menu.** After restarting, it shows up
  under *Program add-ons* — but a skin-shortcuts menu like kodi-retrobox's is a
  list somebody arranged by hand, and nothing new joins it by itself:

**On kodi-retrobox there is nothing to do.** That project generates its home
menu (`bin/kodi_menu.py`, after every games sync) and carries a Fourth Player
entry of its own, which appears as soon as this add-on is installed.

If the entry appears but offers to *install* the add-on when chosen, Kodi has
not rescanned: it reads its add-on list once at startup, so an add-on linked in
underneath a running Kodi is on disk and unknown. Restart Kodi once and it is
found. Nothing is actually missing — `Addons.GetAddonDetails` will already show
it as enabled.

Anywhere else, with a skin-shortcuts menu somebody arranged by hand:

```sh
# Stop Kodi first -- it writes this menu back out when it exits.
install/add-kodi-menu.py            # add FOURTH PLAYER to the home menu
install/add-kodi-menu.py --remove   # take it off again
```

It refuses to run in two situations, and both guards were paid for. It stops
when Kodi is running, because Kodi holds the menu in memory and writes it back
on exit — the entry appeared, worked, and was gone an hour later with the file
byte-identical to its backup. And it stops when a generator is installed,
because an entry added by hand to a file that is rebuilt every ten minutes
lasts about ten minutes.

That edits the menu as text rather than through an XML parser, so the twenty-odd
entries already there keep the formatting they were arranged in, and it keeps a
timestamped backup. Safe to run twice.

### Reaching it from outside

**Two ports, not one.** TCP `8443` carries the join page and the PIN; the video
and sound are WebRTC media on **UDP `40000–40100`**. Forwarding only 8443 gives
a working join page and a permanently black picture, which is the single most
common way this fails.

Point the hostname at the box with a **DNS-only record**. A proxy in front of
the signalling — Cloudflare's orange cloud, for one — leaves the page loading
and the join never answered, and it cannot carry the media in any case, so it
protects an address the ICE candidates hand out regardless.

Behind a symmetric NAT — most home routers — a forward is not enough on its own
either, because the address STUN discovers uses a port the router allocated for
talking to STUN and nobody else. The server therefore announces each of its
local sockets a second time at the public address on the *same* port, which is
what the forward actually maps. That is on by default; `advertise_public_ip` and
`public_ip` control it.

Over a VPN none of that applies — a guest on WireGuard is already inside the
network — but packet size does: a tunnel's MTU is smaller, and an RTP packet
that does not fit is dropped rather than split. `rtp_mtu` defaults to 1200 for
that reason. A black picture *with working sound* is almost always this.

Full details, with the pfSense and HAProxy configuration, in
[docs/NETWORK.md](docs/NETWORK.md).

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

## When the host falls over

The server has segfaulted inside the GPU's video driver more than once, and
systemd puts it straight back — but a session that lives only in memory dies
with the process, so everybody was locked out of something that no longer
existed. The invite is therefore written to disk, and a restarted server picks
it up:

- **The link and PIN already in people's hands keep working.** Only digests are
  saved, exactly as in memory, so a stolen copy of the file is worth nothing.
- **Guests reconnect on their own tokens**, including the ones who were playing
  at the moment it died — they never got the chance to leave, which is what a
  crash is.
- **The owner cannot re-read the pair**, because it was never written down.
  `fourth-player reshare` mints a new link and PIN without ending the session
  or disturbing anybody already in it.

```sh
python3 -m fourthplayer reshare
```

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

**Input is sent when it changes, not on a metronome.** A held button is
re-sent every 50 ms so the host's dead-man switch never fires, and anything that
moves goes immediately — so an idle guest costs 20 messages a second instead of
125. That is not a micro-optimisation: a data channel competing with the video
for a congested uplink can push its SCTP association into an error state, and
when that happens the guest's *video* dies with it while ICE still reports the
connection as healthy. It looks exactly like a black screen with no cause.

**A frozen picture is noticed, not just a broken connection.** The guest's page
watches whether bytes are actually arriving, because connection state is not
enough on its own: a tab that has been in the background comes back with its
peer still reporting `connected` while nothing has moved for however long it
was away. Six seconds of no video and it rebuilds, whatever the connection
claims about itself.

**A guest who drops gets back in on their token, not the PIN.** Their slot is
given away immediately — somebody present beats somebody who might return — but
the *claim* outlives it for fifteen minutes, so a browser that still has its
token walks straight back in and takes the next free slot. That is what makes a
network switch cost a second instead of a scramble: by then the PIN is on a
television in somebody else's house. A kicked guest's token is burned and
reclaims nothing, and a genuinely full session refuses a reclaim rather than
evicting anybody.

**A slot comes back about ten seconds after somebody leaves.** Liveness is
measured by hearing from the guest — their browser sends its pad state every
50 ms whether or not anything moved — because nothing else is trustworthy. A
peer *object* survives a guest vanishing, and so does its ICE state: webrtcbin
sits at `connected` indefinitely when the other end simply stops existing,
since nothing arrives to contradict it. Both were tried and both held slots for
people who had gone.

A guest being refused also triggers an immediate sweep before the refusal is
believed: somebody at the door is better served by a slot than by the grace
period, which exists only so a brief reconnect goes unnoticed.

**A guest whose branch errors is rebuilt.** The host attributes a pipeline error
to the guest it came from and re-offers to that one guest; everybody else is
untouched, and the guest keeps their slot unless the rebuild also fails.

**A guest who changes network keeps their slot.** Moving a phone between mobile
data and wifi replaces every address it had, so the connection cannot recover on
its own — it can only be rebuilt. The browser notices, asks the host to re-offer,
and keeps its slot, its pad and its session; the visible cost is a second of
"reconnecting". Attempts are capped and spaced so an unreachable host is not
hammered, and a rejoin that goes unanswered falls back to asking for the PIN
rather than sitting on "rejoining" forever.

**A dropped guest releases their buttons.** Pad state is sent as a complete
snapshot 125 times a second rather than as press and release events, so a lost
packet self-heals on the next one and silence is unambiguous. After 250 ms of it,
the pad is opened. Without that, a guest whose connection dies mid-press leaves
their character walking into a wall until somebody notices.

## The fullscreen button does not use native fullscreen

On a desktop it asks for real fullscreen and gets it. On iOS Safari there is no
fullscreen for anything that is not a video, and the one call that does work --
`webkitEnterFullscreen` -- hands the picture to the system player. The page
behind it stops being the thing on screen, and a page that is not on screen
does not reliably get gamepad readings or timers, so the controller goes dead
exactly when someone has made the picture as big as they can. It covers the
on-screen pad too.

So on iOS the button strips the page back to the picture instead and leaves the
page running and holding the controller. Tapping the screen brings the chips
back. For a genuinely chrome-free screen, add the page to the home screen --
the manifest and the apple-mobile-web-app meta are there for exactly that.

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
addons/         the Kodi add-on: menu, control client, and the QR/monitor screens
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

Played for real with two remote guests plus the host. Video, sound, input,
invites, expiry, extension, kicking, reconnection, the overlay and the Kodi
add-on all work. Not yet done:

- **ICE ports are ephemeral.** `webrtcbin`'s `ice-agent` cannot safely be
  touched from Python on GStreamer 1.24 — reading the property corrupts the
  agent and kills the process at negotiation — so the UDP range cannot be
  bounded from here. `docs/NETWORK.md` says what that means for the firewall.
- **one session at a time**, which is the whole point, but it is a hard limit
  rather than a queue.
- glass-to-glass latency has not been measured with a camera.

## Licence

MIT. See `LICENSE`.
