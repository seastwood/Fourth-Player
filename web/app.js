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

el("pin-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const pin = el("pin").value.trim();
  if (pin.length < 4) return;
  el("join").disabled = true;
  connect({ t: "join", token, pin });
});

function fail(message) {
  const box = el("gate-error");
  box.textContent = message;
  box.hidden = false;
  el("join").disabled = false;
}

/* ---- signalling ---- */

/* The signalling socket is not the stream. Losing it should cost nothing while
 * the WebRTC connection is up -- so this reconnects quietly in the background
 * and tells the host whether the media it set up is still running, so it knows
 * not to renegotiate a picture that never stopped. */
function mediaIsLive() {
  return pc !== null && pc.connectionState === "connected";
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
    // Say nothing alarming if the game is still playing perfectly well.
    if (!mediaIsLive()) setChip("link", "reconnecting…", "warn");
    reconnectSoon();
  });
  socket.addEventListener("error", () => { /* close follows; handled there */ });

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
  if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; }
  setChip("clock", "session ended", "bad");
  setChip("link", reason || "closed", "bad");
  el("hud").classList.add("show");
  if (ticker) { clearInterval(ticker); ticker = null; }
  // Nothing is coming back, so stop pretending: drop the saved credential so a
  // reload asks for a PIN rather than silently failing to resume.
  try { localStorage.removeItem(storageKey); } catch (_) {}
}

function onError(message) {
  if (gate.hidden) {
    // Already playing. A refused resume means the credential is no longer
    // good -- stop retrying with it rather than hammering the host.
    guestToken = null;
    setChip("link", message.message, "bad");
  } else {
    fail(message.message);
  }
}

function joined(message) {
  retries = 0;
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
    input.addEventListener("close", () => setChip("link", "input closed", "bad"));
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
    if (pc.connectionState === "connected") setChip("link", "connected", "ok");
    if (pc.connectionState === "failed") setChip("link", "connection failed", "bad");
  });

  await pc.setRemoteDescription({ type: "offer", sdp: message.sdp });
  const local = await pc.createAnswer();
  await pc.setLocalDescription(local);
  socket.send(JSON.stringify({ t: "answer", sdp: local.sdp }));
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
    el("hud").classList.add("show");
  });
}

el("unmute").addEventListener("click", () => {
  video.muted = false;
  video.play().then(() => {
    el("unmute").hidden = true;
    el("hud").classList.remove("show");
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
const LAYOUTS = {
  genesis: {
    name: "Mega Drive",
    // Percentages inside the face box. The geometry has to close: with
    // buttons 30% of the width across, the lowest one sits at 50% of the
    // *height*, so the box must be no wider than about 1.5:1 or the bottom of
    // A falls outside it -- which clipped A and B off the screen in landscape
    // and pushed C against the edge in portrait.
    face: [
      { id: "A", button: 2, x: 0, y: 50 },
      { id: "B", button: 0, x: 34, y: 27.5 },
      { id: "C", button: 1, x: 68, y: 5 },
    ],
    start: 9,
  },
};

const DPAD = { up: 12, down: 13, left: 14, right: 15 };

let touchOn = false;
let touchButtons = 0;
const pointers = new Map();

function buildTouchPad(layout) {
  const face = el("face");
  face.innerHTML = "";
  for (const spec of layout.face) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "tbtn tbtn-face";
    button.textContent = spec.id;
    button.dataset.button = String(spec.button);
    button.style.left = spec.x + "%";
    button.style.top = spec.y + "%";
    face.appendChild(button);
  }
  el("touch").querySelector(".tbtn-start").dataset.button = String(layout.start);
  el("touch-name").textContent = layout.name;
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

function applyDpad(event) {
  const live = dpadDirections(event);
  for (const [name, bit] of Object.entries(DPAD)) setBit(bit, live.includes(name));
  el("dpad").classList.toggle("live", live.length > 0);
}

function clearDpad() {
  for (const bit of Object.values(DPAD)) setBit(bit, false);
  el("dpad").classList.remove("live");
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
  touchButtons = 0;
  pointers.clear();
  clearDpad();
  document.querySelectorAll(".tbtn.live").forEach((b) => b.classList.remove("live"));
}

function showTouch(on, layout) {
  touchOn = on;
  el("touch").hidden = !on;
  if (!on) {
    releaseAllTouch();
    return;
  }
  buildTouchPad(LAYOUTS[layout || "genesis"]);
  el("prompt").hidden = true;
  setChip("padstate", "On-screen pad", "ok");
}

el("use-touch").addEventListener("click", (event) => {
  event.preventDefault();
  showTouch(true);
});

/* ---- the pad ---- */

function startPadLoop() {
  window.addEventListener("gamepadconnected", (event) => {
    padIndex = event.gamepad.index;
    el("prompt").hidden = true;
    describePad(event.gamepad);
  });
  window.addEventListener("gamepaddisconnected", () => {
    padIndex = null;
    el("prompt").hidden = false;
    setChip("padstate", "No controller", "warn");
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
    if (!touchOn) el("prompt").hidden = true;
    describePad(pad);
  }
  sendFrame(pad, false);
}

function sendFrame(pad, releaseAll) {
  if (!input || input.readyState !== "open") return;
  // A physical pad and the on-screen one are merged rather than one replacing
  // the other: whichever is being touched wins by simply being pressed.
  const state = FPFrame.padState(releaseAll ? null : pad);
  const buttons = releaseAll ? 0 : (state.buttons | touchButtons);
  const buffer = FPFrame.buildRaw(buttons, state.axes, seq, releaseAll);
  seq = (seq + 1) & 0xffff;
  try { input.send(buffer); } catch (_) { /* a closing channel is not news */ }
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
  if (document.fullscreenElement) document.exitFullscreen();
  else stage.requestFullscreen && stage.requestFullscreen();
});

// A guest whose socket dropped comes back without being asked for the PIN.
const saved = (() => { try { return localStorage.getItem(storageKey); } catch (_) { return null; } })();
if (saved) {
  guestToken = saved;
  el("pin").placeholder = "rejoining…";
  connect({ t: "resume", guest: saved, media: "new" });
}
