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

### Or a keyboard

An on-screen pad is no use on a laptop: there is no touchscreen to put it on,
and a mouse cannot hold two buttons down. **Keyboard** is a third choice in the
same menu, and it turns keys into buttons — the arrow keys for the d-pad, and
one key each for the four face buttons, both shoulders, both triggers, select
and start.

The defaults are the arrangement anybody who has played an emulator on a
keyboard already has in their hands, because this console *is* RetroArch:
arrows for the d-pad, **Z** and **X** on the two buttons a two-button game
uses, **A** and **S** above them, **Q W E R** for the shoulders and triggers in
the order they sit on a controller, **Enter** for start and **right shift** for
select.

The sticks are missing and cannot be otherwise: a key is down or it is not, and
a stick is a position. A game that needs one needs a controller, and the panel
says so where the sticks would have been rather than leaving somebody hunting.

Every binding is shown on the button it belongs to in **Controls**, and
changing one is clicking that button and pressing the key you want. A key
already spoken for *trades* with the button it is taken from rather than being
bound twice — two buttons on one key means one of them can never be pressed by
itself. **Use defaults** puts the whole set back.

The map is stored as key *positions* (`KeyboardEvent.code`), not letters, so a
French or German keyboard keeps the same shape under the fingers rather than
silently rearranging itself. It is this guest's own, kept in their browser, and
the host is never told any of it: what leaves the page is the pad frame it
always was, with the keys merged into it exactly as the on-screen buttons are.
Holding a key, a thumb and a controller at once works, and none of the three
cancels the others.

`tests/test_keyboard.py` covers the defaults, the labels and the trade.

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

Guests can now *play* on a keyboard, and that is not a hole in this: it is the
guest's own browser deciding that their Z key means the A button, before
anything is sent. What goes on the wire is the same pad frame it always was.
The property here is about what the host can be made to do, not about what the
guest happens to be holding.

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

The game list is drawn at 94% opacity rather than opaque, and the page listens
for the video being paused. Both are load-bearing and neither looks it.

A browser is entitled to pause a video it considers completely obscured, and
Safari does. Nothing here listened for `pause`, so the picture stayed paused
for ever and reloading was the only way back -- and on iOS the same judgement
about the page not being watched throttles the timers, which takes the input
loop and eventually the socket with it. From the host that reads as a guest
going quiet a few seconds after picking a game, with nothing broken on this
side at all.

So the list leaves the video being composited, and a pause is reported and
undone rather than accepted. Making that panel opaque again, or dropping the
`pause` listener as redundant, brings the whole thing back.

Four settings, from Kodi (**Can guests start games?**) or the command line:

```sh
python3 -m fourthplayer policy            # what is it now
python3 -m fourthplayer policy off        # the default
python3 -m fourthplayer policy approve    # ask; 30 seconds to answer
python3 -m fourthplayer policy idle       # only when nothing is running
python3 -m fourthplayer policy open       # any time, over the top of a game
```

A game a guest starts **begins at its title screen**. That is the opposite
default from the television's own menu, where picking a game resumes the save
on the box -- because there it is somebody carrying on with their own game, and
here it is a guest starting one on a machine they are not sitting at. Dropping
into the middle of somebody else's save, then writing over it on the way out,
is not a thing to do without being asked.

Continuing is offered, and only when there is something to continue from: the
catalogue looks for RetroArch's automatic save state and the page shows
"Continue where it was left" with the date only if it finds one. The television
says which of the two is being asked for when it asks you to approve.

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
cd Fourth-Player                   # capital F, as GitHub spells it
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

### Why a blip used to black the picture out

A browser that loses a frame asks for a fresh keyframe, and webrtcbin turns
that into an upstream force-key-unit event. It arrived at the guest's appsrc
and stopped there: the encoder is in the capture pipeline, not the guest's, so
nothing was listening. The guest then waited for the next *periodic* keyframe
-- `fps * 2`, which is two seconds at thirty frames a second -- and two seconds
of black after a momentary loss is what that looks like.

