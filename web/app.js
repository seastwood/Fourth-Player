/* The guest side: take the picture, send the pad, and nothing else.
 *
 * The frame this builds is the same 20 bytes `fourthplayer/protocol.py` reads,
 * and `tests/test_webframe.py` checks the two agree byte for byte rather than
 * trusting that two hand-written struct layouts stayed in step.
 *
 * Pad state is sent on a fixed 8 ms timer whether or not anything changed. That
 * looks wasteful -- it is 2.5 kB/s -- and it is what makes the host's dead-man
 * switch possible: silence can only mean the guest is gone if a present guest
 * is always talking.
 */

/* Whether this is the home-screen copy rather than a tab.
 *
 * It matters because the two are laid out differently: a tab sits below
 * Safari's own chrome, while the home-screen copy is drawn under the status
 * bar, and the safe-area inset only describes that gap on a phone with a
 * notch. On one without, the inset is zero, the status bar is still there, and
 * the chips at the top of the screen ended up behind the clock and the
 * battery. The stylesheet floors the gap for this case; this is how it knows.
 *
 * The stylesheet has a matching display-mode query, which covers this
 * wherever iOS answers it; navigator.standalone is the belt and braces,
 * because iOS answered display-mode late and inconsistently for home-screen
 * apps. */
if (window.navigator.standalone === true
    || (window.matchMedia
        && window.matchMedia("(display-mode: standalone)").matches)) {
  document.documentElement.classList.add("standalone");
}

const SEND_HZ = 125;
// Idle heartbeat. The host releases a pad after 250 ms of silence, so anything
// well inside that keeps a held button held -- and sending only when something
// changes takes an idle guest from 125 messages a second to 20. That matters:
// the data channel sharing a congested link with the video is what pushed the
// SCTP association into an error state, which killed the whole peer and left
// the guest black while everything else looked healthy.
const HEARTBEAT_MS = 50;
// Sticks jitter by a count or two at rest. Without this, "changed" is always
// true and the saving disappears.
const AXIS_EPSILON = 600;
// Bytes already queued on the input channel before we stop adding to it. A pad
// frame is 20 bytes, so this is a couple of hundred frames -- far more than a
// healthy channel ever holds, and far less than it takes to fail.
const BACKLOG_LIMIT = 4096;

/* iOS Safari zooms on a double tap and `touch-action: manipulation` does not
 * reliably stop it -- Apple honours that property for scrolling decisions but
 * still runs the zoom gesture on top of a page that has not consumed the
 * second tap. Consuming it here is what actually works.
 *
 * Only the *second* tap of a quick pair is cancelled, so single taps, scrolls
 * and pinch-zoom all behave normally. Pinch is left alone on purpose: taking
 * zoom away from someone who needs it, to fix a gesture, is a poor trade. */
let lastTapEnd = 0;
document.addEventListener("touchend", (event) => {
  /* Not on the pad. Two presses of a button inside 350 ms is not a double tap
     to be swallowed, it is somebody playing, and cancelling the second one
     takes the switch flip -- and so the feeling -- with it. The pad does not
     need this guard anyway: `touch-action: none` on it stops the zoom by
     saying so rather than by cancelling touches after the fact. */
  if (event.target && event.target.closest && event.target.closest(".touch")) {
    lastTapEnd = 0;
    return;
  }
  const now = Date.now();
  if (now - lastTapEnd <= 350) event.preventDefault();
  lastTapEnd = now;
}, { passive: false });

// The desktop equivalent, and a belt-and-braces for Safari's own dblclick.
document.addEventListener("dblclick", (event) => event.preventDefault(),
                          { passive: false });

const el = (id) => document.getElementById(id);
const gate = el("gate"), stage = el("stage"), video = el("screen");

let socket = null, pc = null, input = null;
let seq = 0, ticker = null, padIndex = null;
let guestToken = null, ended = false, retries = 0, retryTimer = null;
/* How many times in a row a resume has been refused. One refusal stops the
 * page retrying on its own -- hammering a host that has already said no helps
 * nobody -- but it no longer throws the credential away, because the usual
 * reason for a refusal is a slot that had not been swept yet, and the sweep is
 * done by the attempt. So the button, and coming back to the page, each get
 * one more go; a second refusal is a real no and asks for the PIN. */
let resumeRefused = 0;

/* The key out of a link, from whatever it is pasted inside.
 *
 * People paste the whole line they were sent, and it arrives with the address
 * in front of it, a full stop after it, or wrapped in whatever the messaging
 * app did to it. The key itself is what follows /j/ and stops at the first
 * slash, question mark or hash -- and a bare key, pasted on its own, is
 * already that. */
function keyFrom(text) {
  let raw = String(text == null ? "" : text).trim();
  if (!raw) return "";
  const at = raw.lastIndexOf("/j/");
  if (at >= 0) {
    raw = raw.slice(at + 3);
  } else if (/[:/\s]/.test(raw)) {
    // An address with no key in it: the plain one, pasted by somebody who did
    // not have the link. Answering "https" would be worse than answering
    // nothing, because nothing is what they gave.
    return "";
  }
  raw = raw.split(/[/?#\s]/)[0];
  // Whatever the message it arrived in wrapped it in, and whatever sentence
  // it was pasted out of. The key's own alphabet is base64url -- letters,
  // digits, - and _ -- so none of this can belong to it.
  raw = raw.replace(/^[<("'\[]+/, "").replace(/[.,;:!>)\]}'"]+$/, "");
  try {
    return decodeURIComponent(raw);
  } catch (_) {
    return raw;                    // a stray % is not a reason to refuse it
  }
}

const linkStore = "fp-link";

function savedKey() {
  try { return localStorage.getItem(linkStore) || ""; } catch (_) { return ""; }
}

function rememberKey(key) {
  try {
    if (key) localStorage.setItem(linkStore, key);
  } catch (_) { /* a private window: it will be asked for again */ }
}

function forgetKey() {
  try { localStorage.removeItem(linkStore); } catch (_) {}
}

const pathToken = location.pathname.startsWith("/j/")
  ? decodeURIComponent(location.pathname.slice(3))
  : "";
/* The key this page is working with: the one it was opened on, or the last one
 * that worked. It decides which session's guest credential is ours, so it is
 * settled before anything is read back. */
let linkKey = pathToken || savedKey();
let token = pathToken;

/* The guest credential belongs to one session's invite, so it is filed under
 * it: a key that has been replaced must not hand back a credential the host
 * has already forgotten. */
function credKey() {
  return "fp:" + linkKey.slice(0, 16);
}

/* ---- the gate ---- */

let joinTimer = null;

/* What this browser can actually decode, so the host can encode the best thing
 * both ends manage rather than guessing. Safari takes H.265 and most others do
 * not, and the difference at 1.5 Mb/s is worth asking about. */
function videoCodecs() {
  try {
    const caps = RTCRtpReceiver.getCapabilities("video");
    if (!caps) return [];
    const seen = new Set();
    for (const codec of caps.codecs) {
      const name = (codec.mimeType || "").split("/")[1];
      if (name) seen.add(name.toLowerCase());
    }
    return [...seen];
  } catch (_) {
    return [];                    // saying nothing gets H.264, which is safe
  }
}

el("pin-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const pin = el("pin").value.trim();
  if (pin.length < 4) return;
  el("join").disabled = true;
  el("join").textContent = "Joining…";
  // A join that is never answered must not leave a dead button and no
  // explanation. The socket can fail to open, or open and close without a
  // word, and both used to look identical to nothing happening at all.
  clearTimeout(joinTimer);
  joinTimer = setTimeout(() => {
    if (!gate.hidden) fail("The host did not answer. Try again.");
  }, 12000);
  const who = (el("who").value || "").trim().slice(0, 16);
  try { localStorage.setItem(nameKey, who); } catch (_) {}
  // The address this page was opened on wins; then whatever was pasted into
  // the field; then the last key that worked, which is what an icon on a home
  // screen relies on. Nothing at all when the host does not require a link:
  // a stale key sent to a session that no longer wants one is checked anyway
  // and refused, which would be a strange way to fail a join that should have
  // succeeded on the PIN alone.
  const typed = keyFrom(el("key").value);
  linkKey = pathToken || typed || savedKey();
  token = pathToken || (linkRequired ? linkKey : "");
  // Kept in memory, never stored: a second controller on this machine claims
  // a seat of its own, which means claiming it the same way this one did.
  // Asking somebody to read the PIN off the television a second time to seat
  // the person next to them would be a poor answer to "make it simple".
  sessionPin = pin;
  const knock = { t: "join", token, pin, name: who, codecs: videoCodecs() };
  // Said at the door rather than once inside. A session locked to accounts
  // admits nobody who has not named themselves, so a login you could only
  // reach from within would be a door the owner had shut behind them.
  const user = (el("gate-user").value || "").trim();
  const pass = el("gate-pass").value || "";
  if (user && pass) {
    knock.login = { name: user, password: pass,
                    code: (el("gate-code").value || "").trim() };
    el("gate-pass").value = "";
    el("gate-code").value = "";
  }
  connect(knock);
});

function myName() {
  try { return localStorage.getItem(nameKey) || ""; } catch (_) { return ""; }
}

/* Adding this page to a home screen saves the address it is on, and that
   address carries an invite that dies with the session -- so the icon worked
   once and then did not, whatever the host had chosen.

   Both halves of that are mended now. The token comes out of the address once
   a guest is in, so what a home screen captures is the plain address; and the
   key that worked is remembered, so the plain address still knows which
   session it belongs to. What the icon asks for on the next launch is the PIN,
   and the link only when the host has opened a new session since -- which is
   the one thing a saved key genuinely cannot survive, and there is a box for
   it on the page for exactly that. */
let linkRequired = true;

fetch("/mode", { cache: "no-store" })
  .then((r) => r.json())
  .then((mode) => {
    linkRequired = mode.require_link !== false;
    askForKeyIfNeeded();
    const note = el("gate-home");
    note.hidden = false;
    note.textContent = linkRequired
      ? "You can add this page to your home screen — it remembers the link you "
        + "last joined with, and there is a box for a new one when the host "
        + "opens a new session."
      : "You can add this page to your home screen — next time just open it "
        + "and enter the new PIN.";
  })
  // Unreachable, or answering something that is not JSON. The safe assumption
  // is the stricter one, which is what linkRequired already says.
  .catch(() => askForKeyIfNeeded());

/* Whether to ask for the link, and how loudly.
 *
 * Three states, and only the last one needs a box: opened on a link, opened
 * without one but with a key that worked before, and opened with neither --
 * which is a home screen icon on the day the host opened a new session. */
function askForKeyIfNeeded() {
  const row = el("key-row"), note = el("key-note");
  if (!row || !note) return;
  if (!linkRequired || pathToken) {
    row.hidden = true;
    note.hidden = true;
    return;
  }
  if (linkKey) {
    row.hidden = true;
    note.hidden = false;
    note.textContent = "Using the link you last joined with. If the host has "
      + "opened a new session since, this will say so and ask for the new one.";
    return;
  }
  row.hidden = false;
  note.hidden = false;
  note.textContent = "This session needs its link as well as the PIN. Paste "
    + "the whole thing — the address, the key, all of it.";
}

/* After a refusal, put the box where they can reach it.
 *
 * The host answers a wrong PIN, an unknown link and an expired one in the same
 * words on purpose, so that guessing tells a guesser nothing -- which means
 * this page cannot know which half was wrong either. It shows both, and keeps
 * the remembered key rather than throwing it away: somebody who mistyped the
 * PIN would otherwise have to go and find their link again to correct it,
 * and somebody whose link really has been replaced only has to paste the new
 * one over the top. Either way the next attempt can succeed. */
function offerKey() {
  if (!linkRequired || pathToken) return;
  const row = el("key-row"), note = el("key-note");
  if (!row || !note) return;
  row.hidden = false;
  note.hidden = false;
  note.textContent = savedKey()
    ? "If the host has opened a new session since you last played, the link "
      + "you had is no longer good. Paste the new one here."
    : "This session needs its link as well as the PIN. Paste the whole thing "
      + "— the address, the key, all of it.";
}

function forgetTokenInAddress() {
  if (!pathToken) return;
  try {
    // Same page, plainer address. Done after joining, so a reload before that
    // still has the invite to work with.
    history.replaceState({}, "", "/");
  } catch (_) { /* not worth failing a join over */ }
}

function fail(message) {
  clearTimeout(joinTimer);
  const box = el("gate-error");
  box.textContent = message;
  box.hidden = false;
  el("join").disabled = false;
  el("join").textContent = "Join the game";
  el("pin").placeholder = "000000";
}

/* ---- signalling ---- */

/* The signalling socket is not the stream. Losing it should cost nothing while
 * the WebRTC connection is up -- so this reconnects quietly in the background
 * and tells the host whether the media it set up is still running, so it knows
 * not to renegotiate a picture that never stopped. */
/* Whether media is genuinely flowing, which is not the same as the connection
 * saying it is up. After the tab has been in the background for a while, iOS
 * hands back an RTCPeerConnection still reporting "connected" over a path that
 * has carried nothing for minutes. Telling the host that is live makes it keep
 * the dead peer and re-point signalling at it, so the picture never comes
 * back -- which is exactly what "I had to enter the PIN again" was. */
let mediaFresh = false;

function mediaIsLive() {
  return pc !== null && pc.connectionState === "connected" && mediaFresh;
}

function reconnectSoon() {
  if (ended || retryTimer || !guestToken || resumeRefused) return;
  const delay = Math.min(15000, 1000 * Math.pow(2, retries++));
  if (!mediaIsLive()) setLink("warn");
  retryTimer = setTimeout(() => {
    retryTimer = null;
    connect({ t: "resume", guest: guestToken, name: myName(),
              codecs: videoCodecs(), media: mediaIsLive() ? "live" : "new" });
  }, delay);
}

function connect(hello) {
  // A remembered device rides on every knock, whichever one it is.
  //
  // It was added to the two reconnect paths and not to the one on the load
  // path -- which is the only one that runs when somebody closes the app and
  // opens it again, so the case it was built for was the case it missed. It
  // goes on here, once, where there is nothing left to forget: a login dies
  // with its socket, and this is the only thing that brings it back without
  // asking. An empty string is simply no device, and the host reads it as
  // nobody.
  if (hello && !hello.login && !hello.device) {
    const device = savedDevice();
    if (device) hello.device = device;
  }

  // Every resume gets a deadline, not just the one on the load path.
  //
  // Reconnect sent a resume and armed nothing, so a resume that was never
  // answered -- which is what a page returning from a long spell in the
  // background usually gets, its slot swept and its token burned -- left the
  // chip saying "reconnecting" and nothing else ever happening. Refreshing
  // worked because that is the one path that did arm a deadline. Pressing the
  // button that exists for exactly this did not.
  // Only if one is not already running: reconnectSoon() retries on a backoff,
  // and re-arming per attempt would push the deadline out for ever on a
  // connection that keeps failing quickly -- which is the same "stuck on
  // reconnecting" this is meant to end, reached by a different road.
  if (hello && hello.t === "resume" && !rejoinTimer) armRejoinTimer();
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  socket = new WebSocket(`${scheme}//${location.host}/ws`);

  socket.addEventListener("open", () => socket.send(JSON.stringify(hello)));
  socket.addEventListener("close", (event) => {
    if (ended) return;
    // A socket we have already replaced, closing after the fact. Without this
    // its handler schedules a reconnect on top of the connection that
    // replaced it, and two sockets answer the same offers.
    if (socket && event.target !== socket) return;
    // Closed before we were ever let in: say so, rather than leaving a
    // disabled button and a page that appears to have stopped caring.
    if (!gate.hidden) {
      // Only if nothing more useful has already been said: the host usually
      // sends a reason and then closes, and a generic line arriving second
      // would replace the explanation with a shrug.
      if (el("gate-error").hidden) {
        fail("Lost contact with the host before joining. Try again.");
      } else {
        el("join").disabled = false;
        el("join").textContent = "Join the game";
      }
      return;
    }
    // Say nothing alarming if the game is still playing perfectly well.
    if (!mediaIsLive()) setLink("warn");
    reconnectSoon();
  });
  socket.addEventListener("error", () => {
    if (!gate.hidden) fail("Could not reach the host. Try again.");
  });

  socket.addEventListener("message", async (event) => {
    const message = JSON.parse(event.data);
    switch (message.t) {
      case "joined":   return joined(message);
      case "offer":    return await answer(message);
      case "ice":      return pc && pc.addIceCandidate({
                                candidate: message.candidate,
                                sdpMLineIndex: message.sdpMLineIndex });
      case "ending":   return setChip("clock", timeLeft(message.remaining) + " left", "warn");
      // "extended" carries the new remaining, which is null if the owner
      // turned the deadline off entirely.
      case "extended": return startClock(message.remaining);
      case "closed":   return sessionOver(message.reason);
      case "error":    return onError(message);
      case "games":         return paintShelf(message);
      case "launchresult":  return launchResult(message);
      case "launchpolicy":  return launchPolicy(message);
      case "starting":
        // The ports are decided while the game comes up, so ask again once it
        // has had time to. Without this the seats stay as they were until
        // somebody opens the panel.
        askSeatsUntilKnown();
        return showNotice(
        "<p><strong>" + escapeText(message.label) + "</strong> is starting on "
        + "the television.</p>", false);
      case "arrived":       return somebodyArrived(message);
      case "pads":          return seatsFrom(message);
      case "hold":          return holdInput(message);
      case "loggedin":      return loggedIn(message);
      case "loggedout":     return loggedOut();
      case "granted":       return granted(message);
      case "limits":        return limitsFrom(message);
      case "reshared":      return reshared(message);
      case "people":        return peopleFrom(message);
      case "chat":          return heardChat(message);
      case "chatlog":       return (message.messages || []).forEach(heardChat);
      case "note":          return showNotice(
        "<p>" + escapeText(message.message) + "</p>", false);
      case "launchdenied":  return showNotice(
        "<p>Not started: " + escapeText(message.reason || "refused") + "</p>",
        false);
    }
  });
}

function sessionOver(reason) {
  ended = true;
  showHud(true);
  if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; }
  setChip("clock", "session ended", "bad");
  setLink("bad", reason || "");
  if (ticker) { clearInterval(ticker); ticker = null; }
  // Nothing is coming back, so stop pretending: drop the saved credential so a
  // reload asks for a PIN rather than silently failing to resume.
  try { localStorage.removeItem(credKey()); } catch (_) {}
}

/* A resume that is never answered must not leave the page saying "rejoining"
 * forever. The credential may be stale, the session may have ended, or the
 * host may be wedged -- from here they are indistinguishable, and all three
 * have the same remedy: ask for the PIN again. */
let rejoinTimer = null;

function clearRejoinTimer() {
  if (rejoinTimer) { clearTimeout(rejoinTimer); rejoinTimer = null; }
}

/* Long enough for a slow phone on a slow network to finish a resume, short
   enough that somebody is not left staring at a chip. A resume that lands
   clears this in joined(), so this only ever fires on one that did not. */
// Covers the whole attempt, retries and all, rather than each try -- so it
// has to be long enough for a couple of backoff steps on a slow network.
const REJOIN_LIMIT_MS = 20000;

function armRejoinTimer() {
  clearRejoinTimer();
  rejoinTimer = setTimeout(() => {
    rejoinTimer = null;
    // Whichever screen they are on. This used to be `if (!gate.hidden)` --
    // only when the join screen was already showing, which is the one case
    // where nobody needs to be sent to it. Somebody who was playing, and is
    // the person this was written for, got nothing. A resume that lands
    // clears the timer, so there is no need to ask again whether it did.
    if (!ended) askForPin("That did not get you back in.");
  }, REJOIN_LIMIT_MS);
}

function askForPin(why) {
  guestToken = null;
  try { localStorage.removeItem(credKey()); } catch (_) {}
  clearRejoinTimer();
  backToGate();
  el("pin").placeholder = "000000";
  el("pin").value = "";
  el("join").disabled = false;
  // Which secrets to ask for. Telling somebody to enter the PIN, when the
  // session wants a link as well and this page has none it can trust, is an
  // instruction that earns the same refusal a second time.
  const needsLink = linkRequired && !pathToken;
  offerKey();
  fail(why + (needsLink ? " Check the link and the PIN, and try again."
                        : " Enter the PIN to join again."));
}

/* Put the join screen back.
 *
 * The page went one way only: gate.hidden = true on the way in and nothing
 * that ever set it back. So asking for the PIN again wrote the reason into
 * the gate's error box while the gate was hidden, and somebody watching a
 * chip that said "That link or PIN is not valid" had no way to act on it --
 * the page had told them the answer and left them on a screen with no way to
 * give it.
 *
 * The picture stops here as well. A stream left running behind the join
 * screen is somebody else's game still making noise at a person being asked
 * for a PIN. */
function backToGate() {
  if (!gate.hidden) return;                // already there
  if (ticker) { clearInterval(ticker); ticker = null; }
  if (video) {
    try { video.pause(); } catch (_) {}
    video.srcObject = null;
  }
  try { if (pc) pc.close(); } catch (_) {}
  pc = null;
  input = null;
  stage.hidden = true;
  gate.hidden = false;
  hideNotice();
  // Whatever it was showing belongs to a session this page is no longer in.
  setLink("");
}

/* Refusals this page cannot wait out: there is nothing to resume, and the
   only way forward is the PIN. Anything else -- a full session, a lockout --
   leaves the credential worth keeping. */
const HOPELESS = ["credential", "closed"];

function onError(message) {
  if (message.reason === "shut") {
    // The one refusal with something to do about it: say who you are.
    const box = el("gate-account");
    if (box) box.hidden = false;
    const why = el("gate-account-why");
    if (why && message.message) why.textContent = message.message;
    askForPin(message.message);
    return;
  }
  if (!gate.hidden) {
    askForPin(message.message);
    return;
  }
  // Already playing.
  //
  // Only refusals about getting in count towards a dead credential. An error
  // about the thing they just asked for -- a seat already taken, a picker
  // that cannot open with no game running -- is not evidence that the link is
  // stale, and counting it meant two failed seat changes threw somebody back
  // to the PIN screen.
  if (message.reason === "request" || message.reason === "login") {
    // A login that did not work is about the login, not about the invite.
    // Counting it would throw somebody back to the PIN screen for mistyping
    // their own authenticator code, which is the same mistake "request" was
    // added to stop.
    setLink("bad", message.message);
    return;
  }
  resumeRefused += 1;
  setLink("bad", message.message);

  // The host says why. A stale link or an ended session cannot come good by
  // being tried again, so the PIN screen goes back up at once rather than
  // after a second refusal -- and the second refusal never came, because the
  // first one stops the page retrying. That is how somebody ended up looking
  // at "That link or PIN is not valid" with nothing to do about it.
  const hopeless = HOPELESS.includes(message.reason)
    // An older host that says no reason at all: fall back to its words, which
    // are fixed strings on the other side of this connection.
    || (!message.reason && /link or PIN is not valid|no session open/i
                             .test(message.message || ""));
  if (hopeless || resumeRefused >= 2) askForPin(message.message);
}

function send(message) {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(message));
  }
}

function joined(message) {
  retries = 0;
  resumeRefused = 0;
  // It got somebody in, so it is worth keeping: this is what makes the icon on
  // a home screen work on its own next time.
  if (linkKey) rememberKey(linkKey);
  clearTimeout(joinTimer);
  clearRejoinTimer();
  if (message.guest) guestToken = message.guest;
  try { if (message.guest) localStorage.setItem(credKey(), message.guest); } catch (_) {}
  launchPolicy(message.launch);
  seatsFrom(message.pads);
  // Who they proved they were at the door, if they did. Before the hold is
  // painted below: whether their controller reaches a Steam game depends on
  // this, and a page told it was held and only then told it was an account
  // would flash the wrong answer.
  if (message.account) loggedIn(message.account);
  else loggedOut();
  if (message.limits) limitsFrom(message.limits);
  // Before the early return below, which is the case this was reported in: a
  // page whose stream never stopped comes back through that branch, and it
  // was keeping whatever notice it had when it went away. The hold is
  // broadcast when it changes, so a game that started while this page was in
  // the background changed it to nobody listening -- and "Controls paused"
  // stayed up over a picture that was plainly playing.
  if (message.hold) holdInput(message.hold);
  forgetTokenInAddress();
  if (message.resumed_media) {
    setLink("ok");
    startClock(message.remaining);
    return;                      // the stream never stopped; leave it alone
  }
  gate.hidden = true;
  stage.hidden = false;
  // Which guest this page is. Kept as the number rather than the name because
  // two people are free to call themselves the same thing, and "which of
  // these is me" has to have exactly one answer.
  mySlot = typeof message.slot === "number" ? message.slot : mySlot;
  setChip("slot", message.label, "ok");
  setLink("");
  showHud();
  startClock(message.remaining);
  startPadLoop();
}

