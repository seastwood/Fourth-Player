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

const el = (id) => document.getElementById(id);
const gate = el("gate"), stage = el("stage"), video = el("screen");

let socket = null, pc = null, input = null;
let seq = 0, ticker = null, padIndex = null;

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

function connect(hello) {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  socket = new WebSocket(`${scheme}//${location.host}/ws`);

  socket.addEventListener("open", () => socket.send(JSON.stringify(hello)));
  socket.addEventListener("close", () => setChip("link", "disconnected", "bad"));
  socket.addEventListener("error", () => setChip("link", "error", "bad"));

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
      case "error":    return gate.hidden ? setChip("link", message.message, "bad")
                                          : fail(message.message);
    }
  });
}

function sessionOver(reason) {
  setChip("clock", "session ended", "bad");
  setChip("link", reason || "closed", "bad");
  el("hud").classList.add("show");
  if (ticker) { clearInterval(ticker); ticker = null; }
  // Nothing is coming back, so stop pretending: drop the saved credential so a
  // reload asks for a PIN rather than silently failing to resume.
  try { localStorage.removeItem(storageKey); } catch (_) {}
}

function joined(message) {
  try { localStorage.setItem(storageKey, message.guest); } catch (_) {}
  gate.hidden = true;
  stage.hidden = false;
  setChip("slot", message.label, "ok");
  setChip("link", "connecting", "");
  startClock(message.remaining);
  startPadLoop();
}

async function answer(message) {
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

  ticker = setInterval(tick, Math.round(1000 / SEND_HZ));

  // Leaving must not leave a button held down on someone else's television.
  const letGo = () => sendFrame(null, true);
  window.addEventListener("pagehide", letGo);
  window.addEventListener("beforeunload", letGo);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") letGo();
  });
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

function sendFrame(pad, releaseAll) {
  if (!input || input.readyState !== "open") return;
  const buffer = FPFrame.buildFrame(pad, seq, releaseAll);
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
  el("pin").placeholder = "rejoining…";
  connect({ t: "resume", guest: saved });
}
