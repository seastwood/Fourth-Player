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

const token = location.pathname.startsWith("/j/")
  ? decodeURIComponent(location.pathname.slice(3))
  : "";
const storageKey = "fp:" + token.slice(0, 16);

/* ---- the gate ---- */

let joinTimer = null;

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
  connect({ t: "join", token, pin });
});

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
  if (ended || retryTimer || !guestToken) return;
  const delay = Math.min(15000, 1000 * Math.pow(2, retries++));
  if (!mediaIsLive()) setChip("link", "reconnecting…", "warn");
  retryTimer = setTimeout(() => {
    retryTimer = null;
    connect({ t: "resume", guest: guestToken, media: mediaIsLive() ? "live" : "new" });
  }, delay);
}

function connect(hello) {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  socket = new WebSocket(`${scheme}//${location.host}/ws`);

  socket.addEventListener("open", () => socket.send(JSON.stringify(hello)));
  socket.addEventListener("close", () => {
    if (ended) return;
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
    if (!mediaIsLive()) setChip("link", "reconnecting…", "warn");
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
      case "extended": return startClock(message.remaining);
      case "closed":   return sessionOver(message.reason);
      case "error":    return onError(message);
    }
  });
}

function sessionOver(reason) {
  ended = true;
  showHud(true);
  if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; }
  setChip("clock", "session ended", "bad");
  setChip("link", reason || "closed", "bad");
  if (ticker) { clearInterval(ticker); ticker = null; }
  // Nothing is coming back, so stop pretending: drop the saved credential so a
  // reload asks for a PIN rather than silently failing to resume.
  try { localStorage.removeItem(storageKey); } catch (_) {}
}

/* A resume that is never answered must not leave the page saying "rejoining"
 * forever. The credential may be stale, the session may have ended, or the
 * host may be wedged -- from here they are indistinguishable, and all three
 * have the same remedy: ask for the PIN again. */
let rejoinTimer = null;

function clearRejoinTimer() {
  if (rejoinTimer) { clearTimeout(rejoinTimer); rejoinTimer = null; }
}

function armRejoinTimer() {
  clearRejoinTimer();
  rejoinTimer = setTimeout(() => {
    rejoinTimer = null;
    if (!gate.hidden) askForPin("That did not get you back in.");
  }, 12000);
}

function askForPin(why) {
  guestToken = null;
  try { localStorage.removeItem(storageKey); } catch (_) {}
  clearRejoinTimer();
  el("pin").placeholder = "000000";
  el("pin").value = "";
  el("join").disabled = false;
  fail(why + " Enter the PIN to join again.");
}

function onError(message) {
  if (gate.hidden) {
    // Already playing. A refused resume means the credential is no longer
    // good -- stop retrying with it rather than hammering the host.
    guestToken = null;
    setChip("link", message.message, "bad");
  } else {
    askForPin(message.message);
  }
}

function joined(message) {
  retries = 0;
  clearTimeout(joinTimer);
  clearRejoinTimer();
  if (message.guest) guestToken = message.guest;
  try { if (message.guest) localStorage.setItem(storageKey, message.guest); } catch (_) {}
  if (message.resumed_media) {
    setChip("link", "connected", "ok");
    startClock(message.remaining);
    return;                      // the stream never stopped; leave it alone
  }
  gate.hidden = true;
  stage.hidden = false;
  setChip("slot", message.label, "ok");
  setChip("link", "connecting", "");
  showHud();
  startClock(message.remaining);
  startPadLoop();
}

