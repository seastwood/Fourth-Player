/* The console's setup page.
 *
 * Every session control goes through one endpoint -- /api/control -- which
 * forwards to the same handler the command line talks to. That is deliberate:
 * a second list of commands here would be a second thing to keep in agreement
 * with the first, which is how a page ends up quietly missing the one setting
 * somebody needs. The account calls are separate only because they are not in
 * the control channel at all, for the reason the README gives.
 */
const el = (id) => document.getElementById(id);
let STATE = {};

async function post(path, body) {
  const answer = await fetch(path, {
    method: "POST",
    headers: {"content-type": "application/json"},
    body: JSON.stringify(body || {}),
  });
  const reply = await answer.json();
  if (answer.status === 401 || (reply && reply.signin)) {
    // The sign-in expired, or the host restarted. Back to the form rather
    // than a page full of empty tables and no explanation.
    el("signin").hidden = false;
    el("everything").hidden = true;
  }
  return reply;
}

const control = (cmd, extra) => post("/api/control", Object.assign({cmd}, extra || {}));

function say(text, bad) {
  const box = el("say");
  box.textContent = text;
  box.classList.toggle("bad", !!bad);
  box.hidden = false;
  clearTimeout(say.timer);
  say.timer = setTimeout(() => { box.hidden = true; }, bad ? 8000 : 4000);
}

/* An answer from the host, reported the same way every time. Returns whether
   it worked, so a caller can decide what to do next without re-reading it. */
function heard(answer, done) {
  if (answer && answer.ok) {
    if (done) say(done);
    return true;
  }
  say((answer && (answer.error || answer.message)) || "that did not work", true);
  return false;
}

/* ---- drawing ---- */

function rows(into, pairs) {
  into.innerHTML = pairs.map(([k, v]) => `<tr><th>${k}</th><td>${v}</td></tr>`).join("");
}

const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g,
  (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));

/* The link and PIN, drawn to be pointed a phone at.

   Only ever redrawn when they change. The QR is a round trip to the host and
   the page reloads every few seconds; redrawing it each time would replace the
   image under somebody's camera while they were reading it. */
let shownFor = null;

async function drawShare(s) {
  const open = s && s.open;
  const have = open && (s.url || s.pin);
  el("share").hidden = !have;
  // Only say "cannot be shown again" about a session that is actually open.
  el("share-gone").hidden = !(open && !have);
  if (!have) { shownFor = null; return; }

  el("share-pin").textContent = s.pin || "not set";
  el("share-url").textContent = s.url || "—";
  el("share-note").textContent = s.require_link
    ? "The guest needs both: open the link, then type the PIN."
    : "The link is not required — the address and the PIN are enough, so this "
      + "can be read out loud.";

  const key = (s.url || "") + "|" + (s.pin || "");
  if (key === shownFor) return;
  shownFor = key;
  const target = s.url || s.base_url;
  if (!target) return;
  const answer = await post("/api/qr", {text: target});
  const image = el("share-qr"), art = el("share-art");
  if (answer.ok && answer.png) {
    image.src = answer.png; image.hidden = false; art.hidden = true;
  } else if (answer.ok && answer.art) {
    art.textContent = answer.art; art.hidden = false; image.hidden = true;
  } else {
    image.hidden = art.hidden = true;
  }
}

function drawSession(s) {
  const open = s && s.open;
  el("session-closed").hidden = !!open;
  el("session-open").hidden = !open;
  if (!open) {
    el("session-now").innerHTML = "<span class=none>No session open.</span>";
    return;
  }
  const bits = [];
  // `url` and `pin` are null once a session has been restored across a
  // restart: only digests are kept, so the host cannot show them again. Say
  // that rather than drawing an empty box.
  if (s.url) bits.push(`link <code>${esc(s.url)}</code>`);
  if (s.pin) bits.push(`PIN <code>${esc(s.pin)}</code>`);
  if (!s.url && !s.pin) {
    bits.push('<span class="none">the link and PIN cannot be shown again — ' +
              'only their digests are kept. Re-share for new ones.</span>');
  }
  // `remaining` is seconds, and null when there is no deadline: JSON has no
  // infinity and a browser will not parse one.
  bits.push(s.unlimited || s.remaining == null
    ? "no time limit"
    : `${Math.max(0, Math.round(s.remaining / 60))} minutes left`);
  bits.push(`${(s.guests || []).length} of ${s.slots || "?"} slots`);
  el("session-now").innerHTML = bits.join(" &middot; ");
}