Those requests are now carried across to the encoder, so recovery is about one
round trip instead of up to two seconds. Rate-limited to one every half second,
because the encoder is shared: four guests on a bad connection all asking at
once would otherwise turn the stream into keyframes, which is the one thing
guaranteed to make a struggling link worse.

Asking for a whole new picture is a heavy way to recover from losing one
packet, though, and for a while it was the only way a guest had. The offer
named the feedback webrtcbin writes by itself -- `nack pli`, `ccm fir`, both of
which mean "send me a keyframe" -- and never `nack` on its own, which means
"send me that packet again". So a single packet lost on a wifi hop cost the
whole picture until a fresh keyframe had been encoded and had arrived. The
video caps now ask for it and the video transceiver has `do-nack`, which is
what makes webrtcbin offer an rtx payload type and keep what it sent long
enough to send it a second time. Both halves are needed: the caps tell the
browser it may ask, and do-nack is what can answer.

If blips are still visible, the other lever is `jitter_ms`, which is 60.
Raising it to 100 or 120 absorbs brief jitter at the cost of exactly that much
added delay -- worth trying in that order, since this one costs nothing and
that one costs latency.

Blackouts that recur on a **regular** interval are a different animal, and no
amount of `jitter_ms` will touch them: they are the signalling socket being cut
by whatever sits in front of this, not the media. The giveaway is that the
guest comes back on a new UDP port each time, meaning the peer connection was
rebuilt rather than interrupted. See `docs/NETWORK.md` -- "The one-minute
blackouts".

### Which of the three it was, when a guest says it froze

A picture that stops for a moment and starts again is counted by the browser
and by nobody else. The host knows what it sent; it cannot see what a guest
saw. So the guest's page reports what its own connection recorded — how many
freezes, for how long, how many packets were lost, how many it asked back, how
many keyframes it had to ask for, and how long its buffer was holding frames —
at most once every fifteen seconds, into the host's log beside everything else
that guest reported.

Three shapes, three different answers:

* **Packets lost, and asked back.** The link is dropping things. Retransmission
  is doing its job; if the freezes persist, the bitrate is above what the link
  carries.
* **Nothing lost, and the host's own log says `video stopped for … ms`.** The
  encoder produced nothing for that long, and no amount of buffering at the
  other end will fill a gap that was never sent.
* **Nothing lost, nothing missing, and the held-back figure near zero.** The
  browser is playing frames the moment they arrive and has nothing in hand
  when one is late. That is what `jitter_ms` is for.

### If it feels laggy

Delay here comes from buffering far more than from picture quality, and there
are three places it accumulates. Worth knowing which knob does what, because
turning the wrong one costs quality for nothing:

| Setting | What it does | Trade |
|---|---|---|
| `bitrate_kbps` | 1500 by default | **Nothing adapts this.** There is no congestion control — `rtpgccbwe` is not in this distribution — so a bitrate the link cannot carry does not soften the picture, it queues packets and becomes delay. Too low is a soft picture; too high is a laggy one |
| `queue_ms` | 60 | How much encoded video may pile up per guest when the link is tight, measured as arrival time rather than bytes — a keyframe is a burst several times the size of an ordinary frame, and a byte limit would be overrun by every one of them. This *is* delay. It was 200, which handed out a fifth of a second the moment a connection got busy |
| `jitter_ms` | 60 | How long the guest's browser holds frames before playing them. Lower is less delay and more stutter — this is the "buffer a couple of frames" knob, and it trades the opposite way from the other two |

Both of those were written down long before they were connected to anything.
`queue_ms` was read by nothing at all, and `jitter_ms` was set on webrtcbin's
`latency` property — which sizes the buffer for media coming *in*, and this
host only ever sends, so it was inert. The number is now sent to the guest
with the offer and applied there, on the receiver, which is the only end that
can hold a frame back. A guest whose picture stops for a moment without a
single packet being lost is the shape this leaves: the browser had decided
when to draw a frame before the last packet of it arrived.

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