async function answer(message) {
  if (pc) { try { pc.close(); } catch (_) {} }
  pc = new RTCPeerConnection({ iceServers: [] });
  // Per connection, not per page. The page survives a host restart and a
  // reconnect -- that is the point of it -- so a flag set once at the top of
  // the file meant the second connection said nothing about its sound, which
  // is exactly the connection somebody is listening to after a fix.
  soundTold = false;

  // Video and audio arrive as separate tracks. Collect them into one stream
  // rather than replacing srcObject on the second one, which drops the first.
  const incoming = new MediaStream();
  pc.addEventListener("track", (event) => {
    incoming.addTrack(event.track);
    if (video.srcObject !== incoming) video.srcObject = incoming;
    holdVideoBack(message.jitter);
    startPlayback();
  });

  pc.addEventListener("datachannel", (event) => {
    input = event.channel;
    input.binaryType = "arraybuffer";
    input.addEventListener("open", () => setLink("ok"));
    input.addEventListener("close", () => {
      setLink("bad", "controller offline");
      lastSent = null;
      // The channel can die on its own -- a congested link can push the SCTP
      // association into an error state while ICE still says connected, so
      // nothing else will notice. Without this the video stops and the page
      // sits there looking fine.
      if (!ended && pc && pc.connectionState !== "closed") renewSoon(1000);
    });
    input.addEventListener("error", () => renewSoon(500));
  });

  pc.addEventListener("icecandidate", (event) => {
    if (event.candidate && socket.readyState === 1) {
      socket.send(JSON.stringify({
        t: "ice",
        candidate: event.candidate.candidate,
        sdpMLineIndex: event.candidate.sdpMLineIndex,
      }));
    }
  });

  pc.addEventListener("connectionstatechange", () => {
    if (pc.connectionState === "connected") {
      setLink("ok");
      clearMediaTimeout();
      clearRenewTimer();
      renewals = 0;
      lastBytes = -1;
      connectedAt = Date.now();          // it now has to prove it carries something
      stalledSince = 0;
      mediaFresh = false;
      startWatchdog();
      setTimeout(() => report("video playing"), 5000);
    }
    // "disconnected" often mends itself in a second or two, so give it that
    // long. "failed" never does: the addresses it was using are gone.
    if (pc.connectionState === "disconnected") {
      setLink("warn");
      renewSoon(4000);
    }
    if (pc.connectionState === "failed") {
      clearMediaTimeout();
      renewSoon(0);
    }
  });

  // A picture that never arrives has to say so. Signalling succeeds, the PIN
  // is accepted, the slot is taken -- and then nothing, because the media
  // ports are not reachable. Left alone that is a black screen with no
  // explanation, which is exactly how this failed from outside the network.
  armMediaTimeout();

  await pc.setRemoteDescription({ type: "offer", sdp: message.sdp });
  holdVideoBack(message.jitter);
  const local = await pc.createAnswer();
  await pc.setLocalDescription(local);
  socket.send(JSON.stringify({ t: "answer", sdp: local.sdp }));

  // A video section answered with port 0 is a refusal, and it is worth saying
  // so out loud: everything else works -- the connection, the controller, the
  // sound -- and the screen simply stays black with nothing to suggest why.
  // The usual cause is a browser built without H.264.
  if (/^m=video 0[ ]/m.test(local.sdp)) {
    clearMediaTimeout();
    videoRefused();
  }
}

/* Autoplay with sound needs a user gesture. Joining is one, so the ordinary
 * path unmutes straight away -- but a guest who *resumes* never clicked
 * anything, and the browser is right to refuse. Rather than leave them
 * wondering why it is silent, ask. */
function startPlayback() {
  video.volume = savedVolume();
  video.play().then(() => {
    video.muted = savedVolume() === 0;
    return video.play();
  }).then(() => {
    el("unmute").hidden = true;
  }).catch(() => {
    video.muted = true;
    video.play().catch(() => {});
    chaseSound();
  });
}

/* ---- sound that comes back by itself ----
 *
 * The browser's rule is that a gesture must have happened, not that it must
 * have been aimed at the sound. A guest who resumes a session has made none
 * yet, so the picture arrives silent through no choice of theirs -- but they
 * joined to play, so there is always something next: a pad button, a tap on
 * the picture, a key. Take that, whatever it was, and ask again.
 *
 * Volume at zero is never touched by any of this. That is somebody turning
 * their own sound off on purpose, and turning it back on for them would be
 * the rudest thing this could do. Only sound the browser silenced is chased.
 */
const SOUND_GESTURES = ["pointerdown", "pointerup", "touchend", "keydown"];
let chasingSound = false;

function soundWanted() {
  return savedVolume() > 0;
}

/* Unmute, and say whether it took. Called straight out of a gesture handler
   so that play() still counts as user-driven -- anything deferred, even by a
   promise tick, is not a gesture any more as far as the browser cares. */
function bringSoundBack() {
  if (!soundWanted()) return Promise.resolve(false);
  if (!video.volume) video.volume = savedVolume();
  video.muted = false;
  let attempt;
  try { attempt = video.play(); } catch (_) { attempt = null; }
  return Promise.resolve(attempt).then(() => {
    if (video.muted) return false;
    el("unmute").hidden = true;
    return true;
  }).catch(() => {
    video.muted = true;                  // a muted picture beats a frozen one
    video.play().catch(() => {});
    return false;
  });
}

function chaseSound() {
  if (chasingSound || !soundWanted()) return;
  chasingSound = true;

  const stop = () => {
    chasingSound = false;
    SOUND_GESTURES.forEach((kind) =>
      document.removeEventListener(kind, go, true));
  };
  // Capture-phase and passive: this watches the gesture, it never takes it.
  // A press on the on-screen pad has to reach the pad.
  const go = () => {
    bringSoundBack().then((won) => {
      if (won) { stop(); return; }
      // A gesture happened and the sound still did not come back, so waiting
      // for another one is not going to help. This is where the button earns
      // its place -- and only here, which is why it is usually never seen.
      stop();
      el("unmute").hidden = false;
      showHud(true);
    });
  };
  SOUND_GESTURES.forEach((kind) =>
    document.addEventListener(kind, go, { capture: true, passive: true }));
}

el("unmute").addEventListener("click", () => {
  if (volume() === 0) setVolume(1);        // silent slider, silent tap, silence
  video.muted = false;
  video.play().then(() => {
    el("unmute").hidden = true;
    hideHud();
  }).catch(() => {});
});

/* ---- volume ----
 *
 * This guest's own loudness, not the television's. Turning it down here is a
 * person turning their phone down, and it would be a nasty surprise if it
 * silenced the room everybody else is playing in.
 *
 * The icon carries the level -- crossed out, one wave, two -- so the common
 * case needs no slider at all: you can see whether the sound is on without
 * touching anything. */
const VOLUME_KEY = "fp:volume";

function volume() {
  const raw = parseInt(el("vol-range").value, 10);
  return isNaN(raw) ? 1 : raw / 100;
}

function paintVolume(level) {
  const box = el("vol");
  box.classList.toggle("is-off", level === 0);
  box.classList.toggle("is-low", level > 0 && level < 0.55);
  box.classList.toggle("is-high", level >= 0.55);
  el("vol-range").style.setProperty("--fill", Math.round(level * 100) + "%");
  const said = level === 0 ? "Sound off" : "Volume " + Math.round(level * 100) + "%";
  el("vol-btn").title = said;
  el("vol-btn").setAttribute("aria-label", said);
}

function setVolume(level, remember = true) {
  level = Math.max(0, Math.min(1, level));
  el("vol-range").value = String(Math.round(level * 100));
  video.volume = level;
  // Muted and zero are the same thing to a listener, and keeping them the same
  // thing here means the icon never disagrees with what is coming out.
  video.muted = level === 0;
  if (level > 0) {
    video.play().catch(() => {});          // raising it is a gesture in itself
    el("unmute").hidden = true;
  }
  paintVolume(level);
  if (remember) {
    try { localStorage.setItem(VOLUME_KEY, String(level)); } catch (_) {}
  }
}

function savedVolume() {
  let raw = null;
  try { raw = localStorage.getItem(VOLUME_KEY); } catch (_) {}
  const level = parseFloat(raw);
  return isNaN(level) ? 1 : Math.max(0, Math.min(1, level));
}

el("vol-btn").addEventListener("click", () => {
  const open = el("vol").classList.toggle("open");
  el("vol-btn").setAttribute("aria-expanded", open ? "true" : "false");
  if (open) el("vol-range").focus({ preventScroll: true });
});

el("vol-range").addEventListener("input", () => setVolume(volume()));

/* Anywhere else closes it. Without this the slider sits open over the picture
   for the rest of the session, because nothing else was ever going to. */
document.addEventListener("pointerdown", (ev) => {
  if (!el("vol").classList.contains("open")) return;
  if (el("vol").contains(ev.target)) return;
  el("vol").classList.remove("open");
  el("vol-btn").setAttribute("aria-expanded", "false");
}, true);

setVolume(savedVolume(), false);

/* ---- on-screen controller ----
 *
 * Which buttons exist and what they send is data, so another pad is a new
 * entry rather than new code. The Mega Drive's three face buttons run left to
 * right; the standard mapping's diamond reads west, south, east across its
 * lower half, which is the arrangement that keeps A B C under a thumb in the
 * order the labels promise. Positions are percentages inside the face area.
 */
/* Controllers, as data.
 *
 * Positions in `face` are percentages inside the face box, which is why its
 * aspect ratio is stated: with buttons 30% of the width across, the lowest one
 * sits at 50% of the height, so a box wider than about 1.5:1 pushes them off
 * the bottom.
 *
 * `button` is always the W3C standard-mapping index, never the printed label.
 * Those disagree, and disagreeing is the point: the standard mapping is
 * Xbox-shaped -- 0 bottom, 1 right, 2 left, 3 top -- while a Nintendo pad
 * prints B on the bottom and A on the right. Naming the position and the label
 * separately is what lets each pad look like itself and still send what the
 * host expects.
 */
const LAYOUTS = {
  genesis: {
    name: "Mega Drive",
    faceAspect: 1.55,
    face: [
      { id: "A", button: 2, x: 0, y: 50 },
      { id: "B", button: 0, x: 34, y: 27.5 },
      { id: "C", button: 1, x: 68, y: 5 },
    ],
    centre: [{ id: "START", button: 9 }],
  },

  /* The same four buttons, labelled the way the wire labels them.
   *
   * Everything on this pad is sent by *position*: the top button sends the
   * standard mapping's north, the right sends east, and so on. That is
   * correct and it is not the whole story, because the host presents an Xbox
   * pad -- so a game reads east as B. On the Nintendo diamond, east is
   * printed A. Press the button marked A and the game is told B, which is
   * exactly the complaint: "it thinks A is B, and X is Y".
   *
   * This layout puts the letters where that game expects them. Nothing about
   * what is sent changes; only what is printed on the key. A guest playing
   * something that names its buttons picks this and reads its prompts
   * straight; a guest playing a Super Nintendo game picks the other and reads
   * the box art straight. Neither is a fix for the other, which is why both
   * are here. */
  xbox: {
    name: "Xbox",
    faceAspect: 1,
    face: [
      { id: "Y", button: 3, x: 35, y: 2 },
      { id: "X", button: 2, x: 2, y: 35 },
      { id: "B", button: 1, x: 68, y: 35 },
      { id: "A", button: 0, x: 35, y: 68 },
    ],
    shoulders: [
      { id: "LT", button: 6, side: "left", row: 0 },
      { id: "RT", button: 7, side: "right", row: 0 },
      { id: "LB", button: 4, side: "left", row: 1 },
      { id: "RB", button: 5, side: "right", row: 1 },
    ],
    centre: [
      { id: "BACK", button: 8 },
      { id: "START", button: 9 },
    ],
  },

  nintendo: {
    name: "Super Nintendo",
    faceAspect: 1,
    // The diamond, by position: X on top, A right, B bottom, Y left. Each
    // sends the standard-mapping index for the place it occupies, so the
    // letters a guest sees match the buttons a game receives.
    face: [
      { id: "X", button: 3, x: 35, y: 2 },
      { id: "Y", button: 2, x: 2, y: 35 },
      { id: "A", button: 1, x: 68, y: 35 },
      { id: "B", button: 0, x: 35, y: 68 },
    ],
    shoulders: [
      { id: "LT", button: 6, side: "left", row: 0 },
      { id: "RT", button: 7, side: "right", row: 0 },
      { id: "LB", button: 4, side: "left", row: 1 },
      { id: "RB", button: 5, side: "right", row: 1 },
    ],
    centre: [
      { id: "SELECT", button: 8 },
      { id: "START", button: 9 },
    ],
  },
};

/* What a guest gets before they choose anything. The on-screen pad is a phone
   thing, and a phone is where the buttons have to be guessable: the SNES
   diamond is the layout most people can name the buttons of without being
   told, and it has the shoulders and Select that the three-button Mega Drive
   pad simply has nowhere to put. Anybody who has chosen already keeps their
   choice -- this is only the starting point. */
/* The Super Nintendo pad with two thumbsticks added, for games that want an
   analogue stick and a diamond of buttons at once. Everything but the sticks
   is shared with the layout above rather than copied, so a change to the
   diamond cannot land on one pad and not the other. */
LAYOUTS.nintendo_sticks = {
  name: "Super Nintendo + sticks",
  faceAspect: 1,
  // The same diamond as the Super Nintendo pad, because that is the layout
  // most people can name the buttons of without being told.
  face: LAYOUTS.nintendo.face,
  shoulders: LAYOUTS.nintendo.shoulders,
  centre: LAYOUTS.nintendo.centre,
  // Standard mapping: axes 0 and 1 are the left stick, 2 and 3 the right.
  sticks: [
    { id: "stick-left", axes: [0, 1] },
    { id: "stick-right", axes: [2, 3] },
  ],
};

/* And the same again for the Xbox letters, because a guest who picked those
   picked them to play something that names its buttons -- and the things that
   name their buttons are the ones that want two sticks as well. Shared from
   the layout above for the same reason: one diamond, not two that drift. */
LAYOUTS.xbox_sticks = {
  name: "Xbox + sticks",
  faceAspect: 1,
  face: LAYOUTS.xbox.face,
  shoulders: LAYOUTS.xbox.shoulders,
  centre: LAYOUTS.xbox.centre,
  sticks: LAYOUTS.nintendo_sticks.sticks,
};

const DEFAULT_LAYOUT = "nintendo";

/* Trading the two pairs of face buttons over, for the guest who would rather
 * keep the letters where their hand expects them.
 *
 * A different question from picking the Xbox layout, and both are worth
 * having. That one moves the *letters* to where the wire puts them, so a game
 * saying "press A" names the button under your thumb. This moves what is
 * *sent*, so the button marked A sends what a game will call A -- which is
 * what somebody wants when the letters on the glass should match the letters
 * in a Super Nintendo game's own menus.
 *
 * Only the diamond. Shoulders, select and start have one name each and
 * nothing to trade with.
 */
const FACE_SWAP = { 0: 1, 1: 0, 2: 3, 3: 2 };
const FACESWAP_KEY = "fp:faceswap";

function faceSwapped() {
  try { return localStorage.getItem(FACESWAP_KEY) === "1"; } catch (_) { return false; }
}

function sentAs(button) {
  return faceSwapped() && button in FACE_SWAP ? FACE_SWAP[button] : button;
}
const LAYOUT_KEY = "fp:layout";

const DPAD = { up: 12, down: 13, left: 14, right: 15 };

/* A short buzz under a thumb that pressed glass.
 *
 * Glass gives nothing back: a finger on a physical button knows it went down
 * before the game shows anything, and a finger on a picture of a button does
 * not. Eight milliseconds is enough to feel and too short to hear, which
 * matters when five people are in one room and only one of them is holding
 * the phone.
 *
 * It is a preference because it is genuinely a matter of taste and of
 * battery, and because a buzz on every d-pad direction is a lot of buzzing
 * for somebody playing a game that holds a direction for minutes. On by
 * default, since that is what this did before there was a switch.
 *
 * Only the on-screen pad buzzes: a physical controller has its own rumble and
 * a keyboard has its own keys, so the switch is out of the way in both cases.
 * What it is *not* hidden by is the browser being unable to vibrate. That was
 * how this first went out and it was exactly backwards -- the one phone that
 * needs the switch most is the one where the plain call does nothing, and
 * somebody went looking for the option and found an empty row. */
/* Which way round the screen sits, and whose decision that is.
 *
 * It was the manifest's: `"orientation": "landscape"`, which an installed
 * Android app obeys absolutely -- rotate the phone and nothing happens, turn
 * off the system rotation lock and nothing happens. iOS ignores manifest
 * orientation entirely, which is why the same file produced a phone that
 * turned and a phone that would not, and why this only ever looked like an
 * Android fault.
 *
 * The manifest now says "any" and the choice is made here, where somebody can
 * change their mind. Three answers and no fourth: follow the phone, which is
 * what a page does when nobody interferes; or hold it one way, which is worth
 * having on a device whose own rotation lock is off but which keeps turning
 * itself over on a sofa.
 *
 * The lock only exists where a browser implements it -- Chrome does, Safari
 * has never -- and even there it is refused outside an installed app. Both of
 * those are said out loud rather than left as a control that does nothing. */
const ORIENT_KEY = "fp:orient";
const ORIENTATIONS = ["any", "landscape", "portrait"];

function canTurn() {
  return !!(window.screen && screen.orientation && screen.orientation.lock);
}

function savedOrient() {
  let raw = null;
  try { raw = localStorage.getItem(ORIENT_KEY); } catch (_) {}
  return ORIENTATIONS.indexOf(raw) > 0 ? raw : "any";
}

/* Apply the choice, and say what happened if it was refused.
 *
 * A refusal is the ordinary case in a browser tab: Chrome only allows the
 * lock for an installed app or a fullscreen page, and rejects with a promise
 * nobody would notice going unhandled. The note is the difference between a
 * control that is broken and a control that is explaining itself. */
function applyOrient(pick) {
  const note = el("pads-orient-note");
  const say = (text) => { if (note) note.textContent = text; };
  if (!canTurn()) return say("");
  /* "any" is a lock, not the absence of one.
   *
   * unlock() was the obvious call and it is the wrong one: it drops back to
   * the *default*, and for an installed app the default is whatever the
   * manifest said when the app was installed. An app added to a home screen
   * while the manifest still asked for landscape therefore went straight back
   * to landscape on being told to follow the phone -- which is exactly what
   * it did, while an explicit portrait worked. Chrome refreshes an installed
   * manifest in its own time, and "reinstall the app" is not an answer to
   * give somebody about a setting.
   *
   * lock("any") is a real lock whose value is every orientation, so it
   * overrides the manifest instead of deferring to it, and the phone's own
   * rotation decides. The unlock stays as a fallback for a browser that will
   * not take "any" as a lock value. */
  let held;
  try {
    held = screen.orientation.lock(pick);
  } catch (exc) {
    if (pick !== "any") return say("This browser will not turn the screen.");
    try { screen.orientation.unlock(); } catch (_) {}
    return say("Turns with the phone, and with its rotation lock.");
  }
  if (pick === "any") {
    if (held && held.catch) {
      held.catch(() => {
        try { screen.orientation.unlock(); } catch (_) {}
      });
    }
    return say("Turns with the phone, and with its rotation lock.");
  }
  say("Held in " + pick + ".");
  if (held && held.catch) {
    held.catch(() => {
      // Kept rather than reverted: the choice is right and the tab is what is
      // wrong, and it will be obeyed the moment the app is opened from the
      // home screen.
      say("Only the installed app can be held " + pick
          + " -- add it to your home screen.");
    });
  }
}

function paintOrient() {
  const row = el("orient-row");
  if (row) row.hidden = !canTurn();
  const picker = el("pads-orient");
  if (picker) picker.value = savedOrient();
}

/* How long the browser took to give us a touch it had already had.
 *
 * event.timeStamp is when the touch happened; performance.now() at the top of
 * the handler is when this page heard about it. The gap is the browser's,
 * not ours -- a main thread busy decoding video, or a compositor deciding
 * whether a finger is a scroll -- and no amount of moving our own work
 * earlier shortens it. Worth measuring because "the buzz feels late" has two
 * quite different causes and they are not tellable apart by feel.
 *
 * Reported once every few dozen presses, not per press: this is a diagnosis,
 * not a stream. */
const LAG_EVERY = 40;
let lagSeen = [];

function noteTouchLag(event) {
  const lag = performance.now() - event.timeStamp;
  // Some browsers stamp events on a different clock; a nonsense figure is
  // worse than none.
  if (!(lag >= 0 && lag < 2000)) return;
  lagSeen.push(lag);
  if (lagSeen.length < LAG_EVERY) return;
  const sorted = lagSeen.slice().sort((a, b) => a - b);
  const median = sorted[Math.floor(sorted.length / 2)];
  report("touch to page: median " + median.toFixed(1) + " ms, worst "
         + sorted[sorted.length - 1].toFixed(1) + " ms over " + sorted.length
         + " presses (buzz " + (canVibrate ? BUZZ_MS[hapticStrength] + " ms"
                                           : SWITCH_TAPS[hapticStrength] + " taps")
         + ")");
  lagSeen = [];
}

const HAPTICS_KEY = "fp:haptics";
const STRENGTH_KEY = "fp:haptics-strength";

/* How long the buzz is, which on Android is the same question as how hard it
 * feels and how soon.
 *
 * Eight milliseconds was the only length there had ever been. A phone's motor
 * has to spin up and stop again inside the pulse, and at eight there is
 * barely time to do either -- so the tap arrives faint, and a faint tap reads
 * as a late one even when it was sent the instant the finger landed. Longer
 * pulses are firmer and, to a thumb, sooner.
 *
 * Which length is right differs by phone, because the motors do, so it is
 * offered rather than decided here. Medium is the default: the old eight is
 * still there as Light for anyone who preferred it. */
const BUZZ_MS = { light: 8, medium: 20, strong: 35 };
const DEFAULT_STRENGTH = "medium";

// Read once: whether the browser has the call at all cannot change under us,
// and asking on every button press would be a lookup per frame.
const canVibrate = typeof navigator.vibrate === "function";

/* The other way to make a phone tap, for the phone that has no vibrate().
 *
 * Safari has never shipped the vibration API, so every `navigator.vibrate` in
 * a web page is a no-op on an iPhone -- which is most of the phones this is
 * played on, and is why the pad has always felt dead there while feeling
 * right on Android.
 *
 * What iOS does give a web page is the haptic its own switch control makes
 * when it flips. A checkbox with `switch` on it is that control, so there is
 * one off the side of the screen and flipping it is the tap.
 *
 * It has to be flipped by clicking the *label*. WebKit plays the feedback
 * from the label's activation behaviour and not from a click on the input
 * itself, which is the whole difference between this working and this doing
 * nothing -- and doing nothing is what the first go at it did.
 *
 * Two further conditions, neither of them ours to relax: it needs a live user
 * gesture, which is why this is only ever called from inside a pointerdown,
 * and the grant lapses about a second after one. It is a trick and it is
 * Apple's to withdraw -- reportedly withdrawn in iOS 26.5 -- so nothing here
 * depends on it working. Where it does nothing the pad is exactly as silent
 * as it was before there was a switch. */
/* How many switch taps each strength is on a phone whose only haptic is one
 * fixed tap. The length is not ours to set there -- iOS decides what a switch
 * feels like -- so the only dimension left is how many, and two in quick
 * succession do feel firmer than one. Spaced far enough apart to be felt
 * separately and close enough to read as one event.
 *
 * Well inside the second or so that Apple's gesture grant lasts, which is
 * what lets the later ones happen at all. */
const SWITCH_TAPS = { light: 1, medium: 2, strong: 3 };
const SWITCH_GAP_MS = 45;

function tapSwitch() {
  const label = el("haptic-label");
  if (!label) return;
  // The click toggles the box itself; setting .checked here as well would put
  // it back where it started and leave the control unflipped.
  try { label.click(); } catch (_) { /* a tap that does not happen is not an error */ }
}

// Whether there is any way at all to answer a press. The switch is offered on
// the strength of this, and on an iPhone it rests on the trick above.
const canBuzz = canVibrate || !!el("haptic-label");

// Which of the two is doing the work, for the host log: "it does not buzz on
// my phone" is three different faults, and they are not tellable apart from
// here without knowing which path the page took.
const feelPath = canVibrate ? "vibrate()"
               : (el("haptic-label") ? "switch tap" : "no way to");

function savedHaptics() {
  let raw = null;
  try { raw = localStorage.getItem(HAPTICS_KEY); } catch (_) {}
  // Anything other than an explicit "off" is on, so a key this page has never
  // written -- or one it cannot read -- lands on the old behaviour.
  return raw !== "0";
}

let hapticsOn = savedHaptics();

function savedStrength() {
  let raw = null;
  try { raw = localStorage.getItem(STRENGTH_KEY); } catch (_) {}
  return Object.prototype.hasOwnProperty.call(BUZZ_MS, raw)
    ? raw : DEFAULT_STRENGTH;
}

let hapticStrength = savedStrength();

function buzz(ms) {
  if (!hapticsOn) return;
  if (ms === undefined) ms = BUZZ_MS[hapticStrength] || BUZZ_MS[DEFAULT_STRENGTH];
  if (canVibrate) {
    // A refusal is normal rather than exceptional: a browser ignores vibrate
    // until the page has been touched, and throws in a few of them.
    try { navigator.vibrate(ms); } catch (_) {}
    return;
  }
  // The iPhone has one length, whatever the switch makes, so strength is a
  // count rather than a duration.
  const times = SWITCH_TAPS[hapticStrength] || 1;
  tapSwitch();
  for (let i = 1; i < times; i++) setTimeout(tapSwitch, i * SWITCH_GAP_MS);
}

/* The switch, and the places its state shows. The panel only carries the row
   while the on-screen pad is the controls, which is what the class on the
   panel says -- the same trick `keys` plays for the keyboard.

   The checkbox is the state rather than a picture of it: the stylesheet draws
   the track and the knob off :checked, so there is no class here to be kept
   in step with what the box actually says. */
function paintBuzz() {
  const panel = el("pads");
  // Whether the pad is on the screen, rather than whether it was asked for:
  // "off" and the keyboard both leave touchOn true behind them, and a buzz
  // switch above a pad that is not there is a switch for nothing.
  const showing = canBuzz && !el("touch").hidden;
  /* `onscreen`, not `touch`. The panel carries this to mean "the controls are
     the on-screen pad", and `touch` is the class of the on-screen pad itself
     -- which in landscape is `pointer-events: none`, because the pad is a
     transparent sheet over the picture with only its clusters taking taps.
     Marking the panel with it handed the panel that rule: sideways, every tap
     went through it to the pad underneath, and nothing on it could be pressed
     or scrolled. A marker class has to be a name nothing else answers to. */
  if (panel) panel.classList.toggle("onscreen", showing);
  const box = el("pads-buzz");
  if (!box) return;
  box.checked = hapticsOn;
  // No sentence under it. "Haptic feedback", on or off, is the whole of what
  // there is to know, and a paragraph explaining a switch somebody has just
  // read the label of is a paragraph in the way.
}

function paintFaceSwap() {
  const box = el("pads-faceswap");
  if (!box) return;
  const on = faceSwapped();
  box.checked = on;
  // "Swap A/B and X/Y" is the whole of it. What it used to say underneath --
  // which button ends up being sent as which -- is the thing somebody finds
  // out by flipping it and pressing one, in less time than the sentence took
  // to read.
}