function drawGuests(s) {
  const guests = (s && s.guests) || [];
  el("guests").innerHTML =
    "<tr><th>slot</th><th>name</th><th>state</th><th>inputs</th><th></th></tr>" +
    (guests.length ? guests.map((g) =>
      `<tr><td>${g.slot}</td><td>${esc(g.label || g.name || "—")}</td>` +
      `<td>${esc(g.state || (g.connected ? "connected" : "—"))}</td>` +
      `<td>${g.inputs == null ? "—" : g.inputs}</td>` +
      `<td><button data-kick="${g.slot}">Remove</button></td></tr>`).join("")
     : "<tr><td colspan=5 class=none>nobody is connected</td></tr>");
}

function drawAccounts(state) {
  const list = state.accounts || [];
  el("accounts").innerHTML =
    "<tr><th>name</th><th>may</th><th>devices</th><th></th></tr>" +
    (list.length ? list.map((a) => {
      const caps = (state.capabilities || []).map((c) =>
        `<span class="pill ${a.can.includes(c) ? "on" : ""}" data-can="${a.name}" ` +
        `data-cap="${c}" title="click to change">${c}</span>`).join("");
      return `<tr><td>${esc(a.name)}` +
        (a.name === state.primary ? ' <span class="pill on">primary</span>' : "") +
        `</td><td>${caps}</td><td>${a.devices}` +
        (a.devices ? ` <button data-forget="${esc(a.name)}">sign out</button>` : "") +
        `</td><td><button data-reset2fa="${esc(a.name)}">new authenticator</button>` +
        (a.name === state.primary ? "" :
          ` <button class="danger" data-remove="${esc(a.name)}">remove</button>`) +
        `</td></tr>`;
    }).join("") : "<tr><td colspan=4 class=none>no accounts yet</td></tr>");

  if (!el("add-can").dataset.built) {
    el("add-can").innerHTML = (state.capabilities || []).map((c) =>
      `<label><input type="checkbox" value="${c}"> ${c}</label>`).join("");
    el("add-can").dataset.built = "1";
  }
}

function drawMachine(state) {
  const p = state.picked || {};
  rows(el("picked"), [
    ["capture", p.capture ? `<code>${esc(p.capture)}</code>` : '<span class=none>none</span>'],
    ["encoder", p.encoder ? `<code>${esc(p.encoder)}</code> (${esc(p.encoder_kind)})`
                          : '<span class=none>none</span>'],
    ["sound", p.sound ? `<code>${esc(p.sound)}</code>`
                      : '<span class=none>none — sessions are silent</span>'],
  ]);
  const d = state.diagnostics || {};
  rows(el("diag"), [
    ["platform", esc(d.platform || "—")],
    ["python", esc(d.python || "—")],
    ["gstreamer", esc(d.gstreamer || "—")],
    ["virtual pads", esc(d.pads || "—")],
    ["uptime", d.uptime == null ? "—" : Math.round(d.uptime / 60) + " minutes"],
    ["refused requests to this page", state.refused],
    ["failed logins since start", d.bad_logins == null ? "—" : d.bad_logins],
    ["rejected PINs since start", d.bad_pins == null ? "—" : d.bad_pins],
  ]);
}

function fillControls(s) {
  if (!s) return;
  const set = (id, v) => { const n = el(id); if (n && document.activeElement !== n && v != null) n.value = v; };
  set("set-link", s.require_link ? "required" : "open");
  set("set-slots", s.slots);
  set("set-limit", s.limit);
  set("set-url", s.public_url || "");
  set("set-share", s.share_pads ? "on" : "off");
  // "" rather than "off" when nothing is locked, which is what the host says.
  set("set-lock", s.locked || "off");
  set("set-policy", (s.launch || {}).policy);
}

/* The picture, which comes from this page's own endpoint rather than from
   status -- `stream` is not a control-channel command, it arrives over the
   guest socket, so the settings are not in the session summary at all. */
function fillPicture(stream, policies) {
  if (!stream) return;
  const set = (id, v) => { const n = el(id); if (n && document.activeElement !== n && v != null) n.value = v; };
  const policy = el("set-policy");
  if (policy && !policy.dataset.built && policies && policies.length) {
    policy.innerHTML = policies.map((p) => `<option value="${p}">${p}</option>`).join("");
    policy.dataset.built = "1";
  }
  const size = el("set-size");
  if (size && !size.dataset.built) {
    // Height carries width: the two are set together from a named size, so
    // there is no way to ask the host for 1920x480.
    size.innerHTML = [[1920, 1080], [1600, 900], [1280, 720], [960, 540], [854, 480]]
      .map(([w, h]) => `<option value="${h}">${w}x${h}</option>`).join("");
    size.dataset.built = "1";
  }
  set("set-size", stream.height);
  set("set-fps", stream.fps);
  set("set-bitrate", stream.bitrate_kbps);
  const codec = el("set-codec");
  if (codec && !codec.dataset.built) {
    codec.innerHTML = ["auto", "h264", "h265"].map((c) => `<option>${c}</option>`).join("");
    codec.dataset.built = "1";
  }
  set("set-codec", stream.codec);
  const apply = document.querySelector('[data-do="apply-stream"]');
  if (apply) {
    apply.disabled = !stream.can_apply;
    apply.title = stream.can_apply ? "" : "there is no session to rebuild";
  }
}