### Changing which player you are, mid-game

A game that is running has bound its player ports to devices, and will not
revisit that until it restarts. So a guest who joins halfway through -- or a
second person arriving after one player claimed a slot -- had no way to be
given controls except by stopping the game and starting it again.

They do now: **You are playing as** in the Buttons panel moves them onto a
different pad. That works because the pad is what the port is bound to, so
moving onto the pad that is already player 2 makes them player 2, instantly and
with nothing restarted. Taking a pad somebody else is on swaps the two, both
pads are released first so neither is left holding a direction, and everybody
is told who ended up where.

What it cannot do is conjure a port that was never bound. A one-player game
started by one person has only one, and a second player still needs the game
restarting -- the picker is what assigns ports, and it runs before RetroArch
does.

### Names, and getting in without the link

A guest may give a name on the join form. It is optional, remembered in their
browser, and sent again when they come back, so a reconnect does not turn them
into a slot number. It is trimmed to sixteen printing characters before it goes
anywhere, because it is drawn on somebody else's television and a name full of
newlines is a card that no longer reads.

Somebody arriving puts a card on the television for five seconds, and tells the
guests already playing. The host is usually looking at a game rather than a
roster, and a controller coming to life with no explanation is how a guest gets
blamed for something the cat did.

By default a guest needs the whole link *and* the PIN. `link open` -- in Kodi,
**How do guests get in?** -- lets them in on the address and the PIN alone, so
it can be read out loud rather than sent:

```sh
python3 -m fourthplayer link            # what is it now
python3 -m fourthplayer link open       # address and PIN
python3 -m fourthplayer link required   # the default
```

### Sharing one controller

Some games were built to be played by passing a pad round a sofa -- Advance
Wars, hot-seat strategy, anything that takes turns. Everybody playing is player
one, and swapping seats between turns is not the same thing as both of you
having the controls.

In Kodi it is **Can players share a controller?**; from a shell:

```sh
python3 -m fourthplayer share            # which rule is in force
python3 -m fourthplayer share on         # picking a taken controller joins it
python3 -m fourthplayer share off        # the default: picking it swaps you
```

With it on, choosing a controller somebody already holds puts you on it beside
them rather than displacing them, and the game sees everybody on it merged --
any number of you, not a pair:
buttons are or-ed, and each stick takes whichever of you has pushed it furthest
from centre. That second rule is the one that matters in practice -- taking the
newest frame instead would mean a passenger's resting thumb, arriving between
two of the driver's frames, straightened the car out. Somebody letting go
releases only what they were holding; the pad is only dropped when the last of
them lets go, however many that took.

Off by default, because when everybody is their own player, being silently
joined to somebody else's controller would be baffling.

### A PIN you choose

By default every session gets six fresh digits, which have to be read off the
television before anybody can join. If that is a chore, set one and it is used
for every session from then on:

In Kodi it is **What PIN do guests type?**, which asks on the number pad so the
remote can answer it; from a shell:

```sh
python3 -m fourthplayer pin             # which rule is in force
python3 -m fourthplayer pin 246813      # 4 to 12 digits, from now on
python3 -m fourthplayer pin ""          # back to a new random one each time
```

It takes effect on the session that is already open, not just the next one, and
it survives a restart: a re-share hands out a new link with the same PIN. It is
stored in `~/.config/fourth-player/config.json`, which is written `0600` because
it is now a password for the television.

Worth knowing what you are trading. A PIN that never changes is one secret that
stops rotating: it is exposed for longer, to everybody you have ever invited,
and it is no longer thrown away with the session. Nothing else changes -- it is
still only ever stored as a digest, still never written to a log, and still
behind the same lockout, which shuts an address out after three wrong tries
without spending the invite everybody else is holding. Pair it with `link
required` if you want the second secret back, or choose more than six digits,
which is why up to twelve are allowed.

It used to be the only way a home-screen icon kept working. Adding the page to
a phone's home screen saves the address it is on, and that address carries an
invite that dies with the session -- so the icon worked once and then did not.