function setFaceSwap(on) {
  try {
    if (on) localStorage.setItem(FACESWAP_KEY, "1");
    else localStorage.removeItem(FACESWAP_KEY);
  } catch (_) {}
  // The buttons carry the mapping, so the pad is built again rather than
  // patched: one place decides what a key sends, and it is buildTouchPad.
  const key = chosenLayout();
  if (touchOn && LAYOUTS[key]) buildTouchPad(LAYOUTS[key]);
  paintFaceSwap();
}

function paintStrength() {
  const row = el("buzz-strength-row");
  const picker = el("pads-buzz-strength");
  if (!row || !picker) return;
  picker.value = hapticStrength;
  // Offered wherever there is any feedback at all to make stronger. What
  // changes differs: a phone that can be told to vibrate gets a longer pulse,
  // and one whose only haptic is a switch gets more taps, because the length
  // of those is iOS's to decide and not ours.
  row.hidden = !canBuzz || !hapticsOn;
  // Light, Medium and Strong say it. Choosing one buzzes at that strength,
  // which is a better explanation than a sentence about motors.
}

function setStrength(which) {
  if (!Object.prototype.hasOwnProperty.call(BUZZ_MS, which)) return;
  hapticStrength = which;
  try { localStorage.setItem(STRENGTH_KEY, which); } catch (_) {}
  paintStrength();
  // Answer the choice with the thing it chooses: picking a strength should
  // feel like that strength, not be read about.
  buzz();
}

function setHaptics(on) {
  hapticsOn = !!on;
  try {
    if (hapticsOn) localStorage.removeItem(HAPTICS_KEY);
    else localStorage.setItem(HAPTICS_KEY, "0");
  } catch (_) {}
  paintBuzz();
  paintStrength();          // the strength row belongs to the switch above it
  // Answer the switch with the thing it switches, so turning it on is its own
  // demonstration -- and on a phone where none of this works, the silence is
  // the honest answer to "does it work on mine?".
  if (hapticsOn) buzz(30);
}

let touchOn = false;
let touchButtons = 0;
/* A keyboard, as a set of buttons held. The same shape as the on-screen pad's
 * mask and merged the same way on the way out, so a key, a thumb and a
 * controller can all be pressed at once and none of them cancels the others.
 *
 * What this is not is a keyboard reaching the host. The wire carries one
 * thing, a pad frame, and the device each guest is wired to on the other end
 * declares gamepad capabilities and nothing else -- it cannot express a
 * keystroke however it is asked to. This is a guest deciding which of their
 * own keys stands for which button, in their own browser, before any of that. */
let keyButtons = 0;
let keyboardOn = false;
const pointers = new Map();

/* A button on the glass, and -- for the phone that cannot be told to vibrate
   -- the control that makes it tap.
 *
 * It is a <label> around a hidden `<input type="checkbox" switch>` rather than
 * a <button>, because a finger landing on a label activates the switch inside
 * it, and iOS plays its own haptic when a switch flips. That is the only
 * feedback left to a web page on a current iPhone: Safari has no vibration
 * API, and the programmatic version of this -- clicking the label from script
 * -- is what Apple closed in iOS 26.5. A real finger on a real control is
 * still a real switch being used, which is the thing Apple did not close.
 *
 * The <input> cannot go inside a <button>: interactive content nested in a
 * button is invalid, and the tap does not reach it. A label costs nothing
 * visually -- .tbtn states its own display, border, background and font, so
 * none of it came from the button element -- and keeps role and name for a
 * screen reader.
 *
 * Everything else about the press is unchanged and unchanged on purpose: the
 * bit is still set on pointerdown, so the game hears the press when the
 * finger lands, not when the switch flips under it. */
function makeButton(spec, className) {
  const button = document.createElement("label");
  button.className = className;
  button.setAttribute("role", "button");
  button.dataset.button = String(spec.button);
  const cap = document.createElement("span");
  cap.className = "tbtn-cap";
  cap.textContent = spec.id;
  button.appendChild(cap);
  const tap = document.createElement("input");
  tap.type = "checkbox";
  // Not a property: `switch` is an attribute Safari reads and every other
  // browser ignores, and there is no IDL for it to be set through.
  tap.setAttribute("switch", "");
  tap.className = "tbtn-tap";
  tap.tabIndex = -1;
  tap.setAttribute("aria-hidden", "true");
  button.appendChild(tap);
  return button;
}

function buildTouchPad(layout) {
  releaseAllTouch();          // never carry a held button across a rebuild

  const face = el("face");
  face.innerHTML = "";
  face.style.aspectRatio = String(layout.faceAspect || 1.55);
  for (const spec of layout.face) {
    const button = makeButton({ ...spec, button: sentAs(spec.button) },
                              "tbtn tbtn-face");
    button.style.left = spec.x + "%";
    button.style.top = spec.y + "%";
    face.appendChild(button);
  }

  for (const side of ["left", "right"]) {
    const box = el("shoulders-" + side);
    box.innerHTML = "";
    for (const spec of (layout.shoulders || []).filter((b) => b.side === side)) {
      box.appendChild(makeButton(spec, "tbtn tbtn-shoulder row" + (spec.row || 0)));
    }
    box.hidden = box.children.length === 0;
  }

  const centre = el("centre");
  centre.innerHTML = "";
  for (const spec of layout.centre || []) {
    centre.appendChild(makeButton(spec, "tbtn tbtn-start"));
  }

  const sticks = layout.sticks || [];
  el("touch").classList.toggle("has-sticks", sticks.length > 0);
  for (const id of ["stick-left", "stick-right"]) {
    const well = el(id);
    const spec = sticks.find((k) => k.id === id);
    well.hidden = !spec;
    well.dataset.axes = spec ? spec.axes.join(",") : "";
    centreKnob(well);
  }

}

/* ---- on-screen sticks ----
 *
 * A thumb on glass has no spring and no centre, so the two things a real stick
 * gives for free both have to be built: the knob follows the thumb only as far
 * as the edge of the well, and it snaps back to the middle the moment the
 * thumb leaves. Anything else and the character keeps walking after you let
 * go, which is the one failure people do not forgive.
 */
let touchAxes = [0, 0, 0, 0];
const stickHeld = {};                    // pointer id -> the well being dragged

function centreKnob(well) {
  const knob = well.querySelector(".stick-knob");
  if (knob) knob.style.transform = "translate(-50%, -50%)";
}

function stickAxesOf(well) {
  return (well.dataset.axes || "").split(",").filter((n) => n !== "")
    .map(Number);
}

function moveStick(well, event) {
  const rect = well.getBoundingClientRect();
  const radius = rect.width / 2;
  if (!radius) return;
  let x = (event.clientX - (rect.left + radius)) / radius;
  let y = (event.clientY - (rect.top + radius)) / radius;
  // Clamped to the circle, not the square: a thumb in the corner of the well
  // would otherwise read as 1.41 of tilt, which is past what a stick can do.
  const reach = Math.hypot(x, y);
  if (reach > 1) { x /= reach; y /= reach; }
  const [ax, ay] = stickAxesOf(well);
  if (ax !== undefined) touchAxes[ax] = x;
  if (ay !== undefined) touchAxes[ay] = y;
  const knob = well.querySelector(".stick-knob");
  if (knob) {
    knob.style.transform =
      "translate(calc(-50% + " + (x * radius * 0.62).toFixed(1) + "px), "
      + "calc(-50% + " + (y * radius * 0.62).toFixed(1) + "px))";
  }
}

function releaseStick(well) {
  for (const axis of stickAxesOf(well)) touchAxes[axis] = 0;
  well.classList.remove("live");
  centreKnob(well);
}

function releaseAllSticks() {
  touchAxes = [0, 0, 0, 0];
  for (const id of ["stick-left", "stick-right"]) {
    const well = el(id);
    well.classList.remove("live");
    centreKnob(well);
  }
  for (const key of Object.keys(stickHeld)) delete stickHeld[key];
}

function wireSticks() {
  for (const id of ["stick-left", "stick-right"]) {
    const well = el(id);
    well.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      well.setPointerCapture(event.pointerId);
      buzz();                       // before the work, for the same reason
      stickHeld[event.pointerId] = well;
      well.classList.add("live");
      moveStick(well, event);
    });
    well.addEventListener("pointermove", (event) => {
      if (stickHeld[event.pointerId] !== well) return;
      event.preventDefault();
      moveStick(well, event);
    });
    const letGo = (event) => {
      if (stickHeld[event.pointerId] !== well) return;
      delete stickHeld[event.pointerId];
      releaseStick(well);
    };
    well.addEventListener("pointerup", letGo);
    well.addEventListener("pointercancel", letGo);
  }
}

function chosenLayout() {
  let saved = null;
  try { saved = localStorage.getItem(LAYOUT_KEY); } catch (_) {}
  return (saved === "off" || saved === "keyboard" || LAYOUTS[saved])
    ? saved : DEFAULT_LAYOUT;
}

/* The panel's copy of the chip's controller menu.
 *
 * Two selects, one choice. Whichever is used, the other has to follow, or the
 * panel says "no on-screen pad" while the buttons are plainly on the screen.
 * Kept in step by writing through the same function both call. */
function mirrorPicker() {
  const chip = el("padtype"), panel = el("pads-type");
  if (!panel) return;
  if (panel.options.length !== chip.options.length
      || panel.dataset.names !== chipOptionNames()) {
    panel.dataset.names = chipOptionNames();
    panel.innerHTML = "";
    for (const option of chip.options) {
      const copy = document.createElement("option");
      copy.value = option.value;
      copy.textContent = option.textContent;
      panel.appendChild(copy);
    }
  }
  panel.value = chip.value;
  paintAttached();
}

function chipOptionNames() {
  return Array.from(el("padtype").options).map((o) => o.textContent).join("|");
}

/* Which real controllers the browser can see.
 *
 * Worth saying out loud in the panel: a pad that is plugged in but has not
 * been touched is invisible to the page -- browsers withhold it until a button
 * is pressed -- so "none detected" and "none plugged in" are different states,
 * and somebody staring at a connected controller deserves to be told which
 * one they are in. */
function paintAttached() {
  const where = el("pads-attached");
  if (!where) return;
  const pads = navigator.getGamepads ? navigator.getGamepads() : [];
  const names = Array.from(pads).filter((p) => p && p.connected)
                     .map((p) => shortPadName(p.id));
  if (names.length) {
    where.textContent = names.length === 1
      ? "Controller found: " + names[0]
      : "Controllers found: " + names.join(", ");
  } else {
    where.textContent = "No controller detected \u2014 press a button on one "
                      + "if it is plugged in, or use the on-screen pad.";
  }
}

function buildLayoutPicker() {
  const picker = el("padtype");
  if (picker.options.length) return;          // built once
  // "Off" first, because somebody with a real controller in their hands wants
  // the buttons out of the way more than they want a different set of them.
  const none = document.createElement("option");
  none.value = "off";
  none.textContent = "No on-screen pad";
  picker.appendChild(none);
  // Beside "off" rather than among the layouts below it: those are shapes of
  // the same on-screen pad, and this is a different thing to press.
  const keys = document.createElement("option");
  keys.value = "keyboard";
  keys.textContent = "Keyboard";
  picker.appendChild(keys);
  for (const [key, layout] of Object.entries(LAYOUTS)) {
    const option = document.createElement("option");
    option.value = key;
    option.textContent = layout.name;
    picker.appendChild(option);
  }
  // "off" is not an absence here -- paintPicker renames it to the physical
  // controller -- so the value has to say which controller is actually in use.
  // Claiming a layout while no on-screen pad is showing, which is the ordinary
  // state on a desktop, would be the dropdown disagreeing with the screen.
  const remembered = chosenLayout();
  picker.value = remembered === "keyboard" ? "keyboard"
               : (touchOn ? remembered : "off");
  if (picker.value === "keyboard") keyboardOn = true;
  paintPicker();
  const choose = (value) => {
    try { localStorage.setItem(LAYOUT_KEY, value); } catch (_) {}
    chosenByHand = true;              // stop guessing for them from here on
    picker.value = value;
    applyLayoutChoice(value);
  };
  picker.addEventListener("change", () => choose(picker.value));
  const panel = el("pads-type");
  if (panel) panel.addEventListener("change", () => choose(panel.value));
  mirrorPicker();
}

let chosenByHand = false;
let padName = "";

function applyLayoutChoice(key) {
  keyboardOn = key === "keyboard";
  // Whatever was held on the way out of keyboard mode is let go of here, or it
  // stays held on somebody else's television.
  if (!keyboardOn) keyButtons = 0;
  if (key === "off" || keyboardOn) {
    el("touch").hidden = true;
    releaseAllTouch();
  } else {
    el("touch").hidden = false;
    buildTouchPad(LAYOUTS[key] || LAYOUTS[DEFAULT_LAYOUT]);
  }
  paintPicker();
  paintKeyMode();
  paintBuzz();
  paintStrength();
}

/* One control, which is also the readout.
 *
 * There were two chips saying the same thing: a status one that claimed
 * "On-screen pad" and a menu that decided which. What is switched on is
 * evident from the buttons being on the screen, so the menu can say what is in
 * charge and be the only thing to touch.
 */
function paintPicker() {
  const picker = el("padtype");
  const off = picker.querySelector('option[value="off"]');
  if (off) {
    // With a controller plugged in, the "off" choice is not an absence of
    // anything -- it is that controller, so it says so.
    off.textContent = padName || "No on-screen pad";
  }
  const usingKeys = picker.value === "keyboard";
  const usingTouch = picker.value !== "off" && !usingKeys;
  // The colour goes on the chip around the icon, not on the select -- which
  // now carries only the classes that make it invisible, and would lose them
  // if this wrote over className the way it used to.
  const chip = el("padpick");
  chip.className = "padpick chip "
    + (usingTouch || usingKeys || padName ? "ok" : "warn");
  const said = usingKeys
    ? "Keyboard — tap to change, or to see which key is which"
    : usingTouch
    ? "On-screen controller — tap to change or turn off"
    : (padName ? padName + " — tap to add an on-screen pad"
               : "No controller — tap to add an on-screen pad");
  chip.title = said;
  picker.setAttribute("aria-label", said);
  mirrorPicker();
}

function setBit(bit, down) {
  const before = touchButtons;
  if (down) touchButtons |= (1 << bit);
  else touchButtons &= ~(1 << bit);
  return touchButtons !== before;
}

function dpadDirections(event) {
  const rect = el("dpad").getBoundingClientRect();
  return FPFrame.direction(
    (event.clientX - (rect.left + rect.width / 2)) / (rect.width / 2),
    (event.clientY - (rect.top + rect.height / 2)) / (rect.height / 2));
}

function paintDpad(live) {
  for (const arm of el("dpad").querySelectorAll(".dpad-arm")) {
    arm.classList.toggle("live", live.includes(arm.dataset.dir));
  }
}

/* Which arms were live at the last look. A thumb sliding across the pad from
   left to up-left to up is three directions and should feel like three, while
   a thumb held on one arm is one press and must not buzz 125 times a second
   for as long as it is held -- so the buzz follows the change, not the touch. */
let dpadLive = "";

function applyDpad(event) {
  const live = dpadDirections(event);
  for (const [name, bit] of Object.entries(DPAD)) setBit(bit, live.includes(name));
  const now = live.join(",");
  if (now !== dpadLive) {
    dpadLive = now;
    if (now) buzz();
    if (now) pressedAt = (event && event.timeStamp) || 0;
    sendNow();
  }
  // Light the arm being pressed, not the middle: a diagonal lights two, which
  // is also the clearest way to see that diagonals work at all.
  paintDpad(live);
}

function clearDpad() {
  for (const bit of Object.values(DPAD)) setBit(bit, false);
  dpadLive = "";
  paintDpad([]);
  sendNow();
}

function wireTouch() {
  const pad = el("dpad");

  pad.addEventListener("pointerdown", (event) => {
    // The same bargain the buttons make: where the feeling comes from a switch
    // inside the arm, the browser has to be left to finish the touch it
    // started, so neither the default nor the capture is taken.
    if (!(hapticsOn && !canVibrate)) {
      event.preventDefault();
      pad.setPointerCapture(event.pointerId);
    }
    pointers.set(event.pointerId, "dpad");
    applyDpad(event);
  });
  const movePad = (event) => {
    if (pointers.get(event.pointerId) !== "dpad") return;
    if (event.cancelable) event.preventDefault();
    applyDpad(event);          // sliding across the pad changes direction
  };
  pad.addEventListener("pointermove", movePad);
  /* Without capture, a thumb that slides past the edge of the d-pad stops
     being the d-pad's business and the direction it was holding sticks. The
     window still sees it, and applyDpad works off the pad's own rectangle
     rather than off what was hit, so a thumb outside it reads as the arm it
     is nearest -- which is what capture was doing anyway. */
  window.addEventListener("pointermove", movePad);
  const releasePad = (event) => {
    if (pointers.get(event.pointerId) !== "dpad") return;
    pointers.delete(event.pointerId);
    clearDpad();
  };
  pad.addEventListener("pointerup", releasePad);
  pad.addEventListener("pointercancel", releasePad);
  window.addEventListener("pointerup", releasePad);
  window.addEventListener("pointercancel", releasePad);

  /* The buzz, before anything else this page does with the touch.
   *
   * Its own listener, in the capture phase, so it runs before the handler
   * below and before whatever that does with the event. On Android the whole
   * complaint is that the tap feels late, and the fix for that is not to have
   * anything at all between the finger landing and the phone answering.
   *
   * On a phone whose only haptic is the switch, this is also what makes the
   * feeling arrive on the press rather than on the release. The switch is
   * flipped from script here; the label the finger is actually on activates
   * on release, as it always did, and where the scripted flip is ignored --
   * Apple closed that path in iOS 26.5 -- the release one is still there. So
   * the press is answered where it can be, and where it cannot, nothing is
   * lost that was there before.
   *
   * How long the browser took to hand us the touch is worth knowing on the
   * way past: "it feels late" is either this page being slow or the browser
   * being slow to say anything, and those have different answers. */
  el("touch").addEventListener("pointerdown", (event) => {
    if (!event.target.closest(".tbtn")) return;
    noteTouchLag(event);
    buzz();
  }, { capture: true });

  el("touch").addEventListener("pointerdown", (event) => {
    const button = event.target.closest(".tbtn");
    if (!button) return;
    /* Two ways to press a button, and the difference is whether the browser's
       own handling of the touch is left alone.
     *
       Ordinarily it is not: preventDefault stops the tap highlight, the text
       selection and the callout, and pointer capture keeps the release with
       the button even if the thumb rolls off it.

       On a phone whose only haptic is the switch inside this label, both of
       those are what kills the feeling. Preventing the default of pointerdown
       cancels the click that would activate the switch, and capture retargets
       that click away from the label. So the browser is left to do its own
       thing with this touch, and what is normally prevented is prevented by
       other means instead: `touch-action: none` on the pad stops the scroll
       and the zoom, and `user-select`/`touch-callout` in the stylesheet stop
       the rest. Letting go is covered below, without capture. */
    const bySwitch = hapticsOn && !canVibrate;
    if (!bySwitch) {
      event.preventDefault();
      button.setPointerCapture(event.pointerId);
    }
    // The buzz already happened, in the capture-phase listener above.
    pointers.set(event.pointerId, button);
    button.classList.add("live");
    setBit(Number(button.dataset.button), true);
    pressedAt = event.timeStamp;
    sendNow();
  });
  const releaseButton = (event) => {
    const button = pointers.get(event.pointerId);
    if (!button || button === "dpad") return;
    pointers.delete(event.pointerId);
    button.classList.remove("live");
    setBit(Number(button.dataset.button), false);
    // Letting go is as urgent as pressing: a button released 8 ms late is a
    // jump held 8 ms too long, and in a game that is the same fault.
    sendNow();
  };
  el("touch").addEventListener("pointerup", releaseButton);
  el("touch").addEventListener("pointercancel", releaseButton);
  /* Without pointer capture a thumb that rolls off the pad entirely lifts
     somewhere this listener never hears about, and the button stays held on
     somebody else's television. The window hears every one of them. Running
     twice for a release inside the pad costs nothing: the second one finds
     the pointer already forgotten and returns. */
  window.addEventListener("pointerup", releaseButton);
  window.addEventListener("pointercancel", releaseButton);

  // A finger still down when the page goes away must not leave a button held
  // on somebody else's television.
  window.addEventListener("blur", releaseAllTouch);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") releaseAllTouch();
  });
}

function releaseAllTouch() {
  releaseAllSticks();
  lastSent = null;          // the next frame must go, whatever it says
  touchButtons = 0;
  pointers.clear();
  clearDpad();
  document.querySelectorAll(".tbtn.live").forEach((b) => b.classList.remove("live"));
}

function showTouch(on, layout) {
  touchOn = on;
  paintBuzz();
  paintStrength();
  // The picker stays available whether or not the pad is showing -- otherwise
  // turning it off is a one-way door.
  buildLayoutPicker();
  el("padpick").hidden = false;

  if (!on) {
    el("touch").hidden = true;
    releaseAllTouch();
    paintPicker();
    return;
  }
  const key = layout || chosenLayout();
  el("padtype").value = (key === "off" || LAYOUTS[key]) ? key : DEFAULT_LAYOUT;
  applyLayoutChoice(el("padtype").value);
  el("prompt").hidden = true;
}

el("use-touch").addEventListener("click", (event) => {
  event.preventDefault();
  showTouch(true);
});

/* The same offer for somebody at a desk. A laptop has no touchscreen to put
   buttons on and often no controller either, and this page had nothing to say
   to them at all. */
if (el("use-keys")) {
  el("use-keys").addEventListener("click", (event) => {
    event.preventDefault();
    buildLayoutPicker();
    el("padpick").hidden = false;
    el("padtype").value = "keyboard";
    try { localStorage.setItem(LAYOUT_KEY, "keyboard"); } catch (_) {}
    chosenByHand = true;
    applyLayoutChoice("keyboard");
    el("prompt").hidden = true;
    showNotice("Keyboard controls are on. The arrow keys are the d-pad; "
               + "<strong>Controls</strong> at the top shows every key and "
               + "changes any of them.", false);
  });
}

function videoRefused() {
  setLink("bad", "no H.264");
  showHud(true);
  showNotice(
    "<strong>This browser will not accept the video.</strong>" +
    "<p class=\"footnote\">It refused the H.264 stream, so the picture cannot " +
    "start &mdash; your controller and the sound still work. Safari and Chrome " +
    "handle it; a Firefox without its H.264 plug-in does not.</p>", true);
  report("browser refused the video format");
}

/* Moving between mobile data and wifi replaces every address this device had,
 * so the connection cannot recover on its own -- it can only be rebuilt. The
 * host re-offers on request, and the guest keeps their slot, their pad and
 * their session, so the only visible cost is a second of "reconnecting".
 *
 * Attempts are capped and spaced: if the host is genuinely unreachable this
 * must not turn into a page hammering it forever. */
const MAX_RENEWALS = 6;
let renewals = 0, renewTimer = null;

function clearRenewTimer() {
  if (renewTimer) { clearTimeout(renewTimer); renewTimer = null; }
}

/* Bring the connection back now, from any state it is in.
 *
 * A phone that has been in a pocket for ten minutes comes back to a page whose
 * timers were frozen, whose socket the system closed without telling anybody,
 * and whose RTCPeerConnection may still cheerfully report "connected" over a
 * path that has carried nothing for minutes. Each of the pieces below already
 * knew how to mend itself and none of them could, because every one was
 * waiting on a count that had run out while nobody was looking: the socket
 * backoff had grown to fifteen seconds, the rebuild allowance was spent, and a
 * single refused resume had thrown the credential away.
 *
 * So this is one path rather than four, and it resets the counts as it goes.
 * The socket is rebuilt rather than reused even when it claims to be open,
 * because after a long sleep that claim is worth nothing and the alternative
 * is a `renew` sent into a closed pipe and twenty seconds of waiting to find
 * out. A fresh socket costs one handshake; the resume that follows carries a
 * fresh offer with it. */
let lastRevive = 0;

function reviveNow(why) {
  if (ended) return;
  // A restored page fires pageshow and visibilitychange one after the other,
  // and a second rebuild started on top of the first only throws away a
  // connection that was on its way.
  const now = Date.now();
  if (now - lastRevive < 1000) return;
  lastRevive = now;
  clearRejoinTimer();
  clearRenewTimer();
  clearMediaTimeout();
  if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; }
  retries = 0;
  renewals = 0;
  stalledSince = 0;
  lastBytes = -1;
  mediaFresh = false;
  if (!guestToken) {
    try { guestToken = localStorage.getItem(credKey()) || null; } catch (_) {}
  }
  if (!guestToken) {
    askForPin("This connection could not be brought back.");
    return;
  }
  setLink("warn", "reconnecting");
  const old = socket;
  try {
    if (old && old.readyState === WebSocket.OPEN) {
      old.send(JSON.stringify({
        t: "report",
        detail: "[" + CLIENT_BUILD + "] bringing the connection back: " + why }));
    }
  } catch (_) { /* saying so is never worth failing over */ }
  socket = null;                          // so its close handler stands down
  try { if (old) old.close(); } catch (_) {}
  try {
    // "new" rather than "live": the host keeps a working stream when a guest
    // only lost signalling, and the whole reason for being here is that this
    // one is not working, whatever it says about itself.
    connect({ t: "resume", guest: guestToken, name: myName(),
              codecs: videoCodecs(), media: "new" });
  } catch (_) {
    // Nothing to connect to yet -- no network at all, usually. The backoff
    // was made for this, and it has just been reset to its first step.
    reconnectSoon();
  }
}

function renewSoon(delay, force) {
  if (ended || renewTimer) return;
  if (renewals >= MAX_RENEWALS) {
    mediaFailed("The video connection could not be rebuilt.");
    return;
  }
  renewTimer = setTimeout(() => {
    renewTimer = null;
    // `force` is for a connection that claims to be up and is not carrying
    // anything -- which is what iOS leaves behind after the tab has been in
    // the background. Without it this returns here and nothing is ever
    // rebuilt, so the picture stays frozen under a chip saying "reconnecting".
    if (!pc || (!force && pc.connectionState === "connected")) return;
    renewals += 1;
    setLink("warn");
    if (socket && socket.readyState === 1) {
      socket.send(JSON.stringify({ t: "renew" }));
      armMediaTimeout();
    } else {
      // The socket went with the network. Get it back first; the resume
      // handshake brings a fresh offer with it.
      reconnectSoon();
    }
  }, delay);
}