async function answer(message) {
  if (pc) { try { pc.close(); } catch (_) {} }
  pc = new RTCPeerConnection({ iceServers: [] });

  // Video and audio arrive as separate tracks. Collect them into one stream
  // rather than replacing srcObject on the second one, which drops the first.
  const incoming = new MediaStream();
  pc.addEventListener("track", (event) => {
    incoming.addTrack(event.track);
    if (video.srcObject !== incoming) video.srcObject = incoming;
    startPlayback();
  });

  pc.addEventListener("datachannel", (event) => {
    input = event.channel;
    input.binaryType = "arraybuffer";
    input.addEventListener("open", () => setChip("link", "connected", "ok"));
    input.addEventListener("close", () => {
      setChip("link", "controller offline", "bad");
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
      setChip("link", "connected", "ok");
      clearMediaTimeout();
      clearRenewTimer();
      renewals = 0;
      lastBytes = -1;
      stalledSince = 0;
      mediaFresh = false;
      startWatchdog();
      setTimeout(() => report("video playing"), 5000);
    }
    // "disconnected" often mends itself in a second or two, so give it that
    // long. "failed" never does: the addresses it was using are gone.
    if (pc.connectionState === "disconnected") {
      setChip("link", "reconnecting…", "warn");
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
  video.play().then(() => {
    video.muted = false;
    return video.play();
  }).then(() => {
    el("unmute").hidden = true;
  }).catch(() => {
    video.muted = true;
    video.play().catch(() => {});
    el("unmute").hidden = false;
    showHud(true);          // stays until they tap it, then behaves normally
  });
}

el("unmute").addEventListener("click", () => {
  video.muted = false;
  video.play().then(() => {
    el("unmute").hidden = true;
    hideHud();
  }).catch(() => {});
});

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

const DEFAULT_LAYOUT = "genesis";
const LAYOUT_KEY = "fp:layout";

const DPAD = { up: 12, down: 13, left: 14, right: 15 };

let touchOn = false;
let touchButtons = 0;
const pointers = new Map();

function makeButton(spec, className) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = spec.id;
  button.dataset.button = String(spec.button);
  return button;
}

function buildTouchPad(layout) {
  releaseAllTouch();          // never carry a held button across a rebuild

  const face = el("face");
  face.innerHTML = "";
  face.style.aspectRatio = String(layout.faceAspect || 1.55);
  for (const spec of layout.face) {
    const button = makeButton(spec, "tbtn tbtn-face");
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

  el("touch-name").textContent = layout.name;
}

function chosenLayout() {
  let saved = null;
  try { saved = localStorage.getItem(LAYOUT_KEY); } catch (_) {}
  return (saved === "off" || LAYOUTS[saved]) ? saved : DEFAULT_LAYOUT;
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
  for (const [key, layout] of Object.entries(LAYOUTS)) {
    const option = document.createElement("option");
    option.value = key;
    option.textContent = layout.name;
    picker.appendChild(option);
  }
  picker.value = chosenLayout();
  picker.addEventListener("change", () => {
    try { localStorage.setItem(LAYOUT_KEY, picker.value); } catch (_) {}
    chosenByHand = true;              // stop guessing for them from here on
    applyLayoutChoice(picker.value);
  });
}

let chosenByHand = false;
let padName = "";

function applyLayoutChoice(key) {
  if (key === "off") {
    el("touch").hidden = true;
    releaseAllTouch();
    setChip("padstate", padName || "No controller", padName ? "ok" : "warn");
    return;
  }
  el("touch").hidden = false;
  buildTouchPad(LAYOUTS[key] || LAYOUTS[DEFAULT_LAYOUT]);
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

function applyDpad(event) {
  const live = dpadDirections(event);
  for (const [name, bit] of Object.entries(DPAD)) setBit(bit, live.includes(name));
  // Light the arm being pressed, not the middle: a diagonal lights two, which
  // is also the clearest way to see that diagonals work at all.
  paintDpad(live);
}

function clearDpad() {
  for (const bit of Object.values(DPAD)) setBit(bit, false);
  paintDpad([]);
}

function wireTouch() {
  const pad = el("dpad");

  pad.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    pad.setPointerCapture(event.pointerId);
    pointers.set(event.pointerId, "dpad");
    applyDpad(event);
  });
  pad.addEventListener("pointermove", (event) => {
    if (pointers.get(event.pointerId) !== "dpad") return;
    event.preventDefault();
    applyDpad(event);          // sliding across the pad changes direction
  });
  const releasePad = (event) => {
    if (pointers.get(event.pointerId) !== "dpad") return;
    pointers.delete(event.pointerId);
    clearDpad();
  };
  pad.addEventListener("pointerup", releasePad);
  pad.addEventListener("pointercancel", releasePad);

  el("touch").addEventListener("pointerdown", (event) => {
    const button = event.target.closest(".tbtn");
    if (!button) return;
    event.preventDefault();
    button.setPointerCapture(event.pointerId);
    pointers.set(event.pointerId, button);
    button.classList.add("live");
    setBit(Number(button.dataset.button), true);
    if (navigator.vibrate) navigator.vibrate(8);
  });
  const releaseButton = (event) => {
    const button = pointers.get(event.pointerId);
    if (!button || button === "dpad") return;
    pointers.delete(event.pointerId);
    button.classList.remove("live");
    setBit(Number(button.dataset.button), false);
  };
  el("touch").addEventListener("pointerup", releaseButton);
  el("touch").addEventListener("pointercancel", releaseButton);

  // A finger still down when the page goes away must not leave a button held
  // on somebody else's television.
  window.addEventListener("blur", releaseAllTouch);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") releaseAllTouch();
  });
}

function releaseAllTouch() {
  lastSent = null;          // the next frame must go, whatever it says
  touchButtons = 0;
  pointers.clear();
  clearDpad();
  document.querySelectorAll(".tbtn.live").forEach((b) => b.classList.remove("live"));
}

function showTouch(on, layout) {
  touchOn = on;
  // The picker stays available whether or not the pad is showing -- otherwise
  // turning it off is a one-way door.
  buildLayoutPicker();
  el("padtype").hidden = false;

  if (!on) {
    el("touch").hidden = true;
    releaseAllTouch();
    return;
  }
  const key = layout || chosenLayout();
  el("padtype").value = (key === "off" || LAYOUTS[key]) ? key : DEFAULT_LAYOUT;
  applyLayoutChoice(el("padtype").value);
  el("prompt").hidden = true;
  if (el("padtype").value !== "off") setChip("padstate", "On-screen pad", "ok");
}

el("use-touch").addEventListener("click", (event) => {
  event.preventDefault();
  showTouch(true);
});

function videoRefused() {
  setChip("link", "no H.264", "bad");
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
    setChip("link", "reconnecting…", "warn");
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
  if (pc && pc.connectionState !== "connected") renewSoon(500);
});
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") {
    // Nothing can be assumed about what survives being in the background.
    mediaFresh = false;
    return;
  }
  if (ended || !pc) return;
  // Coming back to the tab. Do not trust the connection state -- check whether
  // anything is moving, and give it a couple of seconds to prove it before
  // rebuilding, since a brief background is often survivable.
  stalledSince = 0;
  lastBytes = -1;
  mediaFresh = false;
  setTimeout(watchMedia, 500);
  setTimeout(() => {
    if (!ended && pc && lastBytes < 0) renewSoon(0, true);
  }, 3000);
  if (pc.connectionState !== "connected") renewSoon(500);
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
  el("hud").classList.add("show");
  if (hudTimer) { clearTimeout(hudTimer); hudTimer = null; }
  if (!persist) hudTimer = setTimeout(hideHud, HUD_SECONDS * 1000);
}