An icon now lands on the plain address whichever way the link setting is
turned. The token comes out of the address bar as soon as a guest is in, the
manifest starts at `/` rather than at whatever invite was open when the icon
was made, and the key that got somebody in is remembered -- so the icon opens,
recognises the session, and asks for the PIN. When the host has opened a *new*
session since, which is the one thing a saved key cannot survive, the join page
says so and shows a box to paste the new link into. The key is 43 characters of
base64url and nobody is typing that off a television, so that box takes the
whole link and finds the key inside it: an address, an address with the
tracking rubbish a chat app added, one wrapped in angle brackets by a mail
client, or one pasted out of the middle of a sentence. The plain address with
no key in it at all is the one answer it refuses to guess at -- sending
`https` as a key earns a refusal that reads exactly like a link that has been
replaced.

Getting a link wrong is no longer able to end the session, either. Ten wrong
PINs destroy the invite outright, which is the right answer for six digits
where guessing is the threat; a link is 43 characters of random, where it is
not, and now that guests paste one in by hand a few fumbles would otherwise
take down the game everybody else was already playing. A wrong link still
counts against the address it came from, so a stranger at the door is still
slowed down.

That is one secret instead of two, and worth thinking about rather than
switching on by habit. What makes it defensible is the lockout: six digits,
three wrong tries, then thirty seconds, two minutes, ten -- roughly a hundred
days of guessing per address for one session. A link that *is* offered is still
checked either way, so a stale one fails loudly instead of quietly working.

### Sound, and the code on the television

Two things the service reads once at startup, so both cost a restart -- which
is why they are one screen in Kodi, **Sound and the television**, rather than
two entries that each end the session:

* **Sound** in the stream, on or off.
* **The join code on the television**, shown or hidden. Worth hiding if the
  set is somewhere you would rather not leave a way in on display.

### The address links are built on

Set it in Kodi under **Address for links**, or:

```sh
python3 -m fourthplayer url                              # what is it now
python3 -m fourthplayer url fourthplayer.example.com     # the scheme is optional
python3 -m fourthplayer url ""                           # back to this machine
```

With nothing set, links point at this machine's address on the local network,
which works from the sofa and nowhere else. That failure is silent in the worst
way -- the link looks right, gets sent to a friend, and goes nowhere -- so the
status line says which of the two it is doing, and Kodi shows what a link will
look like after the change.

A bare host gets `https://` put in front of it, because that is what a person
types. A path is kept, for a reverse proxy serving this under a prefix.
Anything that cannot be a base -- another scheme, a space in the host, a query
string -- is refused rather than guessed at. `tests/test_address.py`.

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

### One game hung this card, and it took three goes to see why

Written first because it cost a power supply to learn. On the machine this was
built for — a Radeon RX 470 — capturing at 1080p **hangs the graphics card**,
and takes the game and the stream down with it. Measured twice from a clean
boot, running a GameCube game with a guest connected:

| Capture | Result |
|---|---|
| 720p30 | four minutes, 28.8–29.7 fps throughout, no kernel errors at all |
| 1080p30 | ninety seconds, then a GPU reset |

```
amdgpu 0000:01:00.0: GPU reset begin!. Source: 1
amdgpu 0000:01:00.0: suspend of IP block <vce_v3_0> failed -22
amdgpu 0000:01:00.0: GPU reset succeeded, trying to resume
amdgpu 0000:01:00.0: VRAM is lost due to GPU reset!
```

`vce_v3_0` is the hardware H.264 encoder. Once it goes the screen is black,
the process cannot even be killed cleanly, and the card needs a reboot. A new
power supply was fitted between those two measurements and changed nothing, so
it is what the encoder is being asked to do rather than the power it draws.

`Source: 1` on that reset is the scheduler timing out a hung job, which is why
this reads as a hang rather than a brownout.

**It was one game, not the resolution.** Kept here in full because the wrong
answer was reached twice, and the shape of the mistake is more useful than the
conclusion.