/* ---- loading ---- */

/* Whether this browser may see anything, asked before anything is drawn.

   The token got it to the page; an account gets it past this. Until the first
   account exists there is nothing to ask for, so the page opens -- and says
   that is why, rather than looking like it forgot to lock the door. */
async function gate() {
  const who = await post("/api/whoami");
  // Three states, and only one of them draws the page: nobody has an account
  // yet, somebody does and this browser has not said who it is, or it has.
  if (who.first_run) {
    el("firstrun").hidden = false;
    el("signin").hidden = true;
    el("everything").hidden = true;
    return false;
  }
  el("firstrun").hidden = true;
  const need = who.needs_signin && !who.who;
  el("signin").hidden = !need;
  el("everything").hidden = need;
  if (need) {
    el("signin-why").textContent =
      "Opening this page already proves where you are — it is on the loopback, "
      + "behind a token only this machine can read. This asks who you are. No "
      + "authenticator code: it is what issues them.";
  }
  return !need;
}

async function load() {
  if (!(await gate())) return;
  const state = await post("/api/state");
  STATE = state;
  el("trouble").hidden = !state.trouble;
  if (state.trouble) el("trouble").textContent = state.trouble;
  drawSession(state.session);
  await drawShare(state.session);
  drawGuests(state.session);
  drawAccounts(state);
  drawMachine(state);
  fillControls(state.session);
  fillPicture(state.stream, state.policies);
}

/* ---- doing ---- */

const ACTIONS = {
  async start() {
    const minutes = Number(el("start-minutes").value) || 0;
    const slots = Number(el("start-slots").value) || 3;
    heard(await control("start", {minutes, slots}), "session open");
  },
  async extend() {
    heard(await control("extend", {minutes: Number(el("extend-minutes").value) || 30}),
          "time added");
  },
  async reshare() {
    // The old pair stops working, so say so before doing it rather than after
    // somebody has already sent it to four people.
    if (!confirm("Make a new link and PIN?\n\nThe current ones stop working. "
                 + "Anybody already connected stays connected.")) return;
    shownFor = null;                       // the QR has to be redrawn
    heard(await control("reshare"), "a new link and PIN");
  },
  async "copy-url"() { await clip(STATE.session && STATE.session.url, "the link"); },
  async "copy-both"() {
    const s = STATE.session || {};
    const words = s.require_link
      ? `${s.url}\nPIN: ${s.pin}`
      : `${s.base_url || s.url}\nPIN: ${s.pin}`;
    await clip(words, "the link and PIN");
  },
  async stop() {
    const guests = ((STATE.session || {}).guests || []).length;
    if (!confirm(guests ? `End the session? ${guests} guest(s) are connected.`
                        : "End the session?")) return;
    heard(await control("stop"), "session ended");
  },
  async "apply-access"() {
    const steps = [
      ["link", {set: el("set-link").value}],
      ["slots", {slots: Number(el("set-slots").value)}],
      ["limit", {limit: Number(el("set-limit").value)}],
      ["url", {url: el("set-url").value}],
      ["share", {set: el("set-share").value}],
      ["policy", {policy: el("set-policy").value}],
    ];
    const pin = el("set-pin").value.trim();
    if (pin) steps.push(["pin", {pin}]);
    let bad = 0;
    for (const [cmd, extra] of steps) {
      const answer = await control(cmd, extra);
      if (!answer.ok) { bad++; say(`${cmd}: ${answer.error || "refused"}`, true); }
    }
    if (!bad) say("applied");
  },
  async "apply-lock"() {
    heard(await control("lock", {set: el("set-lock").value}), "applied");
  },
  async "apply-stream"() {
    heard(await post("/api/stream", {settings: {
      height: Number(el("set-size").value),
      fps: Number(el("set-fps").value),
      bitrate_kbps: Number(el("set-bitrate").value),
      codec: el("set-codec").value,
    }}), "the picture is being rebuilt — about a second of held picture");
  },
  async "account-add"() {
    const can = Array.from(el("add-can").querySelectorAll("input:checked"))
      .map((b) => b.value);
    const answer = await post("/api/account/add", {
      name: el("add-name").value,
      password: el("add-password").value,
      can,
    });
    if (!heard(answer)) return;
    el("add-name").value = el("add-password").value = "";
    el("add-can").querySelectorAll("input:checked").forEach((b) => { b.checked = false; });
    showSecret(answer);
  },
  async firstrun() {
    const password = el("fr-password").value;
    if (password !== el("fr-again").value) {
      say("those two passwords are not the same", true);
      return;
    }
    if (password.length < 8) {
      say("a password needs to be at least eight characters", true);
      return;
    }
    const answer = await post("/api/account/add", {
      name: el("fr-name").value, password,
    });
    el("fr-password").value = el("fr-again").value = "";
    if (!heard(answer)) return;
    // The secret is shown before anything else happens, because it is shown
    // once -- and the sign-in that follows will want a code from it.
    el("firstrun").hidden = true;
    showSecret(answer);
    say("account made. Scan the code, then sign in.");
  },

  async signin() {
    const answer = await post("/api/signin", {
      name: el("in-name").value,
      password: el("in-password").value,
    });
    // Cleared whether or not it worked: a password left in a field is a
    // password on the screen of a machine somebody walks away from.
    el("in-password").value = "";
    if (heard(answer, "signed in")) await load();
  },
  async "secret-done"() {
    el("secret").hidden = true;
    el("secret-body").innerHTML = "";     // not left in the document
    await load();                         // which now asks for a sign-in
  },
};