function hideHud() {
  if (hudTimer) { clearTimeout(hudTimer); hudTimer = null; }
  // Nothing is hidden while there is something to read.
  if (el("notice").hidden) el("hud").classList.remove("show");
  else hudTimer = setTimeout(hideHud, HUD_SECONDS * 1000);
}

// Tapping the picture asks for the chips, and asks again to dismiss them.
el("screen").addEventListener("click", () => {
  if (el("hud").classList.contains("show")) {
    hideNotice();
    el("hud").classList.remove("show");
    if (hudTimer) { clearTimeout(hudTimer); hudTimer = null; }
  } else {
    showHud();
  }
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

el("hide").addEventListener("click", (event) => {
  event.stopPropagation();
  hideNotice();
  el("hud").classList.remove("show");
  if (hudTimer) { clearTimeout(hudTimer); hudTimer = null; }
});

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
    if (pc && pc.connectionState === "connected") return;
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
    socket.send(JSON.stringify({ t: "report", detail: what + " — " + (await describeRoute()) }));
  } catch (_) { /* reporting must never break anything */ }
}

async function mediaFailed(why) {
  setChip("link", "no video", "bad");
  showHud(true);
  el("prompt").hidden = false;
  const route = await describeRoute();
  report(why);
  el("prompt").innerHTML =
    "<p><strong>" + why + "</strong></p>" +
    "<p class=\"footnote\">The page and the PIN reach the host over one port; " +
    "the video takes a different, direct route, and that one is not getting " +
    "through. Usually the host's UDP ports are not forwarded.</p>" +
    "<p class=\"footnote\">" + route + "</p>";
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
        if (r.type === "remote-candidate" && r.address) {
          tried.push(r.address + ":" + r.port + " (" + r.candidateType + ")");
        }
      });
      return tried.length
        ? "Nothing connected. The host offered: " + [...new Set(tried)].join(", ")
        : "Nothing connected, and the host offered no reachable address.";
    }
    const remote = byId.get(pair.remoteCandidateId);
    const bytes = pair.bytesReceived || 0;
    return "Connected to " +
      (remote ? remote.address + ":" + remote.port + " (" + remote.candidateType + ")"
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
const STALL_LIMIT_MS = 6000;
let lastBytes = -1, stalledSince = 0, watchdogTimer = null;

async function watchMedia() {
  if (ended || !pc) return;
  let bytes = 0;
  try {
    (await pc.getStats()).forEach((r) => {
      if (r.type === "inbound-rtp" && (r.kind === "video" || r.mediaType === "video")) {
        bytes += r.bytesReceived || 0;
      }
    });
  } catch (_) { return; }

  if (bytes > lastBytes) {
    lastBytes = bytes;
    stalledSince = 0;
    mediaFresh = true;
    // Video is arriving, so whatever the chip last said is out of date. This
    // is also what stops it sticking on "reconnecting" after a rebuild that
    // actually worked.
    setChip("link", "connected", "ok");
    if (!el("notice").hidden && lastNotice.includes("could not")) hideNotice();
    return;
  }
  if (lastBytes < 0) return;               // nothing has arrived yet at all

  const now = Date.now();
  if (!stalledSince) { stalledSince = now; return; }
  if (now - stalledSince >= STALL_LIMIT_MS) {
    stalledSince = 0;
    mediaFresh = false;
    setChip("link", "reconnecting…", "warn");
    renewSoon(0, true);
  }
}

function startWatchdog() {
  if (watchdogTimer) clearInterval(watchdogTimer);
  watchdogTimer = setInterval(watchMedia, 2000);
}

/* ---- the pad ---- */

function startPadLoop() {
  window.addEventListener("gamepadconnected", (event) => {
    padIndex = event.gamepad.index;
    el("prompt").hidden = true;
    describePad(event.gamepad);
  });
  window.addEventListener("gamepaddisconnected", () => {
    padIndex = null;
    padName = "";
    setChip("padstate", "No controller", "warn");
    // Their controller has gone; offer the on-screen one back unless they
    // turned it off deliberately.
    if (!chosenByHand && el("padtype").value !== "off") showTouch(true);
    else el("prompt").hidden = false;
  });

  wireTouch();
  // A phone with no controller gets the on-screen pad without being asked; a
  // laptop does not, because a mouse cannot use it and it would only be in the
  // way. The link in the prompt covers everyone this guesses wrong about.
  if (!hasGamepad() && navigator.maxTouchPoints > 0) showTouch(true);

  ticker = setInterval(tick, Math.round(1000 / SEND_HZ));

  // Leaving must not leave a button held down on someone else's television.
  const letGo = () => sendFrame(null, true);
  window.addEventListener("pagehide", letGo);
  window.addEventListener("beforeunload", letGo);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") letGo();
  });
}