// The browser tells us when the network comes back, and when the user returns
// to the tab -- both are exactly when a stale connection needs rebuilding.
window.addEventListener("online", () => {
  if (!ended && gate.hidden && !mediaIsLive()) reviveNow("the network came back");
});

/* Back in front of somebody. Whatever counted down while nobody was looking is
 * reset here: the backoff belongs to the time the page was away, and making
 * somebody who has just picked their phone up wait out fifteen seconds of it
 * is the difference between "it mends itself" and "it is broken". */
function cameBack() {
  if (ended || !gate.hidden) return;
  retries = 0;
  const socketOpen = socket && socket.readyState === WebSocket.OPEN;
  if (!socketOpen || !pc || pc.connectionState !== "connected") {
    reviveNow("came back to the page");
    return;
  }
  // Everything still claims to be up. It may even be true, and a short
  // background usually is, so let it prove it before pulling it down.
  stalledSince = 0;
  lastBytes = -1;
  mediaFresh = false;
  setTimeout(watchMedia, 500);
  setTimeout(() => {
    if (!ended && pc && lastBytes < 0) reviveNow("nothing was moving on return");
  }, 3000);
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") {
    // Nothing can be assumed about what survives being in the background.
    mediaFresh = false;
    return;
  }
  cameBack();
});

/* A page restored from the back/forward cache, which is what a phone does with
 * a tab it froze rather than discarded. No visibilitychange fires for it: the
 * page simply resumes, mid-sentence, with a socket the system closed while it
 * slept. */
window.addEventListener("pageshow", (event) => {
  if (event.persisted) cameBack();
});

/* Everything the page has to say goes through here, so it always lands
 * somewhere readable and can be asked for again later. */
let lastNotice = "";

/* The chips are over the picture in landscape, where there is nowhere else for
 * them to be, so they must not stay there. Anything that shows them starts a
 * timer that takes them away again, and a tap on the picture brings them back.
 * They used to be revealed by a message and never hidden, so they simply sat
 * on top of the game. */
const HUD_SECONDS = 5;
let hudTimer = null;

function showHud(persist) {
  // Asking for the chips back is asking to leave the stripped-back view.
  if (immersive) {
    immersive = false;
    stage.classList.remove("immersive");
    el("full").setAttribute("aria-label", "Fullscreen");
  }
  el("hud").classList.add("show");
  labelHudButton();
  if (hudTimer) { clearTimeout(hudTimer); hudTimer = null; }
}

function hideHud() {
  // Not while somebody is using one of them. The timer does not know that a
  // native dropdown is open -- the list is drawn by the operating system and
  // the page is told nothing about it -- but focus stays on the select the
  // whole time it is, so that is the thing to ask.
  if (el("hud").contains(document.activeElement)) {
    if (hudTimer) clearTimeout(hudTimer);
    hudTimer = setTimeout(hideHud, HUD_SECONDS * 1000);
    return;
  }
  // The chips are going away; the slider must not still be open behind them,
  // waiting to reappear next time somebody wants the volume.
  el("vol").classList.remove("open");
  el("vol-btn").setAttribute("aria-expanded", "false");
  if (hudTimer) { clearTimeout(hudTimer); hudTimer = null; }
  // Nothing is hidden while there is something to read.
  if (el("notice").hidden) { el("hud").classList.remove("show"); labelHudButton(); }
  else hudTimer = setTimeout(hideHud, HUD_SECONDS * 1000);
}

function labelHudButton() {
  // The icon says it visually; this says it to a screen reader, which sees an
  // unchanging button otherwise.
  const open = el("hud").classList.contains("show");
  el("hudbtn").setAttribute(
    "aria-label", open ? "Hide the buttons at the top"
                       : "Show the buttons at the top");
  el("hudbtn").setAttribute("aria-expanded", open ? "true" : "false");
}

function toggleHud() {
  if (el("hud").classList.contains("show")) {
    hideNotice();
    el("hud").classList.remove("show");
    labelHudButton();
    if (hudTimer) { clearTimeout(hudTimer); hudTimer = null; }
  } else {
    showHud();
  }
}

/* Tapping the picture asks for the chips, and asks again to dismiss them --
 * but only where there is no button for it. Sideways the controls lie over the
 * picture, so a thumb that slides off the d-pad lands on the video and used to
 * summon the chips in the middle of a game. Where the menu button is showing,
 * that button is the only way in. */
function hudButtonShowing() {
  return getComputedStyle(el("hudbtn")).display !== "none";
}

el("screen").addEventListener("click", () => {
  // A drag that ends over the picture is not a tap on it. Browsers do not
  // agree about whether a click follows a pointer that moved, so this is
  // decided here rather than hoped for.
  if (dragged) { dragged = false; return; }
  if (hudButtonShowing()) return;
  toggleHud();
});

/* ---- zooming the picture ----
 *
 * A guest is watching a television through whatever they have in their hand,
 * and on a phone that is a picture about as wide as two fingers. The part they
 * actually need is often a corner of it -- a health bar, a lap counter, the
 * map in the top right -- and there was no way to get closer to it.
 *
 * The picture is moved rather than the page: a CSS transform on the video
 * element, so nothing else on the screen scales with it. The hud stays the
 * size it was, the on-screen pad stays where the thumbs are, and the game goes
 * on being sent at exactly the same resolution -- this is a magnifying glass
 * held over what has arrived, not a request for a bigger picture.
 *
 * Panning stops at the edges of the *picture*, which is not the edge of the
 * video element: object-fit letterboxes a 16:9 stream inside whatever shape
 * the phone is, and being able to drag off into the black would be a way to
 * lose the game entirely. */
const ZOOM_MIN = 1, ZOOM_MAX = 4;
let zoom = 1, panX = 0, panY = 0, dragged = false;

/* The picture inside the element, in screen pixels, before any zoom. */
function pictureBox() {
  const box = video.getBoundingClientRect();
  const w = video.videoWidth, h = video.videoHeight;
  if (!w || !h || !box.width || !box.height) {
    return { width: box.width, height: box.height, box };
  }
  const fit = Math.min(box.width / w, box.height / h);
  return { width: w * fit, height: h * fit, box };
}

/* How far the picture may be moved along one axis: half of however much it
   overhangs what can be seen of it, and nothing at all when it does not
   overhang -- a picture narrower than the screen it is on has no slack, and
   letting it be dragged anyway would move the game off into the black. */
function panRoom(size, seen, level) {
  return Math.max(0, (size * level - seen) / 2);
}

/* Where the picture has to sit for the point being zoomed towards to stay
   under the fingers doing it. A point sits at `pan + u * level` for some
   fixed u in the picture, so holding it still across a change of level is
   `towards + (pan - towards) * ratio` -- which is the whole of why a pinch
   grows what is between the fingers rather than what is in the middle. */
function panTowards(pan, towards, ratio) {
  return towards + (pan - towards) * ratio;
}

function applyZoom() {
  zoom = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, zoom));
  if (zoom <= ZOOM_MIN + 0.001) {
    zoom = ZOOM_MIN;
    panX = 0;
    panY = 0;
  } else {
    const picture = pictureBox();
    const maxX = panRoom(picture.width, picture.box.width, zoom);
    const maxY = panRoom(picture.height, picture.box.height, zoom);
    panX = Math.max(-maxX, Math.min(maxX, panX));
    panY = Math.max(-maxY, Math.min(maxY, panY));
  }
  video.style.transform = zoom === ZOOM_MIN
    ? "" : "translate(" + panX + "px, " + panY + "px) scale(" + zoom + ")";
  paintZoom();
}

function paintZoom() {
  const range = el("zoom-range");
  if (range) {
    range.value = String(Math.round(zoom * 100));
    range.style.setProperty(
      "--fill",
      Math.round(((zoom - ZOOM_MIN) / (ZOOM_MAX - ZOOM_MIN)) * 100) + "%");
  }
  const group = el("zoom");
  if (group) group.classList.toggle("is-on", zoom > ZOOM_MIN);
  const button = el("zoom-btn");
  if (button) {
    const said = zoom > ZOOM_MIN
      ? "Zoomed " + (Math.round(zoom * 10) / 10) + "\u00d7 \u2014 drag the "
        + "picture to move it, or set this to 1 to fit it again"
      : "Zoom in";
    button.title = said;
    button.setAttribute("aria-label", said);
  }
}

/* Zooming towards a point rather than towards the middle, so what is under two
   fingers -- or under the mouse -- is still under them afterwards. */
function zoomAbout(next, clientX, clientY) {
  const box = video.getBoundingClientRect();
  const towardsX = (clientX == null ? box.left + box.width / 2 : clientX)
                 - (box.left + box.width / 2);
  const towardsY = (clientY == null ? box.top + box.height / 2 : clientY)
                 - (box.top + box.height / 2);
  const to = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, next));
  const ratio = to / zoom;
  panX = panTowards(panX, towardsX, ratio);
  panY = panTowards(panY, towardsY, ratio);
  zoom = to;
  applyZoom();
}

const held = new Map();
let pinchGap = 0, pinchAt = null;

const gapBetween = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
const middleOf = (a, b) => ({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });

video.addEventListener("pointerdown", (event) => {
  if (event.pointerType === "mouse" && event.button !== 0) return;
  held.set(event.pointerId, { x: event.clientX, y: event.clientY });
  if (held.size === 2) {
    const [a, b] = Array.from(held.values());
    pinchGap = gapBetween(a, b);
    pinchAt = middleOf(a, b);
  }
  if (held.size === 1) {
    // A new touch is a new question. Without this, a drag that ended without
    // a click after it -- which is every drag on a touchscreen -- left the
    // flag set and swallowed the next honest tap.
    dragged = false;
    // Captured so a drag that wanders over the hud, or off the screen
    // entirely, keeps moving the picture instead of stopping dead.
    try { video.setPointerCapture(event.pointerId); } catch (_) {}
  }
});

video.addEventListener("pointermove", (event) => {
  const was = held.get(event.pointerId);
  if (!was) return;
  const now = { x: event.clientX, y: event.clientY };
  held.set(event.pointerId, now);
  if (held.size >= 2) {
    const [a, b] = Array.from(held.values());
    const gap = gapBetween(a, b);
    const at = middleOf(a, b);
    if (pinchGap > 0 && gap > 0) {
      zoomAbout(zoom * (gap / pinchGap), at.x, at.y);
      // Two fingers that move together move the picture, which is how
      // somebody keeps hold of what they were looking at while resizing it.
      if (pinchAt) { panX += at.x - pinchAt.x; panY += at.y - pinchAt.y; }
      applyZoom();
    }
    pinchGap = gap;
    pinchAt = at;
    dragged = true;
    event.preventDefault();
    return;
  }
  if (zoom > ZOOM_MIN) {
    panX += now.x - was.x;
    panY += now.y - was.y;
    applyZoom();
    dragged = true;
    event.preventDefault();
  }
});

function letGoOfPicture(event) {
  held.delete(event.pointerId);
  if (held.size < 2) { pinchGap = 0; pinchAt = null; }
  try { video.releasePointerCapture(event.pointerId); } catch (_) {}
}

video.addEventListener("pointerup", letGoOfPicture);
video.addEventListener("pointercancel", letGoOfPicture);

/* A trackpad pinch arrives as a wheel with ctrl held; an ordinary wheel is
   what a mouse has instead. Neither scrolls anything here -- there is nothing
   on this page to scroll -- so both are zoom. */
video.addEventListener("wheel", (event) => {
  if (!gate.hidden) return;
  event.preventDefault();
  const step = Math.exp(-event.deltaY * (event.ctrlKey ? 0.01 : 0.002));
  zoomAbout(zoom * step, event.clientX, event.clientY);
}, { passive: false });

// Back to the whole picture, by the gesture everything else uses for it.
video.addEventListener("dblclick", () => {
  if (zoom > ZOOM_MIN) { zoom = ZOOM_MIN; applyZoom(); }
});

/* Safari does not give a page pointer events for a pinch: it recognises the
   gesture itself and reports it as one thing, with a scale, after cancelling
   the pointers it was made of. Left alone that means iPhones -- most of the
   guests this is for -- get the browser's own zoom of the whole page instead
   of this one. */
let gestureFrom = 0;
video.addEventListener("gesturestart", (event) => {
  event.preventDefault();
  gestureFrom = zoom;
});
video.addEventListener("gesturechange", (event) => {
  event.preventDefault();
  if (gestureFrom) zoomAbout(gestureFrom * event.scale, event.clientX, event.clientY);
});
video.addEventListener("gestureend", (event) => {
  event.preventDefault();
  gestureFrom = 0;
});

// Guarded, like the reconnect button: a browser holding an older page would
// otherwise throw here and take every listener defined after it with it.
if (el("zoom-btn") && el("zoom-range")) {
  el("zoom-btn").addEventListener("click", () => {
    const open = el("zoom").classList.toggle("open");
    el("zoom-btn").setAttribute("aria-expanded", open ? "true" : "false");
    // Opening it is asking about zoom, so keep the chips up while it is open.
    showHud(true);
  });

  el("zoom-range").addEventListener("input", () => {
    zoomAbout(parseInt(el("zoom-range").value, 10) / 100, null, null);
  });
}

/* The shape of the picture decides how far it can be moved, and that is not
   known until the stream says what size it is -- nor after it changes, which
   is what a codec renegotiation does. */
video.addEventListener("loadedmetadata", applyZoom);
window.addEventListener("resize", applyZoom);
if (window.visualViewport) {
  window.visualViewport.addEventListener("resize", applyZoom);
}

el("hudbtn").addEventListener("click", (event) => {
  event.stopPropagation();
  toggleHud();
});

function showNotice(html, sticky) {
  lastNotice = html;
  const box = el("notice");
  box.innerHTML = html;
  box.hidden = false;
  showHud();
  if (noticeTimer) { clearTimeout(noticeTimer); noticeTimer = null; }
  if (!sticky) noticeTimer = setTimeout(hideNotice, 9000);
}

function hideNotice() {
  if (noticeTimer) { clearTimeout(noticeTimer); noticeTimer = null; }
  el("notice").hidden = true;
  hideHud();
}

let noticeTimer = null;

el("notice").addEventListener("click", hideNotice);

// Guarded, unlike the buttons around it, because this one arrived after the
// page it lives in: a browser holding an older index.html would otherwise
// throw here at load time and take every listener below it with it.
if (el("revive")) {
  el("revive").addEventListener("click", () => {
    hideNotice();
    reviveNow("the guest asked for it");
  });
}

el("info").addEventListener("click", async () => {
  if (!el("notice").hidden) { hideNotice(); return; }
  const route = await describeRoute();
  showNotice(lastNotice
    ? lastNotice + '<p class="footnote">' + route + "</p>"
    : '<p class="footnote">' + route + "</p>", true);
});

let mediaTimer = null;

function armMediaTimeout() {
  clearMediaTimeout();
  mediaTimer = setTimeout(() => {
    // Connected is not the same as carrying anything, which is the mistake
    // that let a silent connection sit there for ever.
    if (pc && pc.connectionState === "connected" && lastBytes > 0) return;
    mediaFailed("No video after 20 seconds.");
  }, 20000);
}

function clearMediaTimeout() {
  if (mediaTimer) { clearTimeout(mediaTimer); mediaTimer = null; }
}

/* Tell the host what actually happened here. It is the only way the person
 * running the box can tell a closed port from a dropped packet from a codec
 * it cannot decode -- everything else they can see stops at "I sent it". */
async function report(what) {
  try {
    if (!socket || socket.readyState !== 1) return;
    socket.send(JSON.stringify({
      t: "report",
      detail: "[" + CLIENT_BUILD + "] " + what + " — " + (await describeRoute()) }));
  } catch (_) { /* reporting must never break anything */ }
}

/* mediaFailed borrows the prompt -- the "press any button" hint -- to explain
 * itself, because it is the one box on the page big enough for the
 * explanation. Two things follow from borrowing it, and neither was handled.
 *
 * It has to be given back. The hint's own words were overwritten, so once a
 * failure had been shown the prompt said "the video connection could not be
 * rebuilt" every time anything asked for the controller hint afterwards.
 *
 * And it has to be dismissible. The click-to-close on the page is attached to
 * the notice, which is a different element; this one had no way out at all,
 * so a guest whose video came back was left reading a stale failure over a
 * working picture until they closed the page and opened it again. */
let promptHint = null;              // the hint's own words, before a failure
let promptFailed = false;

function clearMediaFailure() {
  if (!promptFailed) return;
  promptFailed = false;
  if (promptHint !== null) el("prompt").innerHTML = promptHint;
  el("prompt").hidden = true;
  // The renewals that were spent failing should not count against the next
  // hiccup: without this the very next one is over the limit immediately and
  // says it could not be rebuilt without trying.
  renewals = 0;
}

async function mediaFailed(why) {
  setLink("bad", "no video");
  showHud(true);
  if (promptHint === null) promptHint = el("prompt").innerHTML;
  promptFailed = true;
  el("prompt").hidden = false;
  const route = await describeRoute();
  report(why);
  el("prompt").innerHTML =
    "<p><strong>" + why + "</strong></p>" +
    "<p class=\"footnote\">Reconnect at the top of the screen builds the " +
    "whole connection again, which is worth a try before anything else.</p>" +
    "<p class=\"footnote\">The page and the PIN reach the host over one port; " +
    "the video takes a different, direct route, and that one is not getting " +
    "through. Usually the host's UDP ports are not forwarded.</p>" +
    "<p class=\"footnote\">" + route + "</p>" +
    "<p class=\"footnote\"><button type=\"button\" " +
    "class=\"linkish prompt-close\">Close this</button></p>";
  // Found by class off the prompt, not by id: this button is written here
  // rather than living in index.html, and every id el() reaches for is
  // supposed to be in the page -- there is a test that says so, and it is
  // right to. A node that only exists sometimes should not claim a name that
  // the whole document shares.
  const close = el("prompt").querySelector(".prompt-close");
  if (close) close.addEventListener("click", clearMediaFailure);
}

/* Which pair of addresses the browser settled on, in words. This is the fact
 * that tells a forwarded-port problem from a firewall problem from a codec
 * problem, and the guest is the only one who can see it -- the host cannot
 * tell whether the packets it sent ever arrived. */
async function describeRoute() {
  if (!pc || !pc.getStats) return "No connection details available.";
  try {
    const stats = await pc.getStats();
    let pair = null;
    const byId = new Map();
    stats.forEach((r) => byId.set(r.id, r));
    stats.forEach((r) => {
      if (r.type === "candidate-pair" && (r.selected || r.state === "succeeded")) {
        if (!pair || r.selected) pair = r;
      }
    });
    if (!pair) {
      const tried = [];
      stats.forEach((r) => {
        if (r.type === "remote-candidate" && (r.address || r.ip)) {
          tried.push((r.address || r.ip) + ":" + r.port
                     + " (" + r.candidateType + ")");
        }
      });
      return tried.length
        ? "Nothing connected. The host offered: " + [...new Set(tried)].join(", ")
        : "Nothing connected, and the host offered no reachable address.";
    }
    const remote = byId.get(pair.remoteCandidateId);
    const bytes = pair.bytesReceived || 0;
    return "Connected to " +
      (remote ? (remote.address || remote.ip || "?") + ":" + remote.port
                + " (" + remote.candidateType + ")"
              : "the host") +
      ", received " + Math.round(bytes / 1024) + " kB.";
  } catch (_) {
    return "No connection details available.";
  }
}

// Same detail on demand, whether or not anything failed -- tapping the link
// chip reports the route, which is the only way to learn it from a phone.
el("link").addEventListener("click", async () => {
  showNotice('<p class="footnote">' + (await describeRoute()) + "</p>");
});

/* Whether anything is actually arriving.
 *
 * Connection state is not enough on its own. A tab that has been in the
 * background comes back with its RTCPeerConnection still reporting
 * "connected" while no media has moved for however long it was away -- so
 * nothing fires, nothing retries, and the last frame sits on the screen. The
 * only honest measure is whether the byte count is going up.
 */
/* Bumped whenever the client changes in a way a stale page would hide. It goes
   out with every report, so the host log says which page is actually running
   rather than which one was deployed -- a browser holding an old one looks
   exactly like a fix that did not work. */
const CLIENT_BUILD = "2026-09-05f";

const STALL_LIMIT_MS = 6000;
/* How long a connection that says it is up has to produce a single video byte
   before it is treated as broken. Longer than STALL_LIMIT_MS because a fresh
   connection has DTLS and a first keyframe to get through, and shorter than
   the twenty seconds the old media timeout waited, which never fired anyway --
   it gave up only when the connection was *not* connected, and this one is. */
const SILENT_LIMIT_MS = 9000;
let lastBytes = -1, stalledSince = 0, watchdogTimer = null, connectedAt = 0;

/* A freeze is counted by the browser and by nobody else. The host knows what
 * it sent; it cannot see a picture that stopped for a third of a second and
 * started again, which is the whole difficulty with the complaint. These
 * numbers say which of the three it was: packets that never arrived (lost,
 * and whether asking for them back worked), a host that sent nothing for a
 * while (no loss, and the host's own log says the same), or a buffer too
 * small for the way the packets arrived (no loss, nothing missing, and the
 * held-back figure near zero).
 *
 * Reported at most once every quarter minute, because a bad minute would
 * otherwise fill the host's log with the same sentence. */
const FREEZE_REPORT_GAP_MS = 15000;
let lastVideoStat = null, lastFreezeReport = 0;

function heldBackMs(now, before) {
  const emitted = (now.jitterBufferEmittedCount || 0) -
                  (before.jitterBufferEmittedCount || 0);
  if (emitted <= 0) return null;
  const delay = (now.jitterBufferDelay || 0) - (before.jitterBufferDelay || 0);
  return Math.round((delay / emitted) * 1000);
}

function noteFreezes(now) {
  if (!now) return;
  const before = lastVideoStat;
  lastVideoStat = now;
  if (!before) return;
  const froze = (now.freezeCount || 0) - (before.freezeCount || 0);
  if (froze <= 0) return;
  const at = Date.now();
  if (at - lastFreezeReport < FREEZE_REPORT_GAP_MS) return;
  lastFreezeReport = at;
  const held = Math.round(1000 * ((now.totalFreezesDuration || 0) -
                                  (before.totalFreezesDuration || 0)));
  const buffer = heldBackMs(now, before);
  report("picture froze " + froze + "x for " + held + " ms" +
         "; lost " + ((now.packetsLost || 0) - (before.packetsLost || 0)) +
         ", asked back " + ((now.nackCount || 0) - (before.nackCount || 0)) +
         ", keyframes " + ((now.pliCount || 0) - (before.pliCount || 0)) +
         ", frames dropped " +
         ((now.framesDropped || 0) - (before.framesDropped || 0)) +
         ", held back " + (buffer === null ? "?" : buffer + " ms"));
}

/* What the sound actually turned into on this phone, said once.
 *
 * "The sound is poor" cannot be chased from the host, which knows only what it
 * encoded and sent. The browser knows what it decoded: how many channels it
 * settled on, which is the whole question when the offer forgot to say; how
 * many samples the decoder had to invent because the packet carrying them
 * never arrived, which is what crackle and warble actually are; and what it
 * was told in the fmtp line, which is the fix under test.
 *
 * Once per connection, when enough sound has arrived to mean anything. It is
 * one line in a log that already carries the picture's health, and it is the
 * only way to tell a stereo stream folded to mono apart from a stereo stream
 * arriving in pieces -- the two sound quite different and read the same in a
 * complaint. */
let soundTold = false;

async function tellAboutSound() {
  if (soundTold || !pc || !pc.getStats) return;
  let sound = null, codec = null;
  try {
    const stats = await pc.getStats();
    const byId = new Map();
    stats.forEach((r) => byId.set(r.id, r));
    stats.forEach((r) => {
      if (r.type === "inbound-rtp" && (r.kind === "audio" || r.mediaType === "audio")) {
        sound = r;
      }
    });
    if (sound && sound.codecId) codec = byId.get(sound.codecId);
  } catch (_) { return; }
  // A second of sound, give or take: before that the concealment figure is
  // mostly the connection starting up and says nothing about the sound.
  if (!sound || (sound.totalSamplesReceived || 0) < 48000) return;
  soundTold = true;
  const total = sound.totalSamplesReceived || 1;
  const invented = (sound.concealedSamples || 0) / total * 100;
  report("sound: "
    + (codec ? (codec.channels || "?") + " channels at "
               + Math.round((codec.clockRate || 0) / 1000) + " kHz" : "codec unknown")
    + ", " + (sound.packetsLost || 0) + " packets lost, "
    + invented.toFixed(2) + "% of samples invented, jitter "
    + Math.round((sound.jitter || 0) * 1000) + " ms"
    + (codec && codec.sdpFmtpLine ? " [" + codec.sdpFmtpLine + "]" : ""));
}

async function watchMedia() {
  if (ended || !pc) return;
  let bytes = 0;
  let picture = null;
  let path = null;
  try {
    (await pc.getStats()).forEach((r) => {
      if (r.type === "inbound-rtp" && (r.kind === "video" || r.mediaType === "video")) {
        bytes += r.bytesReceived || 0;
        picture = r;
      }
      // The pair actually carrying the media. Its round trip time is the
      // ping that matters here -- the one the buttons travel over -- and it
      // is not the same as the time to the web server.
      if (r.type === "candidate-pair" && (r.nominated || r.state === "succeeded")
          && r.currentRoundTripTime != null) {
        path = r;
      }
    });
  } catch (_) { return; }
  // Before the branches below, every one of which returns.
  noteFreezes(picture);
  tellAboutSound();
  reportHealth(picture, path);

  if (bytes > lastBytes) {
    lastBytes = bytes;
    stalledSince = 0;
    connectedAt = 0;                     // it proved itself
    mediaFresh = true;
    // Video is arriving, so whatever the chip last said is out of date. This
    // is also what stops it sticking on "reconnecting" after a rebuild that
    // actually worked.
    setLink("ok");
    if (!el("notice").hidden && lastNotice.includes("could not")) hideNotice();
    // And the explanation of a failure that is plainly over. This is the case
    // that sent people to reload the page: the picture was fine and the panel
    // over it still said the connection could not be rebuilt.
    clearMediaFailure();
    return;
  }
  if (lastBytes < 0) {
    /* Not one byte has arrived on this connection yet. This used to return
       here for ever, which is the whole of why a guest had to reload the page
       after starting a game: the rebuilt connection came up, said "connected",
       opened its data channel so the buttons still worked, and carried no
       video -- and nothing was watching for a connection that never starts, as
       opposed to one that stops. */
    if (connectedAt && Date.now() - connectedAt >= SILENT_LIMIT_MS) {
      connectedAt = 0;
      mediaFresh = false;
      setLink("warn");
      report("connected but no video arrived; rebuilding");
      renewSoon(0, true);
    }
    return;
  }

  const now = Date.now();
  if (!stalledSince) { stalledSince = now; return; }
  if (now - stalledSince >= STALL_LIMIT_MS) {
    stalledSince = 0;
    mediaFresh = false;
    setLink("warn");
    renewSoon(0, true);
  }
}