/* Copy, with a fallback. navigator.clipboard is refused on a page that is not
   a secure context, and this one is plain http on the loopback -- which most
   browsers do treat as secure, and not all of them. */
async function clip(text, what) {
  if (!text) { say("there is nothing to copy", true); return; }
  try {
    await navigator.clipboard.writeText(text);
    say(what + " is on the clipboard");
    return;
  } catch (_) { /* fall through */ }
  const box = document.createElement("textarea");
  box.value = text;
  box.style.position = "fixed";
  box.style.opacity = "0";
  document.body.appendChild(box);
  box.select();
  const worked = document.execCommand && document.execCommand("copy");
  box.remove();
  say(worked ? what + " is on the clipboard"
             : "this browser would not copy it — select it by hand", !worked);
}

function showSecret(answer) {
  el("secret-body").innerHTML =
    `<p>Account <strong>${esc(answer.name)}</strong></p>` +
    `<p>secret: <code>${esc(answer.secret)}</code></p>` +
    `<p>or point a camera at this:</p><pre id="qr">drawing…</pre>` +
    `<p class="hint">URI: <code>${esc(answer.uri)}</code></p>`;
  el("secret").hidden = false;
  el("secret").scrollIntoView({behavior: "smooth", block: "center"});
  drawQr(answer.uri);
}

/* The QR code is drawn by the host, which already has the library the command
   line uses -- rather than pulling a second one into this page. */
async function drawQr(uri) {
  const answer = await post("/api/qr", {text: uri});
  el("qr").textContent = answer.ok ? answer.art : "(could not draw one — use the secret above)";
}

/* ---- wiring ---- */

document.addEventListener("click", async (event) => {
  const target = event.target;
  const doing = target.dataset && target.dataset.do;
  if (doing && ACTIONS[doing]) { await ACTIONS[doing](); await load(); return; }
  if (target.dataset && target.dataset.kick) {
    if (!confirm("Remove this guest?")) return;
    heard(await control("kick", {slot: Number(target.dataset.kick)}), "removed");
    await load(); return;
  }
  if (target.dataset && target.dataset.reset2fa) {
    const name = target.dataset.reset2fa;
    if (!confirm(`A new authenticator secret for ${name}?\n\nEvery remembered device is signed out.`)) return;
    const answer = await post("/api/account/reset2fa", {name});
    if (heard(answer)) showSecret(answer);
    await load(); return;
  }
  if (target.dataset && target.dataset.remove) {
    const name = target.dataset.remove;
    if (!confirm(`Remove the account ${name}?`)) return;
    heard(await post("/api/account/remove", {name}), "removed");
    await load(); return;
  }
  if (target.dataset && target.dataset.forget) {
    heard(await post("/api/account/forget_devices", {name: target.dataset.forget}),
          "signed out everywhere");
    await load(); return;
  }
  if (target.dataset && target.dataset.can) {
    // One capability toggled, and the whole list sent -- set_capabilities
    // replaces rather than adds, so sending one would take the rest away.
    const name = target.dataset.can, cap = target.dataset.cap;
    const account = (STATE.accounts || []).find((a) => a.name === name);
    if (!account) return;
    const can = account.can.includes(cap)
      ? account.can.filter((c) => c !== cap)
      : account.can.concat([cap]);
    heard(await post("/api/account/can", {name, can}), `${name} may now: ${can.join(" ") || "nothing"}`);
    await load();
  }
});

el("tabs").addEventListener("click", (event) => {
  const which = event.target.dataset && event.target.dataset.tab;
  if (!which) return;
  for (const button of el("tabs").children) button.classList.toggle("on", button === event.target);
  for (const name of ["session", "guests", "accounts", "picture", "machine"]) {
    el("tab-" + name).hidden = name !== which;
  }
});

load();
setInterval(() => { if (el("secret").hidden) load(); }, 5000);