A GameCube game kept resetting the graphics card mid-session — VRAM lost, the
picture black, the emulator unkillable. It happened within ninety seconds at
1080p and took minutes at 720p, so 1080p looked like the cause. Then it
happened at 720p too:

```
21:00:31  capture running: 1280x720 @30, 1500 kb/s
21:03:12  amdgpu: GPU reset begin!. Source: 1
21:03:17  amdgpu: VRAM is lost due to GPU reset!
```

An earlier run at exactly those settings had lasted twelve minutes. So not a
threshold either.

What it actually was: **one game.** *Smuggler's Run*, which drives around an
open world, hung the card reliably while being played. *Mario Power Tennis*, on
the same emulator at the same settings, played as long as anyone liked. The
resolution only changed how quickly the first one fell over, which is exactly
what made it look responsible.

Two things worth taking from that:

* **A game that hangs the card will say so**, in
  `dmesg | grep "VRAM is lost"`. Nothing else is reliable — not how it felt,
  not how long it lasted, and certainly not which setting was changed most
  recently.
* **The lever for a game like that is not the capture size.** Software
  encoding (`"hardware_encode": false`) takes the encoder out of the picture
  at a cost in CPU; a card newer than Polaris encodes 1080p60 without
  noticing. Dropping to 720p buys minutes, not safety.

A power supply was replaced along the way, on the strength of a single power
connector and a plausible story. It changed nothing. Worth remembering before
buying a part to fix a hang.

### If guests watch on something bigger than a phone

720p is chosen for frame rate, and it shows on a large screen: a 1080p desktop
captured at 1280x720 is downscaled before the encoder ever sees it, so the
softness is lost detail rather than lost bitrate, and no amount of extra
bitrate brings it back. Capturing at the display's own size is the fix, at
30 fps rather than 60. Measured on the same machine, with a **guest actually
connected**:

| Capture | Bitrate | Delivered | While playing |
|---|---|---|---|
| 1080p30, nothing running | 3000 kb/s | ~28.6 fps, 2.8 Mb/s | — |
| 1080p30, Dolphin (GameCube) | 3000 kb/s | ~26.4 fps, 2.7 Mb/s | emulator at 116% of a core |
| 1080p30, Dolphin, sustained | 3000 kb/s | **the card hung after 90 s** | see the warning above |

Dolphin is about the heaviest thing here, so that second row is close to the
worst case: a tenth of the frames, for a picture with twice the detail. Lighter
cores hold the full rate. Audio was unaffected in both — the same 48–49 packets
a second — which is worth checking separately, because a starved encoder
usually takes the sound with it.

```json
{ "width": 1920, "height": 1080, "fps": 30, "bitrate_kbps": 3000 }
```

**Read the section above before setting that**, which is where the same
machine's encoder gave out after ninety seconds. What follows is what to raise
*if the card can take it*.

Two things to raise with it, neither obvious:

* `h264_profile` from `constrained-baseline` to **`main`**. Baseline has no
  CABAC, which costs several per cent of quality for nothing. It was the safe
  default because webrtcbin sent no `a=fmtp` line and a strict browser assumed
  baseline; that offer now states the profile honestly, so main is no longer a
  gamble — see the comment in `config.py`.
* `audio_frame_ms` from `10` to **`20`**. Half as many packets for the same
  sound, so there is half as much to lose, at the cost of 10 ms. Chopped audio
  on an otherwise fine picture is usually this rather than bandwidth.

And `jitter_ms` upwards — 100 for a guest on the far side of the internet.
That one is pure trade: exactly that much added delay for exactly that much
tolerance.

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

**A phone that was in a pocket comes back, and there is a button for when it
does not.** Everything above mends a connection that broke while somebody was
watching it. A tab that has been in the background for ten minutes is a
different animal: its timers were frozen, the system closed its socket without
telling the page, and the peer connection it hands back may still report
`connected` over a path that has carried nothing since. Every piece of the
recovery here knew how to mend itself and none of them could, because each was
waiting on a count that had run out while nobody was looking — the socket
backoff had grown to its fifteen-second ceiling, the rebuild allowance was
spent, and a single refused resume had thrown the credential away for good.