function startWatchdog() {
  if (watchdogTimer) clearInterval(watchdogTimer);
  watchdogTimer = setInterval(watchMedia, 2000);
}

/* How much video to hold before drawing it, in milliseconds, as the host asked.
 *
 * Every packet of a frame leaves the host in one burst. A wifi hop or a tunnel
 * spreads that burst out, and Chrome -- which on a quiet network lets its
 * buffer shrink to almost nothing -- had already decided when to draw the
 * frame before the last packet of it arrived. The picture holds still and then
 * catches up, which is what a guest calls a freeze, and no packet was lost so
 * nothing asks for anything to be resent.
 *
 * jitterBufferTarget is the current name for this; playoutDelayHint is the
 * older one and takes seconds, not milliseconds. Browsers that have neither
 * are left as they are, which is what they did before. */
function holdVideoBack(ms) {
  if (!pc || !(ms > 0)) return;
  try {
    pc.getReceivers().forEach((receiver) => {
      const kind = receiver.track ? receiver.track.kind : "";
      if (kind && kind !== "video") return;
      if ("jitterBufferTarget" in receiver) {
        try { receiver.jitterBufferTarget = ms; } catch (_) {}
      } else if ("playoutDelayHint" in receiver) {
        try { receiver.playoutDelayHint = ms / 1000; } catch (_) {}
      }
    });
  } catch (_) { /* an old browser: it plays as it always did */ }
}

/* ---- the pad ---- */

function startPadLoop() {
  window.addEventListener("gamepadconnected", (event) => {
    // Only if this page has not already got one. It used to take whatever
    // arrived, so plugging a second controller in moved this player's seat
    // onto somebody else's pad -- which was wrong on its own and is the whole
    // problem when the second pad is meant to be a second player.
    const pads = navigator.getGamepads ? navigator.getGamepads() : [];
    const held = padIndex !== null && pads[padIndex] && pads[padIndex].connected;
    if (!held) {
      padIndex = event.gamepad.index;
      el("prompt").hidden = true;
      describePad(event.gamepad);
    }
    paintControllers();
  });
  window.addEventListener("gamepaddisconnected", (event) => {
    // A seated controller being unplugged takes its seat with it: the host
    // would free it eventually on silence, and "eventually" is a player port
    // nobody can use in the meantime.
    if (event && event.gamepad && extras.has(event.gamepad.index)) {
      dropExtra(event.gamepad.index);
      return;
    }
    // Only this page's own controller going is a reason to offer the glass
    // back. Somebody else's pad leaving is not this player's problem.
    const pads = navigator.getGamepads ? navigator.getGamepads() : [];
    if (padIndex === null || !pads[padIndex] || !pads[padIndex].connected) {
      forgetPad();
    }
    paintControllers();
  });

  wireTouch();
  wireSticks();
  // Whatever was chosen last time, applied before anything is on screen, so
  // the app opens the way round it was left rather than turning as it loads.
  applyOrient(savedOrient());
  // Filled in here rather than only when the on-screen pad is shown. It was
  // built inside showTouch(), which a desktop with a real controller and no
  // touchscreen never calls -- and the select is in the page from the start
  // and visible, so it sat there with no options in it, which a browser draws
  // as a small empty box. It looked broken because it was empty, not because
  // it was broken.
  buildLayoutPicker();
  // A phone with no controller gets the on-screen pad without being asked; a
  // laptop does not, because a mouse cannot use it and it would only be in the
  // way. The link in the prompt covers everyone this guesses wrong about.
  if (!hasGamepad() && navigator.maxTouchPoints > 0
      && chosenLayout() !== "keyboard") showTouch(true);

  ticker = setInterval(tick, Math.round(1000 / SEND_HZ));

  // Leaving must not leave a button held down on someone else's television.
  const letGo = () => { keyButtons = 0; sendFrame(null, true); };
  window.addEventListener("pagehide", letGo);
  window.addEventListener("beforeunload", letGo);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") letGo();
  });
}

function describePad(pad) {
  /* Name the controller in the chip. This function was called from two places
     and defined in none, so every call threw and padName stayed empty: on a
     desktop the "press any button" prompt still disappeared, which looks like
     detection, and on a phone that prompt is never shown in the first place,
     so a connected controller produced no sign of itself anywhere. */
  padName = shortPadName((pad && pad.id) || "");
  loadPadMap();
  paintPicker();
}

/* A controller id fit to show somebody.
 *
 * Ids carry vendor and product codes and, in Chrome, the words STANDARD
 * GAMEPAD; Firefox prefixes the vendor and product as hex, e.g.
 * 054c-05c4-Wireless Controller, which is four useless words' worth of a chip.
 * None of it means anything to the person holding the thing. Pulled out of
 * describePad() so the panel's list of what is attached reads the same as the
 * chip does, rather than growing a second opinion about the same controller.
 */
function shortPadName(raw) {
  raw = raw || "";
  let name = raw.replace(/\s*\([^)]*\)\s*/g, " ").replace(/\s+/g, " ").trim();
  name = name.replace(/^[0-9a-f]{4}-[0-9a-f]{4}-\s*/i, "");
  if (!name) name = raw.trim();
  if (!name) name = "Controller";
  if (name.length > 28) name = name.slice(0, 27).trimEnd() + "\u2026";
  return name;
}

function forgetPad() {
  padIndex = null;
  padName = "";
  paintPicker();
  // Their controller has gone; offer the on-screen one back unless they
  // turned it off deliberately.
  if (!chosenByHand && el("padtype").value !== "off") showTouch(true);
  // "Press any button on your controller" is the wrong thing to say to
  // somebody who is playing on the keyboard and has no controller to press.
  else if (!keyboardOn) el("prompt").hidden = false;
}

function hasGamepad() {
  const pads = navigator.getGamepads ? navigator.getGamepads() : [];
  return Array.from(pads).some((p) => p && p.connected);
}

/* Send the pad now, because something on the glass just changed.
 *
 * The timer runs at 125 Hz, so waiting for it costs up to 8 ms -- small, but
 * it is 8 ms of nothing between a thumb landing and anything at all
 * happening, and it is paid on every single press. A button that is read the
 * moment it is pressed is the thing being copied here; the timer's job is the
 * heartbeat and the physical pad, neither of which knows when it changed. */
function sendNow() {
  if (padsOpen) return;
  sendFrame(remapped(livePad()), false);
}

/* The extra seats, on the page's own clock. Deliberately not on sendNow():
   that fires when something on *this* page's glass changed, and nothing a
   thumb does here has anything to do with the controller somebody else is
   holding. */

function tick() {
  // Every seated controller, whatever else this page is doing. Before the
  // page's own frame rather than after it: a second player's buttons are not
  // less urgent than the first's.
  sendExtras();
  /* Deliberately before the channel check below. Which controller is attached
     is worth showing whether or not there is anywhere to send its buttons yet,
     and polling rather than trusting the events matters on iOS, where
     gamepadconnected arrives late, once, or not at all. */
  const pads = navigator.getGamepads ? navigator.getGamepads() : [];
  let pad = padIndex !== null ? pads[padIndex] : null;
  if (pad && !pad.connected) pad = null;
  if (!pad) pad = firstFreePad(pads);
  if (pad && padIndex === null) {
    padIndex = pad.index;
    el("prompt").hidden = true;
    describePad(pad);
  } else if (pad && !padName) {
    describePad(pad);                  // named late, after a reconnect
  } else if (!pad && padIndex !== null) {
    forgetPad();                       // gone without an event, which iOS does
  }

  if (!input || input.readyState !== "open") return;
  // While somebody is teaching this page which button is which, their presses
  // are answers to a question, not moves in a game. Sending them would have
  // the character running about on a television in another house while its
  // owner watched.
  // Nothing pressed while this panel is open reaches the game. It is a
  // mirror: somebody finding out what their buttons are called should not be
  // starting a game by doing it, which is how a game got started too soon.
  if (padsOpen) return;
  // Otherwise: whatever the guest told us their buttons really are.
  sendFrame(remapped(pad), false);
}

let lastSent = null, lastSentAt = 0;

/* How long a press takes to leave this page, measured rather than assumed.
 *
 * The complaint this exists for: buttons that have to be held before the game
 * notices on one phone, and feel like buttons on another. Everything between
 * the thumb and the wire is a suspect -- when the browser chooses to deliver
 * pointerdown, whether the data channel is backed up and dropping frames,
 * whether the timer is being throttled -- and none of it can be seen from the
 * host, where the symptom is.
 *
 * pointerdown's own timeStamp is the start, not the moment the handler runs:
 * that is the whole question. A browser that sat on the event for 80 ms
 * before telling the page about it is indistinguishable, from inside the
 * handler, from one that delivered it instantly. */
let pressedAt = 0;
let presses = [], dropped = 0, buffered = 0;
const PRESS_SAMPLE = 25;

function notePress(latency, backlog) {
  presses.push(Math.round(latency));
  buffered = Math.max(buffered, backlog);
  if (presses.length < PRESS_SAMPLE) return;
  const sorted = presses.slice().sort((a, b) => a - b);
  const mid = sorted[Math.floor(sorted.length / 2)];
  report("press to wire over " + sorted.length + " presses: median " + mid
         + " ms, worst " + sorted[sorted.length - 1] + " ms, "
         + dropped + " dropped for backlog, deepest queue " + buffered
         + " bytes");
  presses = [];
  dropped = 0;
  buffered = 0;
}

function changed(buttons, axes) {
  if (!lastSent) return true;
  if (lastSent.buttons !== buttons) return true;
  for (let i = 0; i < axes.length; i++) {
    if (Math.abs(axes[i] - lastSent.axes[i]) > AXIS_EPSILON) return true;
  }
  return false;
}

function sendFrame(pad, releaseAll) {
  if (!input || input.readyState !== "open") return;
  // A physical pad and the on-screen one are merged rather than one replacing
  // the other: whichever is being touched wins by simply being pressed.
  const state = FPFrame.padState(releaseAll ? null : pad);
  // The keyboard joins the same merge for the same reason: whichever is being
  // pressed wins by being pressed, and nothing has to be turned off first.
  const buttons = releaseAll ? 0 : (state.buttons | touchButtons | keyButtons);
  // Whichever is being touched wins, exactly as the buttons do: an on-screen
  // stick only overrides the physical one while a thumb is actually on it.
  const axes = releaseAll ? [0, 0, 0, 0, 0, 0] : state.axes.slice();
  if (!releaseAll) {
    for (let i = 0; i < 4; i++) {
      if (touchAxes[i]) axes[i] = FPFrame.toAxis(touchAxes[i]);
    }
    /* The shoulders are buttons on this screen and triggers on the pad the
       host builds. A physical controller reports both -- the button pressed
       *and* how far the trigger travelled -- but a finger on glass has no
       travel to report, so the button alone arrived and the trigger stayed at
       rest. Games that steer or accelerate with an analogue trigger therefore
       did nothing at all: Crazy Taxi would let you drive its menus and not its
       car. Held on screen means held all the way. */
    for (const [bit, axis] of [[6, 4], [7, 5]]) {
      if ((touchButtons | keyButtons) & (1 << bit)) axes[axis] = FPFrame.TRIGGER_FULL;
    }
  }

  const now = Date.now();
  const due = releaseAll || changed(buttons, axes) ||
              (now - lastSentAt) >= HEARTBEAT_MS;
  if (!due) return;

  // Never add to a channel that is already backed up, even for real input.
  //
  // This was the other way round -- heartbeats were skipped but changes always
  // sent -- on the reasoning that losing input is worse than a deeper queue.
  // It is not: the queue is an SCTP association, and when it overflows the
  // association fails outright, which takes the guest's whole connection with
  // it and, on this GStreamer, the host's capture pipeline as well. One
  // guest's stuck data channel ended the session for everybody.
  //
  // Dropping a frame costs nothing, because every frame is a complete snapshot
  // of the pad: the next one supersedes whatever was lost. A release is the
  // exception and always goes, because a button left down is the one state
  // that does not correct itself.
  if (!releaseAll && input.bufferedAmount > BACKLOG_LIMIT) {
    if (pressedAt) dropped += 1;
    return;
  }

  const buffer = FPFrame.buildRaw(buttons, axes, seq, releaseAll);
  seq = (seq + 1) & 0xffff;
  try {
    input.send(buffer);
    lastSent = { buttons, axes: axes.slice() };
    lastSentAt = now;
    if (pressedAt) {
      // performance.now() and a pointer event's timeStamp share an origin, so
      // this is the whole distance: the browser's delay in delivering the
      // event, plus everything this page did with it.
      notePress(performance.now() - pressedAt, input.bufferedAmount);
      pressedAt = 0;
    }
  } catch (_) { /* a closing channel is not news */ }
}

/* The television is in a menu, so this controller is not reaching it.
 *
 * Said plainly and immediately, because the alternative is a guest pressing
 * buttons at a picture that is plainly moving and concluding the whole thing
 * is broken -- and then reloading, which fixes nothing and costs them their
 * seat for a moment.
 *
 * The buttons keep working on this page: they light, they send, and the host
 * counts them. They stop at the television. That distinction is not worth
 * explaining to somebody holding a pad, so the message is about the
 * television rather than about frames. */
function holdInput(message) {
  /* Whether this page is the one that may drive. The server says so per
     guest rather than the page working it out from a slot number: which slot
     this browser holds is the server's business, it can change, and a page
     that guessed wrong would either pause a driver or let everybody through. */
  const driving = !!message.driving;
  const held = !!message.held && !driving;
  document.documentElement.classList.toggle("held", held);
  if (driving) {
    document.documentElement.classList.remove("held");
    showNotice("<p><strong>You are driving " + escapeText(message.why || "the screen")
      + "</strong></p>"
      + '<p class="footnote">The host has handed this to you, so your '
      + "controller reaches it as if you were in the room. It lasts until "
      + "they take it back, until you leave, or until something else comes to "
      + "the front.</p>", false);
    return;
  }
  if (held) {
    const who = message.driver_label
      ? escapeText(message.driver_label) + " is driving it."
      : "";
    // The host's own words when it has better ones. "The television is in a
    // menu" is right for a menu and wrong for a Steam game nobody gave them,
    // and being told the real reason is the difference between a controller
    // that seems broken and one that is waiting on a permission.
    const because = message.because
      ? escapeText(message.because) + " Ask whoever set this up to give it to "
        + "you, and your controller works here as it always did."
      : "The television is in a menu rather than in a game, and controllers "
        + "here only reach the game. " + who
        + " They come back the moment something is playing.";
    showNotice("<p><strong>Controls paused</strong></p>"
      + '<p class="footnote">' + because + "</p>", false);
  } else if (!el("notice").hidden
             && (lastNotice.includes("Controls paused")
                 || lastNotice.includes("You are driving"))) {
    hideNotice();
  }
}

/* ---- how this connection is running, and who else is here ----------------
 *
 * The host cannot measure any of this. Round trip time and lost packets are
 * properties of the path to *this* guest, and the host only ever sees its own
 * end of it -- so each page measures itself and says so, and the host is the
 * place they are collected and handed back out.
 *
 * Sent on a slow clock of its own rather than on every stats poll: the poll
 * is a second, and a message a second per guest to keep a panel up to date
 * that is usually closed is a poor trade.
 */
const HEALTH_EVERY_MS = 4000;
let healthSent = 0;
let lastLost = null;                 // to turn running totals into a rate

function reportHealth(picture, path) {
  const now = Date.now();
  if (now - healthSent < HEALTH_EVERY_MS) return;
  healthSent = now;

  const rtt = path && path.currentRoundTripTime != null
    ? Math.round(path.currentRoundTripTime * 1000) : null;

  // Loss since the last report, not since the connection began. A run of bad
  // minutes an hour ago should not still be showing as this guest's state
  // now, and a lifetime average is exactly what would do that.
  let loss = null;
  if (picture) {
    const lost = picture.packetsLost || 0;
    const got = picture.packetsReceived || 0;
    if (lastLost && got >= lastLost.got) {
      const dLost = Math.max(0, lost - lastLost.lost);
      const dGot = got - lastLost.got;
      if (dLost + dGot > 0) loss = (dLost / (dLost + dGot)) * 100;
    }
    lastLost = { lost, got };
  }

  send({ t: "health", rtt,
         loss: loss == null ? null : Math.round(loss * 10) / 10,
         fps: picture && picture.framesPerSecond != null
           ? Math.round(picture.framesPerSecond) : null });
}

/* Who is in the room. Kept here rather than read out of the DOM so that a
   redraw -- which happens whenever anybody joins, leaves or changes seat --
   does not lose which person the reader had open. */
let mySlot = null;
let people = [];
let personOpen = null;               // slot, or null for nobody
let peopleTimer = 0;
const PEOPLE_EVERY_MS = 5000;

/* ---- accounts, on the page ----------------------------------------------
 *
 * Everything here decides what to *draw*. Not one of these checks stops
 * anything: the host asks the same questions again about every action, and a
 * guest who edits this file gets a page with more buttons on it and exactly
 * the same answers from the other end.
 *
 * The login lives behind your own name in the people list. That is where
 * names already are, it is the one row somebody would think to tap about
 * themselves, and it means a guest who has no account never sees a login at
 * all -- no list of names, no hint that an admin exists.
 */
const DEVICE_KEY = "fp-device";

let account = null;            // {name, can: [...], fresh} or null
let sessionLimits = null;      // {limit, slots, locked, here}
let loginOpen = false;

function may(capability) {
  if (!account) return false;
  const can = account.can || [];
  if (can.includes(capability)) return true;
  // Plain "steam" covers every game on the list, the same rule the host uses.
  return capability.indexOf("steam:") === 0 && can.includes("steam");
}

/* Anything in the Session tab. The tab itself is not drawn without one of
   these, so an account given only a Steam game never sees an owner's panel. */
function mayAnything() {
  return ["slots", "lock", "kick", "reshare", "grant"].some(may);
}

function savedDevice() {
  try { return localStorage.getItem(DEVICE_KEY) || ""; } catch (_) { return ""; }
}

function rememberDevice(token) {
  try {
    if (token) localStorage.setItem(DEVICE_KEY, token);
    else localStorage.removeItem(DEVICE_KEY);
  } catch (_) {}
}

function loggedIn(message) {
  const first = !account;
  account = { name: message.name, can: message.can || [],
              fresh: message.fresh !== false };
  if (message.device) rememberDevice(message.device);
  loginOpen = false;
  paintAccount();
  if (first) {
    showToast("Logged in as " + message.name);
    // The catalogue is different for an account -- a Steam game they have
    // been given was not in the list they were sent before they said who
    // they were.
    send({ t: "games" });
  }
}

function loggedOut() {
  account = null;
  loginOpen = false;
  paintAccount();
}

function granted(message) {
  showToast("Saved what " + message.name + " may do");
  send({ t: "people" });
}

function limitsFrom(message) {
  sessionLimits = message;
  paintSession();
}

function reshared(message) {
  if (message.url) {
    // Shown rather than toasted: it is the thing they came here to get, and
    // a toast that disappears is no way to hand somebody a PIN.
    const box = el("reshare-new");
    if (box) {
      box.textContent = "New link: " + message.url + "  ·  PIN: " + message.pin;
      box.hidden = false;
    }
  }
  showToast("New link and PIN");
}

/* What the page draws once it knows who somebody is. */
function paintAccount() {
  // The tab itself is always there -- it is where logging in lives. What
  // changes with the account is how much is inside it.
  //
  // The login sheet is painted whether the panel is open or not. paintSession
  // stops early on a closed panel, which is right for the parts that read the
  // people list, and meant a page logged back in by a remembered device had
  // the right account and a panel that still said nothing -- correct as soon
  // as it was opened, and wrong in the moment before.
  paintLogin();
  paintSession();
}

/* The login sheet. It sits in the Account tab and stays there -- three
   states in one block of markup, and this only picks which one is showing.

   It used to be moved into your own row in the people list, on the reasoning
   that a stranger should see no sign that accounts exist. That cost the owner
   any way of finding it and bought nothing: what keeps people out is the
   password and the authenticator, not whether the door is signposted. */
function paintLogin() {
  const sheet = el("login-sheet");
  if (!sheet) return;
  sheet.hidden = false;
  el("login-in").hidden = !account;
  el("login-outside").hidden = !!account || loginOpen;
  el("login-form").hidden = !!account || !loginOpen;
  if (account) {
    el("login-as").textContent = account.name;
    paintChips(el("login-can"), account.can || []);
    el("login-remembered").hidden = account.fresh !== false;
  }
}

/* One chip per thing an account may do. It was a comma-separated sentence
   that ran to five lines on a phone; these are the same words in a shape that
   can be counted at a glance. */
function paintChips(box, can) {
  box.innerHTML = "";
  const words = can.length ? can.map(saidCapability)
                           : ["nothing has been given to this account yet"];
  box.classList.toggle("acct-none", !can.length);
  words.forEach((said) => {
    const chip = document.createElement("span");
    chip.textContent = said;
    box.appendChild(chip);
  });
}

/* Said in what it does. "reshare" is a word from this program's insides. */
function saidCapability(capability) {
  const said = GRANTABLE.find((row) => row[0] === capability);
  // Only the first letter, not the whole phrase: lowercasing all of it turned
  // "a new link and PIN" into "a new link and pin".
  if (said) return said[1].charAt(0).toLowerCase() + said[1].slice(1);
  if (capability.indexOf("steam:") === 0) return "play one Steam game";
  return capability;
}

function wireLogin() {
  const open = el("login-open");
  if (open) open.addEventListener("click", () => { loginOpen = true; paintLogin(); });
  const cancel = el("login-cancel");
  if (cancel) cancel.addEventListener("click", () => { loginOpen = false; paintLogin(); });
  const out = el("login-out");
  if (out) {
    out.addEventListener("click", () => {
      // Forgotten here as well as at the host: a device this page stops
      // trusting should not be quietly re-offered on the next join.
      rememberDevice("");
      send({ t: "logout" });
    });
  }
  const form = el("login-form");
  if (form) {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      send({ t: "login",
             name: (el("login-user").value || "").trim(),
             password: el("login-pass").value || "",
             code: (el("login-code").value || "").trim(),
             remember: !!el("login-remember").checked });
      // Cleared whatever the answer is. A password left sitting in a field on
      // a phone that gets handed round is the thing this was all for.
      el("login-pass").value = "";
      el("login-code").value = "";
    });
  }
}

/* ---- the owner's panel --------------------------------------------------- */

function paintSession() {
  const panel = el("tab-session");
  if (!panel || panel.hidden) return;
  paintLogin();
  const who = el("session-who");
  if (who) who.textContent = peopleHere();
  show("session-limit", may("slots"));
  show("session-lock", may("lock"));
  show("session-reshare", may("reshare"));
  show("session-kick", may("kick"));
  show("session-grant", may("grant"));

  const count = el("limit-count");
  if (count && sessionLimits) {
    count.max = sessionLimits.slots || 8;
    if (document.activeElement !== count) count.value = sessionLimits.limit;
  }
  const lock = el("lock-toggle");
  if (lock && sessionLimits) {
    lock.textContent = sessionLimits.locked
      ? "Open it to everyone again" : "Lock to accounts";
  }
  paintKickList();
  paintGrantList();
}

function wireSession() {
  const set = el("limit-set");
  if (set) {
    set.addEventListener("click", () => {
      const count = parseInt(el("limit-count").value, 10);
      if (count > 0) send({ t: "limit", count });
    });
  }
  const lock = el("lock-toggle");
  if (lock) {
    lock.addEventListener("click", () => {
      const on = !(sessionLimits && sessionLimits.locked);
      if (on && !window.confirm("Lock this session to named accounts? "
                                + "Everybody who is not logged in is removed. "
                                + "You keep your place.")) return;
      send({ t: "lock", on });
    });
  }
  const share = el("reshare-now");
  if (share) {
    share.addEventListener("click", () => {
      if (!window.confirm("Give out a new link and PIN? The ones you sent "
                          + "stop working. Everybody here keeps their place.")) return;
      send({ t: "reshare" });
    });
  }
}

function peopleHere() {
  if (!sessionLimits) return "";
  const here = sessionLimits.here || 0;
  return here + (here === 1 ? " person is" : " people are") + " connected of "
    + sessionLimits.limit + " allowed.";
}

function show(id, on) {
  const box = el(id);
  if (box) box.hidden = !on;
}

function paintKickList() {
  const list = el("kick-list");
  if (!list || !may("kick")) return;
  list.innerHTML = "";
  people.filter((person) => person.slot !== mySlot).forEach((person) => {
    const row = document.createElement("div");
    row.className = "acct-person";
    const name = document.createElement("span");
    name.textContent = person.name
      + (person.account ? " \u00b7 " + person.account : "");
    row.appendChild(name);
    const go = document.createElement("button");
    go.type = "button";
    go.className = "ghost";
    go.textContent = "Remove";
    go.addEventListener("click", () => {
      // Asked, because it happens to somebody else and cannot be undone: the
      // link they were sent stops working with them.
      if (!window.confirm("Remove " + person.name + "? Their link stops "
                          + "working, so they cannot simply rejoin.")) return;
      send({ t: "kick", slot: person.slot });
    });
    row.appendChild(go);
    list.appendChild(row);
  });
  if (!list.children.length) {
    const empty = document.createElement("p");
    empty.className = "footnote";
    empty.textContent = "Nobody else is connected.";
    list.appendChild(empty);
  }
}

/* Who else has an account, and what they may do. The names come from the
   people list, so this only offers to change accounts that are actually in
   the session -- which is the only case where changing one is useful, and
   keeps the page from ever being a directory of accounts. */