function hasGamepad() {
  const pads = navigator.getGamepads ? navigator.getGamepads() : [];
  return Array.from(pads).some((p) => p && p.connected);
}

function tick() {
  if (!input || input.readyState !== "open") return;
  const pads = navigator.getGamepads ? navigator.getGamepads() : [];
  let pad = padIndex !== null ? pads[padIndex] : null;
  if (!pad) pad = Array.from(pads).find((p) => p && p.connected) || null;
  if (pad && padIndex === null) {
    padIndex = pad.index;
    el("prompt").hidden = true;
    describePad(pad);
  }
  sendFrame(pad, false);
}

let lastSent = null, lastSentAt = 0;

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
  const buttons = releaseAll ? 0 : (state.buttons | touchButtons);
  const axes = releaseAll ? [0, 0, 0, 0, 0, 0] : state.axes;

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
  if (!releaseAll && input.bufferedAmount > BACKLOG_LIMIT) return;

  const buffer = FPFrame.buildRaw(buttons, axes, seq, releaseAll);
  seq = (seq + 1) & 0xffff;
  try {
    input.send(buffer);
    lastSent = { buttons, axes: axes.slice() };
    lastSentAt = now;
  } catch (_) { /* a closing channel is not news */ }
}

/* ---- chrome ---- */

function setChip(id, text, kind) {
  const chip = el(id);
  chip.textContent = text;
  chip.className = "chip" + (kind ? " " + kind : "");
}

function timeLeft(seconds) {
  const m = Math.floor(seconds / 60), s = Math.floor(seconds % 60);
  return m + ":" + String(s).padStart(2, "0");
}

let clockTimer = null;
function startClock(seconds) {
  let left = seconds;
  if (clockTimer) clearInterval(clockTimer);
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

function enterVideoFullscreen() {
  // Native video fullscreen puts the picture above everything, including the
  // on-screen pad -- fine with a real controller, and the reason this is the
  // fallback rather than the first choice.
  if (video.webkitEnterFullscreen) video.webkitEnterFullscreen();
  else if (video.webkitSetPresentationMode) video.webkitSetPresentationMode("fullscreen");
  else showNotice('<p class="footnote">This browser will not let the page go '
                  + "fullscreen. Rotating the phone usually gets the same result.</p>");
}

// A guest whose socket dropped comes back without being asked for the PIN.
const saved = (() => { try { return localStorage.getItem(storageKey); } catch (_) { return null; } })();
if (saved) {
  guestToken = saved;
  el("pin").placeholder = "rejoining…";
  el("join").disabled = true;
  armRejoinTimer();
  connect({ t: "resume", guest: saved, media: "new" });
}