Coming back to the page — a `visibilitychange`, or a `pageshow` from the
back/forward cache, which is what a phone does to a tab it froze rather than
discarded — now resets all three and rebuilds in one path. The socket is
replaced rather than reused even when it claims to be open, because after a
long sleep that claim is worth nothing and the alternative is a renewal sent
into a closed pipe and twenty seconds of waiting to find out. A refused resume
no longer burns the credential either: the first refusal stops the page
retrying on its own, and the next deliberate attempt gets one more go, because
the usual reason for a refusal is a slot the host had not yet swept — and the
sweep is done by the attempt. Twice is a real no, and then it asks for the PIN.

And there is now a **Reconnect** button, beside the pip that says the
connection is unwell. It appears exactly when that pip is not green, it does
the same thing the page does for itself on return, and it exists because
"try it again now" is what a person reaches for and there was nothing to reach
for. A picture that has stopped is also enough to open the chips, so the button
is on screen rather than behind the hamburger.

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

## Getting closer to part of the picture

A guest is watching a whole television through whatever is in their hand, and
on a phone that is a picture about as wide as two fingers. What they actually
need is often a corner of it — a health bar, a lap counter, the map in the top
right — and there was no way to get closer to it.

**Pinch the picture** to zoom it, up to four times, and **drag it** to move
about at that zoom. A pinch grows what is between the fingers rather than
whatever happens to be in the middle, so getting to a corner is one gesture
instead of a zoom and then a hunt. On a desktop the wheel does the same thing
around the pointer, and there is a **zoom** button in the chips that expands a
slider, built like the volume one beside it. Double-click, or drag the slider
back to 1, and the whole picture is back.

Two details that decide whether it feels right rather than approximately
right:

- **Dragging stops at the edges of the picture, not of the video element.** A
  16:9 stream inside a taller phone is letterbox black above and below; being
  able to drag the game off into that black would be a way to lose it
  entirely. The slack is half of however much the picture overhangs the
  screen, and it is nothing at all until it does overhang.
- **Nothing is asked of the host.** This is a transform on the video element —
  a magnifying glass held over what has already arrived. The stream is the same
  resolution and bitrate it was, the hud does not scale with it, the on-screen
  pad stays where the thumbs are, and the host is never told any of it
  happened. Zooming in does not sharpen anything, and it costs nothing.

One thing zooming changed that nobody asks for and everybody notices: where the
picture *paints*. An untransformed video is a plain block and sits underneath
everything positioned over it — the chips, the on-screen pad, the panels —
without anyone having to say so. A transformed one is its own stacking context,
which browsers hand to the compositor, and a composited layer can come up over
siblings that were painting above it a moment earlier. What that looked like
was a zoomed picture drawn over the row of chips. The order is stated in the
stylesheet now rather than inherited from the page order, and
`tests/test_zoom.py` checks it stays that way.

The picture takes its own touch gestures now (`touch-action: none` on the video
alone), because the browser's page zoom and this one fought: the page zoomed,
the fixed stage slid out from under the visual viewport, and the chips went
with it. Safari does not report a pinch as pointer events at all — it
recognises the gesture itself and cancels the pointers it was made of — so its
`gesture*` events are handled as well, or iPhones would have got the browser's
zoom and nobody else would have.

`tests/test_zoom.py` covers both pieces of arithmetic: the slack at each zoom,
including the letterboxed case where there is none, and that the point being
zoomed towards stays under the fingers doing it.

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

**"All suites passed" depends on where you ran it.** The suites that exercise
the browser half shell out to `node`, and the ones that touch pads import
`evdev`; each **skips, loudly, when its dependency is missing**. A machine with
node and no evdev and a machine with evdev and no node will both report
everything passing while running different halves. If the box runs the service
but has no node, run the suite on a workstation as well before believing it.

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