function paintGrantList() {
  const list = el("grant-list");
  if (!list || !may("grant")) return;
  list.innerHTML = "";
  const named = people.filter((person) => person.account);
  if (!named.length) {
    const empty = document.createElement("p");
    empty.className = "footnote";
    empty.textContent = "Nobody here is logged in to an account.";
    list.appendChild(empty);
    return;
  }
  named.forEach((person) => {
    const row = document.createElement("div");
    row.className = "grant-row";
    const title = document.createElement("p");
    title.className = "acct-name";
    title.textContent = person.account
      + (person.slot === mySlot ? " (you)" : "");
    row.appendChild(title);
    // Anything the tick boxes below cannot express -- a grant for one Steam
    // game rather than all of them. Said rather than hidden: it is kept when
    // the boxes are saved, and an admin looking at an unticked "Steam games"
    // would otherwise think this account had none.
    const perGame = (person.can || []).filter((c) => c.indexOf("steam:") === 0);
    if (perGame.length) {
      const also = document.createElement("p");
      also.className = "footnote";
      also.textContent = "Also has " + perGame.length
        + (perGame.length === 1 ? " Steam game" : " Steam games")
        + " given at the console. Kept when you save this.";
      row.appendChild(also);
    }
    GRANTABLE.forEach(([capability, words]) => {
      const label = document.createElement("label");
      const tick = document.createElement("input");
      tick.type = "checkbox";
      tick.checked = (person.can || []).includes(capability);
      tick.addEventListener("change", () => {
        const now = new Set(person.can || []);
        if (tick.checked) now.add(capability); else now.delete(capability);
        send({ t: "grant", name: person.account, can: Array.from(now) });
      });
      label.appendChild(tick);
      label.appendChild(document.createTextNode(" " + words));
      row.appendChild(label);
    });
    list.appendChild(row);
  });
}

/* Said in what they do, not in what they are called. "reshare" is a word from
   this program's insides; "give out a new link and PIN" is the thing. */
const GRANTABLE = [
  ["steam", "Start and play Steam games"],
  ["stop", "End and restart what is playing"],
  ["kick", "Remove people"],
  ["reshare", "Give out a new link and PIN"],
  ["slots", "Set how many may connect"],
  ["lock", "Lock the session to accounts"],
  ["grant", "Change what others may do"],
];

function peopleFrom(message) {
  people = Array.isArray(message.people) ? message.people : [];
  paintPeople();
}

function askPeople() {
  if (chatOpen()) send({ t: "people" });
}

/* A ping is only meaningful next to a sense of what is good. These bands are
   the ones that match what the game feels like: under 50ms is indistinguishable
   from sitting on the sofa, 150ms is where a fast game starts to feel late,
   and past 300ms everybody notices. */
function pingBand(rtt) {
  if (rtt == null) return "";
  if (rtt < 50) return "is-good";
  if (rtt < 150) return "is-fair";
  return "is-poor";
}

function saidTime(seconds) {
  if (seconds == null) return "";
  if (seconds < 60) return Math.round(seconds) + "s";
  const mins = Math.round(seconds / 60);
  if (mins < 60) return mins + " min";
  const hours = Math.floor(mins / 60);
  return hours + "h " + (mins % 60) + "m";
}

function paintPeople() {
  const strip = el("chat-people");
  if (!strip) return;
  strip.innerHTML = "";
  people.forEach((person) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chat-person-chip"
      + (person.slot === mySlot ? " is-me" : "")
      + (person.here === false ? " is-away" : "")
      + (person.slot === personOpen ? " is-open" : "");
    chip.setAttribute("role", "tab");
    chip.setAttribute("aria-selected", person.slot === personOpen ? "true" : "false");

    const dot = document.createElement("span");
    dot.className = "chat-person-ping " + pingBand(person.rtt);
    chip.appendChild(dot);
    chip.appendChild(document.createTextNode(
      person.name + (person.slot === mySlot ? " (you)" : "")));

    chip.addEventListener("click", () => {
      personOpen = personOpen === person.slot ? null : person.slot;
      paintPeople();
    });
    strip.appendChild(chip);
  });
  if (!people.length) {
    const empty = document.createElement("span");
    empty.className = "browse-note";
    empty.textContent = "Nobody else is connected.";
    strip.appendChild(empty);
  }
  paintPerson();
}

function paintPerson() {
  const box = el("chat-person");
  if (!box) return;
  const person = people.find((p) => p.slot === personOpen);
  if (!person) { box.hidden = true; box.innerHTML = ""; return; }

  const facts = [];
  // The controller first: it is the thing they can point at, and the question
  // behind "who is on player 2" is nearly always this one.
  facts.push(["Controller",
              person.pad_name || ("Pad " + ((person.pad || 0) + 1))]);
  facts.push(["In the game",
              person.player != null ? "Player " + person.player
                : "not bound to a player yet"]);
  facts.push(["Ping", person.rtt == null ? "not measured yet"
              : person.rtt + " ms"]);
  if (person.loss != null) {
    facts.push(["Picture lost", person.loss.toFixed(1) + "%"]);
  }
  if (person.fps != null) facts.push(["Frame rate", person.fps + " per second"]);
  facts.push(["Connected for", saidTime(person.seconds)]);
  if (person.here === false) {
    facts.push(["Right now", "no picture is reaching them"]);
  }
  if (person.driving) facts.push(["Allowed to", "use the screen directly"]);
  if (person.held) {
    facts.push(["Presses held back",
                person.held + " (the television was in a menu)"]);
  }

  box.innerHTML = "";
  const list = document.createElement("dl");
  list.className = "chat-person-facts";
  facts.forEach(([term, said]) => {
    const dt = document.createElement("dt");
    dt.textContent = term;
    const dd = document.createElement("dd");
    dd.textContent = said;
    list.appendChild(dt);
    list.appendChild(dd);
  });
  box.appendChild(list);
  box.hidden = false;
}


/* ---- another controller on this machine ---------------------------------
 *
 * Two people on one sofa, one screen, two pads. The second of them needs a
 * seat of their own -- their own player port, their own row in the list --
 * and does not need a second copy of the picture they are already looking at.
 *
 * So an extra controller opens a connection of its own that asks for input
 * only. To the host it is an ordinary guest: a seat, a pad, a name, a place
 * in the seat picker. Nothing about the host's model of a session had to
 * learn that a guest can be plural, which is where the bugs would have been.
 *
 * Nothing here joins by itself. A machine with three pads plugged in is
 * usually one person and two spares, so a controller is only seated when
 * somebody says so.
 *
 * Each one is a whole object rather than another branch in the page's
 * globals: the page has one socket, one peer and one sequence number, and
 * teaching all of that to be a list is how a working connection gets broken
 * on behalf of a second one.
 */
let sessionPin = "";
const extras = new Map();          // gamepad index -> ExtraPlayer

function extraFor(index) {
  return extras.get(index) || null;
}

/* Which controllers are attached, and what each one is doing. The list the
   panel draws and the thing the "add" decision is made from. */
function attachedPads() {
  const pads = navigator.getGamepads ? navigator.getGamepads() : [];
  const out = [];
  for (const pad of pads) {
    if (!pad || !pad.connected) continue;
    const extra = extraFor(pad.index);
    out.push({
      index: pad.index,
      name: shortPadName(pad.id),
      mine: pad.index === padIndex,          // drives this page's own seat
      extra,
      seat: pad.index === padIndex ? "you"
            : (extra ? extra.seatName() : null),
    });
  }
  return out;
}

class ExtraPlayer {
  /* One more seat, driven by one more controller on this machine. */
  constructor(index, name) {
    this.index = index;                 // the gamepad, as the browser numbers it
    this.name = name;
    this.socket = null;
    this.pc = null;
    this.input = null;
    this.seq = 0;
    this.slot = null;
    this.label = "";
    this.state = "joining";
    this.error = "";
    this.closed = false;
  }

  seatName() {
    if (this.state === "playing") return this.label || "playing";
    if (this.state === "failed") return this.error || "could not join";
    return "joining\u2026";
  }

  open() {
    const scheme = location.protocol === "https:" ? "wss:" : "ws:";
    this.socket = new WebSocket(`${scheme}//${location.host}/ws`);
    this.socket.addEventListener("open", () => {
      this.socket.send(JSON.stringify({
        t: "join",
        token: linkKey || "",
        pin: sessionPin,
        // Named after the controller rather than the person, because the
        // person is already in the list under their own name and two rows
        // called the same thing help nobody.
        name: (myName() || "Guest") + " " + (extras.size + 1),
        // The whole point: a seat and a pad, and no second encode of a
        // picture that is already on the screen in front of them.
        input: "only",
        codecs: [],
      }));
    });
    this.socket.addEventListener("message", (event) => this.heard(event));
    this.socket.addEventListener("close", () => {
      if (this.closed) return;
      this.state = "failed";
      this.error = "disconnected";
      paintControllers();
    });
    this.socket.addEventListener("error", () => {
      if (this.closed) return;
      this.state = "failed";
      this.error = "could not connect";
      paintControllers();
    });
  }

  async heard(event) {
    let message;
    try { message = JSON.parse(event.data); } catch (_) { return; }
    if (message.t === "joined") {
      this.slot = message.slot;
      this.label = message.label || ("Player " + (message.slot + 1));
      paintControllers();
      return;
    }
    if (message.t === "error") {
      this.state = "failed";
      // The host's own words. "Every player slot is taken" is the answer
      // somebody needs, and inventing a friendlier one would lose it.
      this.error = message.message || "refused";
      paintControllers();
      return;
    }
    if (message.t === "offer") return await this.answer(message);
    if (message.t === "ice" && this.pc) {
      try {
        await this.pc.addIceCandidate({ candidate: message.candidate,
                                        sdpMLineIndex: message.sdpMLineIndex });
      } catch (_) { /* a candidate that arrives late is not news */ }
    }
  }

  async answer(message) {
    if (this.pc) { try { this.pc.close(); } catch (_) {} }
    this.pc = new RTCPeerConnection({ iceServers: [] });
    this.pc.addEventListener("datachannel", (event) => {
      this.input = event.channel;
      this.input.binaryType = "arraybuffer";
      this.input.addEventListener("open", () => {
        this.state = "playing";
        paintControllers();
      });
      this.input.addEventListener("close", () => {
        if (this.closed) return;
        this.state = "failed";
        this.error = "controller offline";
        paintControllers();
      });
    });
    this.pc.addEventListener("icecandidate", (event) => {
      if (event.candidate && this.socket && this.socket.readyState === 1) {
        this.socket.send(JSON.stringify({
          t: "ice",
          candidate: event.candidate.candidate,
          sdpMLineIndex: event.candidate.sdpMLineIndex,
        }));
      }
    });
    try {
      await this.pc.setRemoteDescription({ type: "offer", sdp: message.sdp });
      const reply = await this.pc.createAnswer();
      await this.pc.setLocalDescription(reply);
      if (this.socket && this.socket.readyState === 1) {
        this.socket.send(JSON.stringify({ t: "answer", sdp: reply.sdp }));
      }
    } catch (exc) {
      this.state = "failed";
      this.error = "could not start";
      paintControllers();
    }
  }

  /* One frame, from this controller only. No on-screen pad and no keyboard
     merged in: those belong to the person holding the page, and this seat is
     somebody else. */
  send() {
    if (!this.input || this.input.readyState !== "open") return;
    const pads = navigator.getGamepads ? navigator.getGamepads() : [];
    const pad = pads[this.index];
    if (!pad || !pad.connected) return;
    const state = FPFrame.padState(pad);
    if (this.input.bufferedAmount > BACKLOG_LIMIT) return;
    try {
      this.input.send(FPFrame.buildRaw(state.buttons, state.axes, this.seq,
                                       false));
      this.seq = (this.seq + 1) & 0xffff;
    } catch (_) { /* a closing channel is not news */ }
  }

  close() {
    this.closed = true;
    // Everything down, in the order that leaves nothing behind: the pad first,
    // so the host hears the release rather than inferring it from silence.
    try {
      if (this.input && this.input.readyState === "open") {
        this.input.send(FPFrame.buildRaw(0, [0, 0, 0, 0, 0, 0], this.seq, true));
      }
    } catch (_) {}
    try { if (this.pc) this.pc.close(); } catch (_) {}
    try { if (this.socket) this.socket.close(); } catch (_) {}
  }
}

function addExtra(index) {
  if (extras.has(index) || index === padIndex) return;
  const pads = navigator.getGamepads ? navigator.getGamepads() : [];
  const pad = pads[index];
  if (!pad || !pad.connected) return;
  const player = new ExtraPlayer(index, shortPadName(pad.id));
  extras.set(index, player);
  player.open();
  paintControllers();
  report("seating a second controller: " + player.name);
}

function dropExtra(index) {
  const player = extras.get(index);
  if (!player) return;
  player.close();
  extras.delete(index);
  paintControllers();
}

// Every extra seat's frame, on the same clock as this page's own.
function sendExtras() {
  for (const player of extras.values()) player.send();
}

// A page going away takes its extra seats with it, or the host holds them
// until the dead-man sweep notices.
window.addEventListener("pagehide", () => {
  for (const player of extras.values()) player.close();
});


/* The controllers plugged into this machine, and what each is doing.
 *
 * Written as rows with one button each rather than a picker: "which of these
 * is player three" is a question about a specific controller, and the answer
 * somebody wants to give is "that one". A row per pad, tappable, reads the
 * same on a phone and across a room.
 */
function paintControllers() {
  const box = el("pad-seats");
  if (!box) return;
  const found = attachedPads();
  box.innerHTML = "";

  if (found.length < 2 && !extras.size) {
    // One controller is the ordinary case and needs no list at all: the row
    // above already names it. This appears when there is a choice to make.
    box.hidden = true;
    return;
  }
  box.hidden = false;

  for (const pad of found) {
    const row = document.createElement("div");
    row.className = "pad-seat" + (pad.mine ? " is-mine" : "");

    const name = document.createElement("span");
    name.className = "pad-seat-name";
    name.textContent = pad.name;
    row.appendChild(name);

    const said = document.createElement("span");
    said.className = "pad-seat-state";
    said.textContent = pad.mine ? "yours"
                     : (pad.extra ? pad.extra.seatName() : "not playing");
    row.appendChild(said);

    if (!pad.mine) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "chip pad-seat-do";
      const seated = !!pad.extra;
      button.textContent = seated ? "Remove" : "Add player";
      button.addEventListener("click", () => {
        if (seated) dropExtra(pad.index); else addExtra(pad.index);
      });
      row.appendChild(button);
    }
    box.appendChild(row);
  }
}

/* ---- chat ----------------------------------------------------------------
 *
 * Guests can watch each other play and cannot say a word to each other, which
 * is a strange way to spend an evening together. This is a line of text from
 * whoever is holding a pad to everybody -- the other guests and the room,
 * because the person on the sofa is playing too.
 *
 * Nothing is kept here beyond the session. The page holds what it has been
 * told and forgets it on reload; the host holds the last sixty lines so
 * somebody joining late is not joining blind. A chat that outlives the
 * evening is a different thing to own, and nobody asked for that one.
 */
let chatSeen = 0;               // the newest message id this page has shown
let chatUnread = 0;

function chatOpen() {
  return !el("chat").hidden;
}

function heardChat(message) {
  if (!message || !message.text) return;
  if (message.id && message.id <= chatSeen) return;   // already on the page
  chatSeen = Math.max(chatSeen, message.id || 0);
  const log = el("chat-log");
  const line = document.createElement("p");
  line.className = "chat-line";
  const who = document.createElement("span");
  who.className = "chat-who";
  who.textContent = message.from || "somebody";
  const said = document.createElement("span");
  said.className = "chat-said";
  // textContent, never innerHTML: this is the one place another person's
  // words reach this page, and the host does not escape them because
  // escaping belongs where the medium is known. Here the medium is a DOM.
  said.textContent = message.text;
  line.append(who, said);
  // Whether they were reading the newest line before this arrived. Somebody
  // scrolled back to re-read something should not be yanked to the bottom by
  // a message they have not seen yet.
  const atEnd = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  log.appendChild(line);
  // Only the last hundred stay in the page. A conversation nobody scrolled
  // back through is not worth the memory on a phone.
  while (log.children.length > 100) log.removeChild(log.firstChild);
  if (atEnd) log.scrollTo({ top: log.scrollHeight, behavior: "smooth" });

  if (chatOpen()) return;
  chatUnread += 1;
  paintChatBadge();
  pulseChatDot();
}

/* The dot, told that something just arrived.
 *
 * There was a banner here: the message itself, over the picture, for six
 * seconds. It said more and it was the wrong thing -- somebody is watching a
 * game, and a box that appears over it is an interruption whether or not the
 * message was urgent. The dot is already the answer to "is there anything to
 * read"; all it was missing was a way to say "and one just came".
 *
 * The animation restarts on every message, which is what makes a second one
 * arriving feel different from the first one still sitting there.
 */
function pulseChatDot() {
  const dot = el("chatnew");
  dot.classList.remove("arrived");
  void dot.offsetWidth;          // the reflow that lets it start again
  dot.classList.add("arrived");
}

function paintChatBadge() {
  el("chatnew").hidden = chatUnread === 0;
  // The dot says there is something; the label says how much, because a dot
  // is nothing at all to a screen reader.
  el("chatbtn").setAttribute(
    "aria-label",
    chatUnread === 0 ? "Chat"
      : "Chat, " + chatUnread + (chatUnread === 1 ? " new message"
                                                  : " new messages"));
}

function openChat() {
  el("chat").hidden = false;
  chatUnread = 0;
  paintChatBadge();
  // Everything said before this page was looking. Asked for rather than
  // pushed, so a guest who joins an hour in gets the conversation and a guest
  // who never opens chat is never sent it.
  send({ t: "chatlog", since: chatSeen });
  // Who is here, and then again while they are looking. The host pushes this
  // when somebody joins, leaves or changes seat, but a ping that got worse
  // changes nothing the host can see -- so the panel that is showing it asks.
  askPeople();
  clearInterval(peopleTimer);
  peopleTimer = setInterval(askPeople, PEOPLE_EVERY_MS);
  const box = el("chat-text");
  // Not focused on a phone: focus opens the keyboard over the game, and
  // somebody opening chat to *read* it did not ask for that.
  if (window.matchMedia && window.matchMedia("(pointer: fine)").matches) {
    box.focus({ preventScroll: true });
  }
  el("chat-log").scrollTop = el("chat-log").scrollHeight;
}

function closeChat() {
  el("chat").hidden = true;
  el("chat-text").blur();
  // Nothing is drawing them any more, so stop asking.
  clearInterval(peopleTimer);
  peopleTimer = 0;
}

el("chatbtn").addEventListener("click", () => {
  if (chatOpen()) closeChat(); else openChat();
});
el("chat-close").addEventListener("click", closeChat);
el("chat-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const box = el("chat-text");
  const text = box.value.trim();
  if (!text) return;
  send({ t: "chat", message: text });
  box.value = "";
  // Focus is kept deliberately: letting the field blur closes the keyboard,
  // and a keyboard that shuts after every line makes a conversation feel like
  // a form. It closes when the panel does, or when somebody taps away.
  box.focus({ preventScroll: true });
  // Not echoed locally: it comes back from the host with everybody else's,
  // in the order the host put them in, which is the order everyone else sees.
  // A page that draws its own first shows a different conversation from the
  // one being had.
});

/* ---- chrome ---- */

function setChip(id, text, kind) {
  const chip = el(id);
  chip.textContent = text;
  chip.className = "chip" + (kind ? " " + kind : "");
  chip.hidden = false;              // something to say means something to show
}

/* The connection chip is a light, not a sentence.
 *
 * "Connected" spelled out is a word a guest has to read and finish before it
 * tells them the one thing they wanted, which is whether they are on. A lit
 * pip says it without being read at all. The words stay for anything that is
 * not simply on or off -- "no H.264" is the reason somebody has no picture and
 * throwing it away to save four characters would be a bad trade. */
const LINK_WORDS = { ok: "Connected", warn: "Reconnecting",
                     bad: "Not connected", "": "Connecting" };

function setLink(kind, detail) {
  const chip = el("link");
  chip.className = "chip link" + (kind ? " " + kind : "");
  chip.textContent = detail || "";
  // Still announced, and still there on a long press: a pip is not readable
  // by a screen reader and not obvious to somebody seeing it for the first
  // time.
  const words = detail || LINK_WORDS[kind] || LINK_WORDS.bad;
  chip.setAttribute("aria-label", words);
  chip.title = words;
  chip.hidden = false;
  // The button belongs to exactly the states this pip is not happy in. A
  // session that has ended is not one of them: nothing is coming back.
  const button = el("revive");
  if (button) button.hidden = ended || kind === "ok";
  // A collapsed hud is a hamburger and nothing else, so a button nobody can
  // see is no better than the button that was not there. "warn" is not enough
  // reason to open it -- a connection wobbles and mends itself all the time,
  // and a hud that opens itself every time is its own annoyance -- but "bad"
  // is the state somebody has to be given something to do about.
  if (kind === "bad" && !ended && gate.hidden) showHud(true);
}

function timeLeft(seconds) {
  const m = Math.floor(seconds / 60), s = Math.floor(seconds % 60);
  return m + ":" + String(s).padStart(2, "0");
}

let clockTimer = null;
function startClock(seconds) {
  if (clockTimer) clearInterval(clockTimer);
  // null is a session with no deadline -- not zero, and not an error. A chip
  // reading "no time limit" is a line of screen furniture that never changes
  // and never will; the absence of a clock says the same thing more quietly.
  if (seconds === null || seconds === undefined) {
    el("clock").hidden = true;
    return;
  }
  let left = seconds;
  const paint = () => {
    setChip("clock", timeLeft(Math.max(0, left)) + " left",
            left < 300 ? "warn" : "");
    if (left <= 0) setChip("clock", "session ended", "bad");
    left -= 1;
  };
  paint();
  clockTimer = setInterval(paint, 1000);
}

el("full").addEventListener("click", () => {
  if (document.fullscreenElement || document.webkitFullscreenElement) {
    (document.exitFullscreen || document.webkitExitFullscreen).call(document);
    return;
  }
  if (immersive) { toggleImmersive(false); return; }
  // iOS Safari has no Fullscreen API for ordinary elements -- only a video can
  // go fullscreen, and only through its own webkit call. Without this the
  // button did nothing at all on a phone, which is where it is most wanted.
  const request = stage.requestFullscreen || stage.webkitRequestFullscreen;
  if (request) {
    request.call(stage).catch(() => enterVideoFullscreen());
  } else {
    enterVideoFullscreen();
  }
});

/* Fullscreen where the page keeps running.

   Native video fullscreen -- webkitEnterFullscreen, the only kind iOS Safari
   offers a page that is not a video -- hands the picture to the system player.
   The page behind it is no longer the thing on screen, and a page that is not
   on screen does not reliably get gamepad readings or timers, so the
   controller stops working exactly when someone has made the picture as big as
   possible. It also covers the on-screen pad, which was already a known cost.

   So this is the fallback instead: strip the page back to the picture, which
   is most of what fullscreen was wanted for, and keep the page alive and
   holding the controller. For an actually chrome-free screen on iOS, the
   answer is Add to Home Screen -- the manifest and meta tags are there for it. */
let immersive = false;

function toggleImmersive(on) {
  const wanted = on === undefined ? !immersive : on;
  if (!wanted) { showHud(); return; }          // showHud clears the class
  immersive = true;
  stage.classList.add("immersive");
  el("full").setAttribute("aria-label", "Leave fullscreen");
  hideHud();
  fitStage();
}

function enterVideoFullscreen() {
  toggleImmersive();
  if (immersive) {
    showNotice('<p class="footnote">Tap the screen to bring the buttons back. '
               + "For a proper fullscreen with no address bar, add this page to "
               + "your home screen.</p>", false);
  }
}

/* Keep the stage inside the part of the window you can actually see.
   Safari's address bar and tab strip shrink the visual viewport without
   shrinking the layout viewport that `position: fixed` is measured against,
   so anything anchored to the bottom -- select and start -- gets laid out
   behind them. visualViewport reports what is really on screen, including
   while the bars slide in and out and while the page is pinch-zoomed. */
/* Whether the page is pinch-zoomed, said out loud once.
 *
 * A zoomed page looks like a broken layout and is not one: everything sized
 * in rem -- the chips, the buttons -- is drawn larger, and the picture becomes
 * a window onto a page wider than the screen, so the pad appears to hang off
 * the right. The two are indistinguishable from a description, and this is
 * the number that tells them apart. Reported when it changes rather than
 * continuously, because it changes while a pinch is happening. */
let zoomTold = 0;

function noteZoom(vv) {
  const scale = Math.round((vv.scale || 1) * 100) / 100;
  if (scale === zoomTold) return;
  zoomTold = scale;
  if (scale === 1) return;               // back to normal is not worth a line
  report("the page is zoomed to " + Math.round(scale * 100) + "%: "
         + Math.round(vv.width) + "x" + Math.round(vv.height)
         + " of the layout is showing. Everything will look too big and the "
         + "pad will sit off the right until it is pinched back to 100%.");
}

function fitStage() {
  const vv = window.visualViewport;
  if (!vv) return;                       // the dvh fallback in the CSS applies
  noteZoom(vv);
  const style = document.documentElement.style;
  style.setProperty("--vv-height", vv.height + "px");
  style.setProperty("--vv-width", vv.width + "px");
  style.setProperty("--vv-top", vv.offsetTop + "px");
  style.setProperty("--vv-left", vv.offsetLeft + "px");
}

/* There was a fitGutter() here, measuring the black bars beside the picture so
   the d-pad and face buttons could sit in them -- off the game, but no further
   out than necessary. It is gone because the controls no longer fit in those
   bars: on a phone held sideways the bar is a thumb's width if you are lucky,
   and sizing the controls by it made them too small to use. They lie over the
   picture now and are faint enough to see through, which buys the size the
   hand actually needs. Nothing reads --gutter any more. */

if (window.visualViewport) {
  let pending = false;
  const schedule = () => {
    // resize and scroll both fire in bursts while the bars animate.
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => { pending = false; fitStage(); });
  };
  window.visualViewport.addEventListener("resize", schedule);
  window.visualViewport.addEventListener("scroll", schedule);
  /* The keyboard coming up is a viewport resize like any other, and it is the
     one that shows. Two things make it rough rather than smooth: the last
     message slides up behind the keyboard because the log keeps its scroll
     offset while the box around it shrinks, and iOS scrolls the *window* to
     reveal a focused field even though everything here is fixed, which drags
     the whole stage by a few pixels and then drags it back.

     So the log is pinned to the end while the panel is open, and the stray
     window scroll is put back to zero. Both on the same frame as the resize,
     which is the frame the keyboard is drawn on. */
  window.visualViewport.addEventListener("resize", () => {
    if (el("chat").hidden) return;
    requestAnimationFrame(() => {
      if (window.scrollY || window.scrollX) window.scrollTo(0, 0);
      const log = el("chat-log");
      log.scrollTop = log.scrollHeight;
    });
  });
  window.addEventListener("orientationchange", () => setTimeout(fitStage, 200));
  fitStage();
}

/* ---- what your controller is doing ---- */

/* Every pad reports a different layout, and browsers guess at the ones they do
   not know. A guest holding such a pad finds out only by pressing things and
   watching a television in somebody else's house do the wrong thing -- and the
   picker's own button tester is on that television, which is no use to them.
   So: the same idea, on their screen.

   Names are the W3C standard mapping's, which is exactly what gets sent: bit N
   of the frame is buttons[N] here. What the host calls them is underneath. */
const STANDARD_KEYS = [
  ["A", "bottom face"], ["B", "right face"], ["X", "left face"], ["Y", "top face"],
  ["LB", "left bumper"], ["RB", "right bumper"],
  ["LT", "left trigger"], ["RT", "right trigger"],
  ["BACK", "select"], ["START", "start"],
  ["L3", "left stick in"], ["R3", "right stick in"],
  ["UP", "d-pad"], ["DOWN", "d-pad"], ["LEFT", "d-pad"], ["RIGHT", "d-pad"],
  ["GUIDE", "home"],
];
const AXIS_NAMES = ["LEFT X", "LEFT Y", "RIGHT X", "RIGHT Y"];

/* Which key stands for which button.
 *
 * The arrangement is RetroArch's own, because this console is RetroArch and
 * anybody who has played an emulator on a keyboard already has it in their
 * hands: arrows for the d-pad, Z and X on the two buttons a two-button game
 * uses, A and S above them, Enter for start and the right shift key for
 * select. The shoulders and triggers take the row above the letters, in the
 * order they sit on a controller.
 *
 * The sticks are not here and cannot be: a key is down or it is not, and a
 * stick is a position. A game that needs one needs a controller.
 *
 * Codes rather than characters, so the map does not change meaning on a French
 * or German keyboard: KeyZ is the key where Z is on a US layout, whatever is
 * printed on it. */
const KEY_STORE = "fp-keys";
const KEY_DEFAULTS = STANDARD_KEYS.map(() => null);
[[0, "KeyZ"], [1, "KeyX"], [2, "KeyA"], [3, "KeyS"],
 [4, "KeyQ"], [5, "KeyW"], [6, "KeyE"], [7, "KeyR"],
 [8, "ShiftRight"], [9, "Enter"],
 [12, "ArrowUp"], [13, "ArrowDown"], [14, "ArrowLeft"], [15, "ArrowRight"],
].forEach(([index, code]) => { KEY_DEFAULTS[index] = code; });

let keyMap = KEY_DEFAULTS.slice();

function loadKeyMap() {
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem(KEY_STORE) || "null"); } catch (_) {}
  keyMap = saved
    ? STANDARD_KEYS.map((_n, i) => (typeof saved[i] === "string" ? saved[i] : null))
    : KEY_DEFAULTS.slice();
}

function saveKeyMap() {
  try { localStorage.setItem(KEY_STORE, JSON.stringify(keyMap)); } catch (_) {}
}

function keysAreDefault() {
  return KEY_DEFAULTS.every((code, i) => keyMap[i] === code);
}

/* What to call a key on screen. `event.code` names a position -- "KeyZ",
   "ArrowUp", "ShiftRight" -- and none of those is what somebody would say out
   loud about the key under their finger. */
function keyLabel(code) {
  if (!code) return "\u2014";
  const named = {
    ArrowUp: "\u2191", ArrowDown: "\u2193",
    ArrowLeft: "\u2190", ArrowRight: "\u2192",
    Enter: "Enter", NumpadEnter: "Enter", Space: "Space", Tab: "Tab",
    Escape: "Esc", Backspace: "Backspace", CapsLock: "Caps",
    ShiftLeft: "L Shift", ShiftRight: "R Shift",
    ControlLeft: "L Ctrl", ControlRight: "R Ctrl",
    AltLeft: "L Alt", AltRight: "R Alt",
    Backquote: "`", Minus: "-", Equal: "=", BracketLeft: "[",
    BracketRight: "]", Backslash: "\\", Semicolon: ";", Quote: "'",
    Comma: ",", Period: ".", Slash: "/",
  };
  if (named[code]) return named[code];
  if (/^Key[A-Z]$/.test(code)) return code.slice(3);
  if (/^Digit[0-9]$/.test(code)) return code.slice(5);
  if (/^Numpad[0-9]$/.test(code)) return "Num " + code.slice(6);
  return code;
}

/* Giving one button a key. Whatever had that key takes the one being given up,
   so the map stays a swap rather than growing a duplicate -- exactly as the
   controller's own map does, and for the same reason: two buttons on one key
   means one of them can never be pressed by itself. */
function bindKeyInto(map, index, code) {
  const next = map.slice();
  const was = next[index];
  const clash = next.findIndex((held, i) => i !== index && held === code);
  if (clash >= 0) next[clash] = was;
  next[index] = code;
  return { map: next, clash };
}

/* Somebody typing into the PIN box, the name box or the game search is typing,
   whatever the controls are set to. */
function typingSomewhere() {
  const node = document.activeElement;
  if (!node) return false;
  const tag = node.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT"
      || node.isContentEditable === true;
}

function buttonForKey(code) {
  const index = keyMap.indexOf(code);
  return index >= 0 ? index : -1;
}

window.addEventListener("keydown", (event) => {
  if (typingSomewhere()) return;
  // While one button is waiting to be given a key, the next key pressed is the
  // answer to that question rather than a move in the game.
  if (keyboardOn && learnTarget >= 0) {
    event.preventDefault();
    const target = learnTarget;
    cancelLearn();
    bindKey(target, event.code);
    return;
  }
  if (!keyboardOn || !gate.hidden || event.repeat) return;
  const index = buttonForKey(event.code);
  if (index < 0) return;
  // Arrows scroll a page and space presses whatever has focus. Neither is what
  // somebody holding the d-pad down meant.
  event.preventDefault();
  keyButtons |= (1 << index);
});

// Not conditional on the keyboard being the chosen controls: a key let go of
// after they were turned off would otherwise stay down for ever.
window.addEventListener("keyup", (event) => {
  const index = buttonForKey(event.code);
  if (index >= 0) keyButtons &= ~(1 << index);
});

/* A window that loses focus stops sending key-ups, so whatever was held stays
   held -- which on a television in another house is a character walking into a
   wall until somebody notices. */
window.addEventListener("blur", () => { keyButtons = 0; });

loadKeyMap();
// Which physical button answers for each standard one. null means "as the
// browser reports it", which is right for every pad it knows.
let padMap = null;
let sticksSwapped = false;
let padsOpen = false, padsFrame = null, remapStep = -1;
// Whether everything has been let go of since the last button was learned.
let remapArmed = true;

function mapKey() {
  return "fp-padmap:" + (padName || "pad");
}

/* Kept apart from the button map, and keyed the same way, because it is a
   different question about the same controller: which stick is which, rather
   than which button is which. Somebody who has fixed their buttons should not
   lose that by swapping their sticks, or the other way about. */
function sticksKey() {
  return "fp-sticks:" + (padName || "pad");
}

function loadPadMap() {
  try {
    const raw = localStorage.getItem(mapKey());
    padMap = raw ? JSON.parse(raw) : null;
  } catch (_) { padMap = null; }
  try {
    sticksSwapped = localStorage.getItem(sticksKey()) === "1";
  } catch (_) { sticksSwapped = false; }
  let tune = null;
  try { tune = JSON.parse(localStorage.getItem(tuneKey()) || "null"); } catch (_) {}
  deadzone = tune && typeof tune.deadzone === "number" ? tune.deadzone : 0.10;
  sensitivity = tune && typeof tune.sensitivity === "number"
    ? tune.sensitivity : 1.0;
  paintSticks();
  paintTune();
  const reset = el("pads-reset");
  if (reset) reset.hidden = !padMap && !sticksSwapped;
}

function paintSticks() {
  const button = el("pads-sticks");
  if (!button) return;
  button.textContent = sticksSwapped ? "Sticks swapped" : "Swap sticks";
  button.setAttribute("aria-pressed", sticksSwapped ? "true" : "false");
  button.classList.toggle("on", sticksSwapped);
}

/* The two sticks, exchanged. Standard mapping puts the left stick on axes 0
   and 1 and the right on 2 and 3, so this is those two pairs traded -- and
   only when there are four axes to trade, because plenty of pads report
   fewer and half a swap would be worse than none. */
/* How a stick is shaped on the way out: how much of the middle to ignore, and
   how quickly it reaches full tilt. Both belong to the guest -- a stick that
   will not sit still is a fact about their controller, not about the game --
   so nothing here is sent anywhere. */
let deadzone = 0.10, sensitivity = 1.0;

function tuneKey() {
  return "fp-axistune:" + (padName || "pad");
}

/* Applied to a stick as a whole rather than to each axis on its own.
   Per-axis is the easy version and it is wrong in a way people feel: it
   carves a cross out of the middle, so a stick pushed diagonally answers
   while the same stick pushed straight up does not. Measuring the distance
   from the centre carves a circle, which is what a thumb expects.

   Past the edge of the dead zone the value is stretched back out to the full
   range, so the first movement that registers is a small one -- without that,
   a tenth of dead zone makes the stick jump to a tenth as soon as it wakes. */
function shapeStick(x, y) {
  const magnitude = Math.hypot(x, y);
  if (magnitude <= deadzone) return [0, 0];
  const live = Math.min(1, (magnitude - deadzone) / (1 - deadzone) * sensitivity);
  return [x / magnitude * live, y / magnitude * live];
}

function shapeAxes(axes) {
  if (!axes || axes.length < 2) return axes;
  if (deadzone === 0 && sensitivity === 1) return axes;
  const out = axes.slice();
  for (let i = 0; i + 1 < out.length; i += 2) {
    const [x, y] = shapeStick(out[i], out[i + 1]);
    out[i] = x;
    out[i + 1] = y;
  }
  return out;
}

function swapSticks(axes) {
  if (!sticksSwapped || !axes || axes.length < 4) return axes;
  const out = axes.slice();
  out[0] = axes[2]; out[1] = axes[3];
  out[2] = axes[0]; out[3] = axes[1];
  return out;
}

/* The pad as the host should see it. Built fresh each time rather than mutated:
   the browser's Gamepad objects are snapshots and must not be written to. */
function remapped(pad) {
  if (!pad) return pad;
  // Not `!padMap` any more: a controller whose buttons are all correct can
  // still want its sticks the other way round, and returning early here meant
  // it could not have that without first breaking its buttons.
  if (!padMap && !sticksSwapped && !faceSwapped()
      && deadzone === 0 && sensitivity === 1) {
    return pad;
  }
  let buttons = !padMap ? pad.buttons : STANDARD_KEYS.map((_n, i) => {
    const from = padMap[i];
    return (from == null || !pad.buttons[from])
      ? { pressed: false, value: 0 } : pad.buttons[from];
  });
  /* The same swap the on-screen pad gets, from the same switch.
   *
   * There used to be a button up in the panel's bar that did this by
   * rewriting the controller's own map -- a second control, doing what looks
   * from the sofa like the same job, in a different place and by a different
   * mechanism. One switch now, applied here instead of written into the map,
   * which keeps it out of "Fix my buttons": somebody who has taught this page
   * where their controller's buttons are can still trade the pairs over
   * without disturbing any of that, and can trade them back. */
  if (faceSwapped()) {
    const traded = buttons.slice();
    for (const [from, to] of Object.entries(FACE_SWAP)) {
      traded[to] = buttons[from] || { pressed: false, value: 0 };
    }
    buttons = traded;
  }
  return { buttons, axes: shapeAxes(swapSticks(pad.axes)), id: pad.id,
           index: pad.index, connected: pad.connected, mapping: pad.mapping };
}

/* Which player you are, changed while a game is running.

   A game that is already going has bound its player ports to devices and will
   not revisit that until it restarts, so somebody who joins halfway through --
   or a second person arriving after one player claimed -- had no way to be
   given controls short of stopping the game. Moving onto the pad that is
   already player 2 does it instantly, because that pad is already player 2. */
let myPad = 0, padSeats = { count: 0, who: {}, ports: {}, playing: false };
// Whether what is on screen is known to be out of date, because the question
// could not be sent. Never guessed at: it is set when a request is skipped and
// cleared by the answer.
let padsStale = false;

/* The player number is the game's, not this pad's position. These used to be
   counted off as "Player 2" upwards on the assumption that somebody at the
   television is player 1 -- and on the machine this was written for that was
   simply untrue: the pad the host had given to a guest WAS player 1, and the
   other three ports were bound to nothing at all. So a guest was told they
   were player 3 while the game called them player 1, and was offered two more
   seats that did not exist. The host tells us the real mapping now. */
function paintSeats() {
  const pick = el("pads-seat");
  const wanted = padSeats.count || 0;
  const port = (i) => padSeats.ports[String(i)];
  const label = (i) => {
    const who = padSeats.who[String(i)];
    // No port means the game was started without that seat. Saying so is the
    // whole point: it cannot be taken until the game is started again.
    // Who is on it, always -- including the seat you are on. It used to name
    // everybody except you, which is fine when a pad holds one person and
    // useless once two can share one: the seat you most need to see the
    // company on is your own.
    const seat = (port(i) ? "Player " + port(i)
                          : "Controller " + (i + 1) + " (not in this game)")
               + (i === myPad ? " (you)" : "");
    return who ? seat + " — " + who : seat;
  };
  // Rebuilt only when something changed, or a menu open on a phone closes
  // itself underneath the person using it.
  //
  // The player numbers belong in this key. They are half of what each option
  // says, and they are the half that arrives late: a guest who is already here
  // when a game starts sees ports go from nothing to a real mapping while the
  // count, the seat and the names all stay put -- so the key matched, the list
  // was left alone, and every option went on reading "not in this game" for a
  // player who was holding a pad that worked. Reloading the page fixed it,
  // which is the shape of a cache that is asked the wrong question.
  const signature = wanted + "|" + myPad + "|" + JSON.stringify(padSeats.who)
                  + "|" + JSON.stringify(padSeats.ports);
  if (pick.dataset.signature !== signature) {
    pick.dataset.signature = signature;
    pick.innerHTML = "";
    for (let i = 0; i < wanted; i++) {
      const option = document.createElement("option");
      option.value = String(i);
      option.textContent = label(i);
      pick.appendChild(option);
    }
    pick.value = String(myPad);
  }
  const mine = padSeats.ports[String(myPad)];
  const note = el("pads-seat-note");
  const known = Object.keys(padSeats.ports).length;
  // Offered whenever a game is running. It used to key off whether the player
  // numbers were known, which meant that the one time it was most wanted --
  // a game running whose players could not be read -- it was hidden.
  el("pads-repick").hidden = !padSeats.playing;
  // Its explanation goes with it, rather than sitting under a button that is
  // not there.
  if (el("pads-repick-note")) el("pads-repick-note").hidden = !padSeats.playing;
  if (padsStale) {
    note.hidden = false;
    note.textContent = "Not connected to the host just now, so which "
                     + "controller is which player cannot be read. The game "
                     + "keeps playing; this will catch up on its own.";
  } else if (mine) {
    note.hidden = true;
  } else if (!padSeats.playing) {
    note.hidden = false;
    note.textContent = "No game is running, so there are no players to be yet.";
  } else if (known) {
    note.hidden = false;
    note.textContent = "This controller is not one of the game's players.";
  } else {
    // A game is running and which pad is which player could not be read. Say
    // that, rather than "no game is running" -- which was the message, and
    // was flatly untrue, for as long as the host could not reach the file.
    note.hidden = false;
    note.textContent = "A game is running, but which controller is which "
                     + "player could not be read.";
  }
}

/* One question eight seconds after a game starts was a guess at how long a
 * game takes to come up. A cartridge is ready in that time; a GameCube disc
 * through Dolphin is not, so the answer came back saying the ports were not
 * known yet and nothing ever asked again. Ask a few times instead, spread out,
 * and stop as soon as there is a real answer -- or after about a minute, which
 * is long enough that a game still not up is not one this can wait for. */
const SEAT_ASKS = [4000, 9000, 18000, 32000, 60000];
let seatAsks = [];

function askSeatsUntilKnown() {
  seatAsks.forEach(clearTimeout);
  seatAsks = SEAT_ASKS.map((delay) => setTimeout(() => {
    if (Object.keys(padSeats.ports).length) return;   // already answered
    send({ t: "pads" });
  }, delay));
}

function seatsFrom(message) {
  if (!message) return;
  padsStale = false;
  if (typeof message.yours === "number") myPad = message.yours;
  padSeats = { count: message.count || 0, who: message.who || {},
               ports: message.ports || {}, playing: !!message.playing };
  paintSeats();
  paintEndGame();          // whether there is a game to end has just changed
}

el("pads-seat").addEventListener("change", (ev) => {
  const wanted = parseInt(ev.target.value, 10);
  if (!isNaN(wanted)) send({ t: "usepad", pad: wanted });
});

/* Moving between the seats a game already has is instant. Asking for a seat it
   does not have is not, and cannot be: the ports are fixed when the game
   starts. This asks the television to bring the picker back up over the game,
   which closes it, keeps it, and puts it back where it was.
 *
 * Asked first, because it is not a private action: the game stops on the
 * television and everybody playing has to choose a slot again. A button that
 * did that on one tap, from a phone, with a label that did not say so, was a
 * trap. */
el("pads-repick").addEventListener("click", () => {
  el("repick-ask").hidden = false;
});

el("repick-no").addEventListener("click", () => {
  el("repick-ask").hidden = true;
});

el("repick-yes").addEventListener("click", () => {
  el("repick-ask").hidden = true;
  send({ t: "repick" });
  closePads();
});

function signallingUp() {
  return socket !== null && socket.readyState === WebSocket.OPEN;
}

function openPads() {
  el("repick-ask").hidden = true;
  mirrorPicker();
  // Ask what the seats look like now rather than trusting what they looked
  // like when this page joined. A guest who was already here when the game
  // started had been told, and kept being told, that no game was running.
  //
  // The picture survives signalling going down -- that is deliberate, and it
  // is why a game keeps playing through a blip. But this question travels on
  // signalling, so while it is down the answer never arrives and the panel
  // went on showing what it knew before: a player being told no controller is
  // in the game, while they are playing with one. Say what is actually true,
  // and stop waiting out the backoff, since somebody is now looking at it.
  padsStale = !signallingUp();
  if (padsStale) {
    retries = 0;
    reconnectSoon();
    paintSeats();
  } else {
    send({ t: "pads" });
  }
  // Let go of everything on the way in, so a button held as the panel opens is
  // not left held down in the game behind it.
  sendFrame(null, true);
  padsOpen = true;
  el("pads").hidden = false;
  el("prompt").hidden = true;          // it shows through, and says the same
  loadPadMap();
  buildPadsGrid();
  paintKeyMode();
  paintBuzz();
  paintStrength();
  paintControllers();
  paintFaceSwap();
  paintOrient();
  paintPads();
}

function closePads() {
  cancelLearn();
  padsOpen = false;
  remapStep = -1;
  el("pads").hidden = true;
  // Only worth saying when there is no other way to play. With a controller
  // attached it is wrong, and with the on-screen pad up it is noise sitting
  // over the picture -- which is what closing this panel used to put back.
  if (padIndex === null && !touchOn && !keyboardOn) el("prompt").hidden = false;
  if (padsFrame) { cancelAnimationFrame(padsFrame); padsFrame = null; }
}

function buildPadsGrid() {
  const grid = el("pads-grid");
  grid.innerHTML = "";
  STANDARD_KEYS.forEach(([name, note], i) => {
    const cell = document.createElement("div");
    cell.className = "key";
    cell.id = "key" + i;
    cell.innerHTML = '<span class="key-name">' + name + "</span>"
                   + '<span class="key-note">' + note + "</span>"
                   + '<span class="key-bind" id="bind' + i + '"></span>';
    cell.addEventListener("click", () => startLearn(i));
    grid.appendChild(cell);
  });
  const axes = el("pads-axes");
  axes.innerHTML = "";
  AXIS_NAMES.forEach((name, i) => {
    const row = document.createElement("div");
    row.className = "axis";
    row.innerHTML = '<span class="axis-name">' + name + "</span>"
      + '<span class="axis-track"><span class="axis-fill" id="axis' + i
      + '"></span></span>';
    axes.appendChild(row);
  });
}

/* The pad as it is right now, for a send that is not waiting for the timer.
 *
 * tick() does more than this -- it notices a controller arriving or leaving --
 * and that part belongs on a timer. Reading the buttons does not.
 *
 * There were two of these, both at the top level, and the second silently
 * replaced the first: the strict one that was commented and the one that
 * falls back to any connected pad, which is the one that has actually been
 * running. Kept as it runs, with the explanation the other one had.
 *
 * That fallback is deliberate for one controller and wrong for two: it means
 * a seat will take whatever pad is connected rather than the pad it was given.
 * Anything that lets one page drive two seats has to start here. */
function livePad() {
  const pads = navigator.getGamepads ? navigator.getGamepads() : [];
  let pad = padIndex !== null ? pads[padIndex] : null;
  if (pad && !pad.connected) pad = null;
  return pad || firstFreePad(pads);
}

/* A connected controller that is not already somebody else's seat.
 *
 * The fallback used to be "any connected pad", which is right while there is
 * only ever one. Once a second controller can be seated as its own player,
 * that fallback would quietly merge their buttons into this page's frame:
 * two people pressing one player. */
function firstFreePad(pads) {
  return Array.from(pads).find(
    (p) => p && p.connected && !extras.has(p.index)) || null;
}

function paintPads() {
  if (!padsOpen) return;
  const raw = livePad();
  const pad = remapped(raw);
  setChip("pads-name",
          keyboardOn ? "keyboard" : (padName || (raw ? "controller" : "no controller")),
          (raw || keyboardOn) ? "ok" : "warn");

  if (learnTarget >= 0 && raw && !keyboardOn) {
    /* The same discipline as the full walk: a press only counts once
       everything has been let go of since the last one. Without it the click
       that started this, or a button still held from the previous binding,
       answers instantly. */
    const hit = raw.buttons.findIndex((b) => b && b.pressed);
    if (hit < 0) {
      remapArmed = true;
    } else if (remapArmed) {
      const target = learnTarget;
      cancelLearn();
      remapArmed = false;
      bindOne(target, hit);
    }
  } else if (remapStep >= 0 && raw) {
    /* One press, one button. This ran every frame while a button was still
       down, so a single press of A -- held for a tenth of a second, which is
       sixty frames -- answered all ten prompts with A and left every other
       face button doing nothing. Reported exactly that way.

       So a press only counts once everything has been let go of since the last
       one, and a button already spoken for is refused rather than quietly
       taking a second job. */
    const hit = raw.buttons.findIndex((b) => b && b.pressed);
    const next = learnPress({ map: padMap, step: remapStep, armed: remapArmed },
                            hit);
    padMap = next.map;
    remapStep = next.step;
    remapArmed = next.armed;
    if (next.step >= REMAP_ORDER.length) {
      finishRemap();
    } else if (next.said) {
      el("pads-hint").textContent = next.said;
    }
  } else {
    STANDARD_KEYS.forEach((_n, i) => {
      const cell = el("key" + i);
      if (!cell) return;
      const held = (pad && pad.buttons[i] && pad.buttons[i].pressed)
                || !!(keyButtons & (1 << i));
      cell.classList.toggle("on", held);
    });
    AXIS_NAMES.forEach((_n, i) => {
      const fill = el("axis" + i);
      if (!fill) return;
      const v = Math.max(-1, Math.min(1, (pad && pad.axes[i]) || 0));
      // Centre is the middle of the track; a stick pushed left fills left.
      fill.style.left = (v < 0 ? 50 + v * 50 : 50) + "%";
      fill.style.width = Math.abs(v) * 50 + "%";
    });
  }
  padsFrame = requestAnimationFrame(paintPads);
}

/* Learning a pad. Only the buttons somebody can name are asked for: the sticks
   are axes and are not remapped, and a pad that reports its d-pad as a hat has
   nothing to press for those either. */
const REMAP_ORDER = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9];

/* One step of learning a pad, as a decision rather than a side effect, so the
   rule can be checked without a browser and a controller.

   `hit` is the index of the first button held, or -1 for none. A press only
   counts once everything has been let go of since the last one: this ran on
   every frame, so a single press of A -- held for a tenth of a second, which
   is sixty frames -- answered all ten prompts with A and left every other face
   button doing nothing. A button already spoken for is refused rather than
   quietly taking a second job. */
function learnPress(state, hit) {
  const map = state.map.slice();
  if (hit < 0) return { map, step: state.step, armed: true, said: null };
  if (!state.armed) return { map, step: state.step, armed: false, said: null };
  const already = map.indexOf(hit);
  if (already >= 0) {
    return { map, step: state.step, armed: false,
             said: "That one is already " + STANDARD_KEYS[already][0]
                   + ". Use a different button, or start again." };
  }
  map[REMAP_ORDER[state.step]] = hit;
  const step = state.step + 1;
  return { map, step, armed: false,
           said: step < REMAP_ORDER.length ? promptFor(step) : null };
}

function promptFor(step) {
  const [name, note] = STANDARD_KEYS[REMAP_ORDER[step]];
  return "Press the button you use for " + name + "  (" + note + ")"
    + "   \u2014   " + (step + 1) + " of " + REMAP_ORDER.length;
}

function remapPrompt() {
  return promptFor(remapStep);
}

function startRemap() {
  if (!livePad()) {
    el("pads-hint").textContent =
      "Press a button on your controller first, so the browser can see it.";
    return;
  }
  padMap = STANDARD_KEYS.map(() => null);
  // Let go of anything held before the first prompt, or a button that was down
  // when this started stays down for the whole walk through.
  sendFrame(null, true);
  remapStep = 0;
  remapArmed = false;          // the button that opened this may still be down
  el("pads-hint").textContent = remapPrompt();
}

function finishRemap() {
  remapStep = -1;
  // Anything not asked about keeps the browser's own idea of it.
  padMap = padMap.map((from, i) => (from == null ? i : from));
  try { localStorage.setItem(mapKey(), JSON.stringify(padMap)); } catch (_) {}
  el("pads-reset").hidden = false;
  el("pads-hint").textContent =
    "Saved. Press anything to check it — nothing here reaches the game.";
  report("remapped a controller");
}

/* One tap for the commonest confusion there is.

   The host has one virtual pad per guest and it can only be lettered one way,
   so it is lettered to match the on-screen controller: A on the right, the
   Nintendo arrangement. A guest holding an Xbox or PlayStation pad has A on
   the bottom, and the two are then the other way round for them -- their A
   reads as B on the television. Nothing can tell the two apart from the host's
   side, so the person who can see the pad fixes it, in one tap. */
function paintTune() {
  el("pads-deadzone").value = String(Math.round(deadzone * 100));
  el("pads-sens").value = String(Math.round(sensitivity * 100));
  el("pads-deadzone-value").textContent = Math.round(deadzone * 100) + "%";
  el("pads-sens-value").textContent = Math.round(sensitivity * 100) + "%";
  el("pads-reset").hidden = !padMap && !sticksSwapped
    && deadzone === 0.10 && sensitivity === 1.0;
}

function saveTune() {
  try {
    localStorage.setItem(tuneKey(),
                         JSON.stringify({ deadzone, sensitivity }));
  } catch (_) {}
  paintTune();
}

el("pads-deadzone").addEventListener("input", () => {
  deadzone = Number(el("pads-deadzone").value) / 100;
  saveTune();
});

el("pads-sens").addEventListener("input", () => {
  sensitivity = Number(el("pads-sens").value) / 100;
  saveTune();
});

/* Rebinding one button rather than all ten.
 *
 * "Fix my buttons" walks the whole set, which is right the first time and
 * heavy-handed when a single button is in the wrong place. Clicking the one
 * that is wrong and pressing what it should be is the small version. */
let learnTarget = -1;

function startLearn(index) {
  if (remapStep >= 0) return;              // the full walk is already running
  if (learnTarget === index) {             // clicking it again changes nothing
    return cancelLearn();
  }
  cancelLearn();
  learnTarget = index;
  remapArmed = false;                      // let go of the mouse first
  const cell = el("key" + index);
  if (cell) cell.classList.add("learning");
  el("pads-hint").textContent = keyboardOn
    ? "Press the key you want for " + STANDARD_KEYS[index][0]
      + ". Click it again to cancel."
    : "Press the button you want for " + STANDARD_KEYS[index][0]
      + ". Click it again to cancel.";
}

function cancelLearn() {
  if (learnTarget >= 0) {
    const cell = el("key" + learnTarget);
    if (cell) cell.classList.remove("learning");
  }
  learnTarget = -1;
}

/* Whatever already had that button takes the one being given up, so the map
   stays a swap rather than growing a duplicate -- two entries reading the same
   physical button means one of them can never be pressed on its own. */
/* What the panel shows when the controls are a keyboard: the key on every
   button, and the buttons a keyboard has nothing to say about out of the way.
   The sticks are the honest omission -- a key is down or it is not. */
function paintKeyMode() {
  const panel = el("pads");
  if (!panel) return;
  panel.classList.toggle("keys", keyboardOn);
  STANDARD_KEYS.forEach((_n, i) => {
    const bind = el("bind" + i);
    if (bind) bind.textContent = keyboardOn ? keyLabel(keyMap[i]) : "";
  });
  if (!keyboardOn) return;
  const reset = el("pads-reset");
  if (reset) reset.hidden = keysAreDefault();
  const hint = el("pads-hint");
  if (hint && learnTarget < 0) {
    hint.textContent = "Nothing here reaches the game. Click a button below, "
      + "then press the key you want for it.";
  }
}

function bindKey(index, code) {
  const bound = bindKeyInto(keyMap, index, code);
  keyMap = bound.map;
  saveKeyMap();
  // The key that answered the question was pressed, and its key-up will arrive
  // against the button it has just been given. Start it from nothing.
  keyButtons = 0;
  paintKeyMode();
  el("pads-reset").hidden = keysAreDefault();
  el("pads-hint").textContent = bound.clash >= 0
    ? STANDARD_KEYS[index][0] + " is " + keyLabel(code) + ", and "
      + STANDARD_KEYS[bound.clash][0] + " took the key it gave up."
    : STANDARD_KEYS[index][0] + " is " + keyLabel(code) + ". Press it to check.";
  report("bound " + STANDARD_KEYS[index][0] + " to a key");
}

function bindOne(index, hit) {
  padMap = padMap || STANDARD_KEYS.map((_n, i) => i);
  const was = padMap[index];
  const clash = padMap.findIndex((from, i) => i !== index && from === hit);
  if (clash >= 0) padMap[clash] = was;
  padMap[index] = hit;
  try { localStorage.setItem(mapKey(), JSON.stringify(padMap)); } catch (_) {}
  el("pads-reset").hidden = false;
  el("pads-hint").textContent = clash >= 0
    ? STANDARD_KEYS[index][0] + " set, and " + STANDARD_KEYS[clash][0]
      + " took the button it gave up."
    : STANDARD_KEYS[index][0] + " set. Press it to check.";
  report("rebound " + STANDARD_KEYS[index][0]);
}

el("pads-sticks").addEventListener("click", () => {
  sticksSwapped = !sticksSwapped;
  try {
    if (sticksSwapped) localStorage.setItem(sticksKey(), "1");
    else localStorage.removeItem(sticksKey());
  } catch (_) {}
  paintSticks();
  el("pads-reset").hidden = !padMap && !sticksSwapped;
  el("pads-hint").textContent = sticksSwapped
    ? "Sticks swapped: the left one is now the right one. Push them to check."
    : "Sticks back the way the controller has them.";
  report(sticksSwapped ? "swapped the sticks" : "unswapped the sticks");
});

el("pads-orient").addEventListener("change", (event) => {
  const pick = ORIENTATIONS.indexOf(event.target.value) > 0
    ? event.target.value : "any";
  try {
    if (pick === "any") localStorage.removeItem(ORIENT_KEY);
    else localStorage.setItem(ORIENT_KEY, pick);
  } catch (_) {}
  applyOrient(pick);
  report("set the screen to " + pick);
});

el("pads-faceswap").addEventListener("change", (event) => {
  setFaceSwap(event.target.checked);
  el("pads-hint").textContent = event.target.checked
    ? "A and B traded, and X and Y with them. Press them in the game to check."
    : "Back to sending each button by where it sits.";
  report("face buttons " + (event.target.checked ? "swapped" : "unswapped"));
});

el("pads-buzz").addEventListener("change", (event) => {
  setHaptics(event.target.checked);
  // Which path the page is taking, and on what, so a phone that feels nothing
  // can be told apart from a phone that was never asked. The user agent is
  // the only way to know which iOS this is, and iOS is the whole question.
  report("turned the buzz " + (hapticsOn ? "on" : "off") + ", via " + feelPath
         + " on " + navigator.userAgent);
});

if (el("pads-buzz-strength")) {
  el("pads-buzz-strength").addEventListener("change", (event) => {
    setStrength(event.target.value);
    report("haptic strength " + hapticStrength + " ("
           + BUZZ_MS[hapticStrength] + " ms), via " + feelPath);
  });
}

el("padtest").addEventListener("click", openPads);
el("pads-close").addEventListener("click", closePads);
el("pads-remap").addEventListener("click", startRemap);
el("pads-reset").addEventListener("click", () => {
  cancelLearn();
  if (keyboardOn) {
    // One button, one meaning: undo whatever I did to *these* controls. The
    // controller's own map is not these controls and is left alone.
    try { localStorage.removeItem(KEY_STORE); } catch (_) {}
    loadKeyMap();
    keyButtons = 0;
    paintKeyMode();
    el("pads-reset").hidden = true;
    el("pads-hint").textContent = "Back to the keys this starts with.";
    return;
  }
  padMap = null;
  deadzone = 0.10;
  sensitivity = 1.0;
  try { localStorage.removeItem(tuneKey()); } catch (_) {}
  // The sticks go back too. This is the one button that means "undo whatever
  // I did to this controller", and leaving one of the two changes in place
  // would make it the button that undoes most of it.
  sticksSwapped = false;
  try {
    localStorage.removeItem(mapKey());
    localStorage.removeItem(sticksKey());
  } catch (_) {}
  paintSticks();
  paintTune();
  el("pads-reset").hidden = true;
  el("pads-hint").textContent = "Back to what the browser reports.";
});

/* ---- the game list ---- */

/* Everything here works off one snapshot from the host. A row carries a label,
   a console, a player count and an id -- never a path and never a command, so
   the worst a tampered-with page can ask for is a game that is already in the
   list. */
let shelfRows = [], shelfSystems = [], launchMode = "off", askTimer = null;

function escapeText(text) {
  const box = document.createElement("span");
  box.textContent = text == null ? "" : String(text);
  return box.innerHTML;
}

/* Somebody else joined. Ignored before this page knows its own label, which
   is exactly the moment it is being told about itself. */
function somebodyArrived(message) {
  const mine = el("slot").textContent;
  if (!mine || mine === "—" || message.label === mine) return;
  // A toast, not the notice. The notice opens the chips and takes the picture
  // out of the stripped-back view to make room for itself -- worth it for
  // "there is no video", and much too much for "somebody joined" arriving in
  // the middle of a game.
  showToast(message.label + " joined", message.guests + " of " + message.slots);
}

/* ---- toast ----
 *
 * One line over the picture, for the things that are worth knowing and not
 * worth interrupting for. It changes no layout: absolutely positioned, no
 * pointer events, and it never asks for the chips or leaves immersive mode.
 */
const TOAST_MS = 3600;
let toastTimer = null;

function showToast(what, footnote) {
  const box = el("toast");
  if (!box) return;                     // an older page than this build
  box.innerHTML = "";
  const line = document.createElement("span");
  line.className = "toast-what";
  line.textContent = what;
  box.appendChild(line);
  if (footnote) {
    const note = document.createElement("span");
    note.className = "toast-note";
    note.textContent = footnote;
    box.appendChild(note);
  }
  box.hidden = false;
  // Restarting the animation needs the class off and a reflow read between,
  // or a second arrival while the first is still fading does nothing at all.
  box.classList.remove("show");
  void box.offsetWidth;
  box.classList.add("show");
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    toastTimer = null;
    box.classList.remove("show");
    // Hidden only after it has faded, or it vanishes instead of fading.
    setTimeout(() => { if (!box.classList.contains("show")) box.hidden = true; },
               400);
  }, TOAST_MS);
}

/* Which tab is showing. Two things live in this panel and they are not the
   same thing: how your own controller behaves, and what the television is
   doing. */
function showTab(which) {
  for (const name of ["controls", "game", "session"]) {
    const panel = el("tab-" + name);
    const pick = el("tab-" + name + "-pick");
    const on = name === which;
    if (panel) panel.hidden = !on;
    if (pick) {
      pick.classList.toggle("is-on", on);
      pick.setAttribute("aria-selected", on ? "true" : "false");
    }
  }
  if (which === "game") paintEndGame();
  if (which === "session") { paintLogin(); paintSession(); }
}

function paintEndGame() {
  const actions = el("game-actions");
  const none = el("game-none");
  if (!actions || !none) return;
  // Only when there is a game and the owner allows starting and stopping them
  // at all. Under "approve" they are offered and the owner is asked, the same
  // as starting one.
  const playing = !!(padSeats && padSeats.playing);
  const allowed = launchMode !== "off";
  const can = allowed && playing;
  actions.hidden = !can;

  // What is on, by name, above the buttons that change it.
  const now = el("game-now");
  const named = playing && padSeats && padSeats.game;
  if (now) {
    now.hidden = !named;
    now.textContent = named ? "Playing: " + padSeats.game : "";
  }

  // And when nothing is on, the last thing that was.
  const back = el("game-continue");
  const last = !playing && allowed && padSeats && padSeats.last;
  if (back) {
    back.hidden = !last;
    if (last) el("continuegame").textContent = "Continue " + padSeats.last.label;
  }

  none.hidden = can || !!last;
  none.textContent = allowed
    ? "Nothing is playing."
    : "The owner has not turned on starting and stopping games from here.";
}

function launchPolicy(message) {
  launchMode = (message && message.policy) || "off";
  // No point offering a button that can only ever refuse.
  el("games").hidden = launchMode === "off";
  paintEndGame();
  if (launchMode === "off") closeBrowser();
  const waiting = message && message.pending;
  if (waiting) {
    countdown(waiting.who + " asked for " + waiting.label, waiting.seconds);
  } else if (askTimer) {
    clearInterval(askTimer);
    askTimer = null;
    hideNotice();
  }
}

function countdown(what, seconds) {
  if (askTimer) clearInterval(askTimer);
  let left = Math.max(0, seconds | 0);
  const paint = () => {
    showNotice("<p>" + escapeText(what) + "</p>"
               + '<p class="footnote">Waiting for the owner to say yes &mdash; '
               + left + "s</p>", true);
    if (left <= 0) { clearInterval(askTimer); askTimer = null; }
    left -= 1;
  };
  paint();
  askTimer = setInterval(paint, 1000);
}

/* A video nobody can see is a video the browser may stop.

   Safari pauses playback it decides is not being watched -- fully obscured,
   backgrounded, off screen -- and nothing here ever listened for that, so a
   paused picture stayed paused for ever and reloading the page was the only
   way back. Which is exactly what a guest reported after opening the game list
   over the top of the picture and picking something.

   So: say so, and start it again. The report goes to the host log, because
   this is otherwise invisible from the only side that keeps records. */
video.addEventListener("pause", () => {
  if (ended || video.ended) return;
  report("the browser paused the video; starting it again");
  resumeVideo();
});

// Coming back to the tab is the other half of the same thing.
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") resumeVideo();
});

function resumeVideo() {
  if (ended) return;
  const attempt = video.play();
  if (attempt && attempt.catch) {
    attempt.catch(() => {
      // Autoplay rules can refuse a silent restart. Muting is allowed, and a
      // muted picture beats a frozen one; the next thing they do gets the
      // sound back without their having to know any of this happened.
      video.muted = true;
      video.play().catch(() => {});
      chaseSound();
    });
  }
}

function openBrowser() {
  el("browser").hidden = false;
  // Asked for every time it opens: games get added to the box, and a list
  // cached from twenty minutes ago is a list missing the one they want.
  send({ t: "games" });
  el("shelf").innerHTML = '<p class="browse-note">Loading the game list…</p>';
}

function closeBrowser() {
  el("browser").hidden = true;
  el("chooser").hidden = true;
  chosen = null;
  // Whatever the browser decided while the picture was behind the list.
  resumeVideo();
}

function paintShelf(message) {
  shelfRows = message.games || [];
  shelfSystems = message.systems || [];
  launchPolicy(message);
  const picker = el("fsystem");
  if (picker.options.length !== shelfSystems.length + 1) {
    picker.innerHTML = '<option value="">Every console</option>';
    for (const row of shelfSystems) {
      const option = document.createElement("option");
      option.value = row.system;
      option.textContent = row.short;
      picker.appendChild(option);
    }
  }
  filterShelf();
}

function filterShelf() {
  const needle = el("q").value.trim().toLowerCase();
  const system = el("fsystem").value;
  const players = el("fplayers").value;
  shelfShown = shelfRows.filter((row) => {
    if (system && row.system !== system) return false;
    // A game with no known player count answers only to "any": guessing that
    // it is one-player would hide two-player games from the filter that
    // matters most here.
    if (players && row.bucket !== players) return false;
    if (needle && !row.label.toLowerCase().includes(needle)) return false;
    return true;
  });

  const shelf = el("shelf");
  // The old marker goes with the old list. Left observed, it is a detached
  // node the observer holds on to for the life of the page.
  if (shelfWatcher) shelfWatcher.disconnect();
  shelf.innerHTML = "";
  shelfDrawn = 0;
  if (!shelfShown.length) {
    shelf.innerHTML = '<p class="browse-note">Nothing matches that.</p>';
    el("browse-note").hidden = true;
    return;
  }
  drawMore();
  // Back to the top: the list underneath has changed, and leaving somebody
  // three hundred games down a list they have just replaced is disorienting.
  shelf.scrollTop = 0;
}

/* One card. Pulled out of the loop because the loop now runs many times --
   a chunk at a time, as somebody scrolls -- rather than once over everything. */
function makeCard(row) {
  const card = document.createElement("button");
  card.type = "button";
  card.className = "card";
  card.setAttribute("role", "listitem");
  card.dataset.id = row.id;
  const art = row.art
    ? '<img class="box" loading="lazy" alt="" src="/art/' + row.id + '">'
    : '<span class="box box-none" aria-hidden="true">' + escapeText(row.short) + "</span>";
  const count = row.players
    ? (row.players >= 5 ? "5+ players" : row.players + (row.players === 1 ? " player" : " players"))
    : "";
  card.innerHTML = art
    + '<span class="card-name">' + escapeText(row.label) + "</span>"
    + '<span class="card-meta">' + escapeText(row.short)
    + (count ? " &middot; " + count : "") + "</span>";
  card.addEventListener("click", () => askFor(row));
  return card;
}

/* The next chunk, and the marker that asks for the one after it.
 *
 * A library here can be thousands of games. Drawing all of them costs a phone
 * seconds of stalled main thread and the memory of a card apiece, and the
 * first screenful is the only part anybody looks at before typing in the
 * search box -- so the rest is drawn when somebody actually goes looking for
 * it. The search itself still runs over the whole library, because the whole
 * library is here; it is the drawing that is rationed, not the finding.
 *
 * This replaces a hard stop at 400 games and a line explaining that the rest
 * were not shown. */
const SHELF_CHUNK = 48;

function drawMore() {
  const shelf = el("shelf");
  if (shelfDrawn >= shelfShown.length) return;
  const end = Math.min(shelfDrawn + SHELF_CHUNK, shelfShown.length);
  const fragment = document.createDocumentFragment();
  for (let i = shelfDrawn; i < end; i++) fragment.appendChild(makeCard(shelfShown[i]));
  shelfDrawn = end;

  // The marker always ends up last, so it moves down as the list grows.
  const marker = shelfMarker();
  shelf.insertBefore(fragment, marker);
  const more = shelfDrawn < shelfShown.length;
  marker.hidden = !more;
  if (more) watchShelfEnd(marker);
  paintShelfCount();
}

function shelfMarker() {
  // Found by class off the shelf rather than by id: it is written into the
  // page rather than living in it, and every id el() reaches for is supposed
  // to be in index.html. There is a test that says so.
  const shelf = el("shelf");
  let marker = shelf.querySelector(".shelf-end");
  if (!marker) {
    marker = document.createElement("div");
    marker.className = "shelf-end";
    marker.textContent = "Loading more\u2026";
  }
  shelf.appendChild(marker);             // always the last thing in the list
  return marker;
}

let shelfWatcher = null;

function watchShelfEnd(marker) {
  if (!window.IntersectionObserver) {
    // Old enough to have no observer: fall back to asking on scroll, which
    // costs a comparison per scroll event and works everywhere.
    const shelf = el("shelf");
    if (!shelf.dataset.watching) {
      shelf.dataset.watching = "1";
      shelf.addEventListener("scroll", () => {
        if (shelf.scrollTop + shelf.clientHeight > shelf.scrollHeight - 400) {
          drawMore();
        }
      });
    }
    return;
  }
  if (!shelfWatcher) {
    shelfWatcher = new IntersectionObserver((entries) => {
      // Only when it is actually on screen. rootMargin gives it a screenful of
      // warning, so the next chunk is there before the scroll reaches it and
      // the list never visibly stops.
      if (entries.some((entry) => entry.isIntersecting)) drawMore();
    }, { root: el("shelf"), rootMargin: "600px" });
  }
  shelfWatcher.observe(marker);
}

function paintShelfCount() {
  const note = el("browse-note");
  const total = shelfShown.length;
  note.hidden = total <= SHELF_CHUNK;
  // The whole number, always -- somebody searching wants to know how many
  // matched, not how many happen to be drawn.
  note.textContent = total + (total === 1 ? " game" : " games");
}

// The filtered list, and how much of it has been drawn so far.
let shelfShown = [];
let shelfDrawn = 0;

let chosen = null;

/* Asked before anything starts, for two reasons. A mis-tap here starts a game
   on a television in somebody else's house; and "play this" means two
   different things -- begin it, or carry on from the save that is on the box.
   Beginning it is the default here, the opposite of the television's own menu:
   there, picking a game is somebody choosing to resume their own save; here it
   is a guest starting one on a machine they are not sitting at, and dropping
   into the middle of someone else's game -- then writing over it on the way
   out -- is not a thing to do without being asked. */
function askFor(row) {
  chosen = row;
  el("chooser-name").textContent = row.label;
  const count = row.players
    ? (row.players >= 5 ? "5+ players"
       : row.players + (row.players === 1 ? " player" : " players"))
    : "";
  el("chooser-meta").textContent = [row.short, count].filter(Boolean).join("  ·  ");

  const resume = el("chooser-resume");
  resume.hidden = !row.saved;
  if (row.saved) {
    resume.textContent = "Continue where it was left" + savedWhen(row.saved_at);
  }
  const warn = el("chooser-warn");
  warn.hidden = launchMode !== "open";
  warn.textContent = "This stops whatever is playing now.";
  el("chooser").hidden = false;
}

function savedWhen(stamp) {
  if (!stamp) return "";
  try {
    const d = new Date(stamp * 1000);
    return " — saved " + d.toLocaleDateString(undefined,
      { day: "numeric", month: "short" }) + ", "
      + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  } catch (_) { return ""; }
}

function startChosen(resume) {
  const row = chosen;
  chosen = null;
  el("chooser").hidden = true;
  if (!row) return;
  send({ t: "launch", game: row.id, resume: !!resume });
  closeBrowser();
  showNotice("<p>Asking for <strong>" + escapeText(row.label) + "</strong>"
             + (resume ? ", continuing where it was left" : ", from the start")
             + "&hellip;</p>", true);
}

el("chooser-fresh").addEventListener("click", () => startChosen(false));
el("chooser-resume").addEventListener("click", () => startChosen(true));
el("chooser-cancel").addEventListener("click", () => {
  chosen = null;
  el("chooser").hidden = true;
});

function launchResult(message) {
  if (!message.ok) {
    showNotice('<p class="footnote">' + escapeText(message.error) + "</p>", false);
    return;
  }
  if (message.state === "pending") {
    countdown("You asked for " + message.label, message.seconds);
    return;
  }
  if (message.stopped) {
    // The host says whether it closed on its own or had to be stopped, and
    // that is the difference between a save that was written and one that
    // was not. It sends the words; this only has to not talk over them.
    return;
  }
  showNotice("<p><strong>" + escapeText(message.label)
             + "</strong> is starting&hellip;</p>", false);
}

/* The search box lives behind its own icon: the bar has room for the filters
   or a search field on a phone, not both, and the filters are what is usually
   wanted. Clicking the magnifier opens it; closing it clears the query, since
   a hidden filter still filtering is a list that looks broken. */
function toggleFind(open) {
  const bar = el("q").parentElement;
  const wanted = open === undefined ? !bar.classList.contains("searching") : open;
  bar.classList.toggle("searching", wanted);
  if (wanted) {
    el("q").focus();
  } else if (el("q").value) {
    el("q").value = "";
    filterShelf();
  }
}

el("find-open").addEventListener("click", () => toggleFind());
el("q").addEventListener("blur", () => {
  if (!el("q").value) toggleFind(false);
});

el("games").addEventListener("click", openBrowser);
/* Ending the game. Asked about first, because it is somebody else's evening:
   the person holding this phone may be the fourth player in a room where
   three people are mid-race. */
if (el("endgame")) {
  el("endgame").addEventListener("click", () => {
    if (!confirm("End the game on the television?\n\nIt is saved first, and "
                 + "carries on from here next time.")) return;
    send({ t: "endgame" });
  });
}
if (el("restartgame")) {
  el("restartgame").addEventListener("click", () => {
    // Spelled out, because "restart" is the one of these that throws
    // something away: it is the same game from the beginning, not a reload of
    // where everybody is.
    if (!confirm("Start this game again from the beginning?\n\nEverybody "
                 + "playing goes back to the start. Your save is left where "
                 + "it is -- end the game instead to keep your place."))
      return;
    send({ t: "restart" });
  });
}
if (el("continuegame")) {
  el("continuegame").addEventListener("click", () => {
    // No confirmation: nothing is playing, so this interrupts nobody. The
    // two above it stop something somebody is in the middle of, which is the
    // difference.
    const last = padSeats && padSeats.last;
    if (last) send({ t: "launch", game: last.id, resume: true });
  });
}

for (const name of ["controls", "game", "session"]) {
  const pick = el("tab-" + name + "-pick");
  if (pick) pick.addEventListener("click", () => showTab(name));
}
wireSession();
wireLogin();

el("browse-close").addEventListener("click", closeBrowser);
for (const id of ("q fsystem fplayers").split(" ")) {
  el(id).addEventListener("input", filterShelf);
}

// The name is theirs, not the session's: it is remembered here and sent again
// on a resume, so coming back does not turn them into a slot number.
const nameKey = "fp-name";
try {
  const saved = localStorage.getItem(nameKey);
  if (saved) el("who").value = saved;
} catch (_) {}

// A guest whose socket dropped comes back without being asked for the PIN.
const saved = (() => { try { return localStorage.getItem(credKey()); } catch (_) { return null; } })();
if (saved) {
  guestToken = saved;
  el("pin").placeholder = "rejoining…";
  el("join").disabled = true;
  connect({ t: "resume", guest: saved, name: myName(),
            codecs: videoCodecs(), media: "new" });
}
