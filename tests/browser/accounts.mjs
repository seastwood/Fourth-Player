/* Loads the real page in a real browser and drives the account UI with
   fabricated host messages, so what is checked is the page the phone gets
   rather than a reading of the source. */
import puppeteer from "puppeteer-core";



const fails = [];
function check(cond, msg) {
  console.log((cond ? "  ok   " : "  FAIL ") + msg);
  if (!cond) fails.push(msg);
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: process.env.FP_CHROME,
    args: ["--no-sandbox", "--disable-gpu", "--allow-file-access-from-files"],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 390, height: 844, isMobile: true });
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  await page.goto(process.env.FP_PAGE);
  await new Promise((r) => setTimeout(r, 600));

  check(errors.length === 0, "the page loads with no script errors: " + errors.slice(0, 2));

  // Nothing about accounts is visible to somebody who has not logged in.
  const start = await page.evaluate(() => ({
    sessionTab: document.getElementById("tab-session-pick").hidden,
    gateAccount: document.getElementById("gate-account").hidden,
  }));
  check(!start.sessionTab,
        "the Account tab is there before anybody logs in -- a login nobody "
        + "can find is a login nobody has");
  check(start.gateAccount, "and the join screen says nothing about accounts");

  // Findable in the obvious place: open the options panel, and it is a tab.
  const found = await page.evaluate(() => {
    showTab("session");
    const sheet = document.getElementById("login-sheet");
    return { panel: !document.getElementById("tab-session").hidden,
             sheet: !sheet.hidden,
             inPanel: document.getElementById("tab-session").contains(sheet),
             offer: !document.getElementById("login-outside").hidden,
             form: !document.getElementById("login-form").hidden,
             owner: document.getElementById("session-who").textContent };
  });
  check(found.panel && found.sheet && found.inPanel,
        "opening the Account tab shows the login");
  check(found.offer && !found.form, "as a Log in button rather than a form");
  check(found.owner === "",
        "and says nothing about the session to somebody not logged in");

  // The people list is about connections and nothing else now.
  const roster = await page.evaluate(() => {
    mySlot = 0;
    people = [{ slot: 0, name: "Seth", pad: 0, seconds: 5 },
              { slot: 1, name: "Mate", pad: 1, seconds: 5 }];
    personOpen = 0;
    paintPeople();
    return { own: document.getElementById("chat-person").innerHTML,
             sheetMoved: document.getElementById("chat-person")
                           .contains(document.getElementById("login-sheet")) };
  });
  check(!roster.sheetMoved, "the people list carries no login of its own");
  check(/Controller/.test(roster.own), "and still says what it always said");

  const opened = await page.evaluate(() => {
    showTab("session");
    document.getElementById("login-open").click();
    return { form: !document.getElementById("login-form").hidden,
             offer: !document.getElementById("login-outside").hidden };
  });
  check(opened.form && !opened.offer, "Log in opens the form");

  // What the page sends.
  const sent = await page.evaluate(() => {
    const out = [];
    window.send = (m) => out.push(m);
    document.getElementById("login-user").value = "seth";
    document.getElementById("login-pass").value = "a-good-password";
    document.getElementById("login-code").value = "123456";
    document.getElementById("login-remember").checked = true;
    document.getElementById("login-form").dispatchEvent(
      new Event("submit", { cancelable: true }));
    return { out, pass: document.getElementById("login-pass").value };
  });
  check(sent.out.length === 1 && sent.out[0].t === "login",
        "submitting sends one login message: " + JSON.stringify(sent.out[0]));
  check(sent.out[0] && sent.out[0].name === "seth" && sent.out[0].remember === true,
        "carrying the name and the remember tick");
  check(sent.pass === "", "and the password field is cleared whatever the answer");

  // Logged in, with the powers of an owner.
  const admin = await page.evaluate(() => {
    // With the panel closed, which is the case a remembered device lands in:
    // logged in on the load path, nobody having opened the tab yet.
    showTab("controls");
    loggedIn({ t: "loggedin", name: "seth", fresh: true,
               can: ["kick", "lock", "slots", "reshare", "grant", "steam"] });
    limitsFrom({ t: "limits", limit: 4, slots: 4, locked: false, here: 2 });
    return { tab: !document.getElementById("tab-session-pick").hidden,
             says: document.getElementById("login-as").textContent,
             can: document.getElementById("login-can").textContent };
  });
  check(admin.tab, "the Account tab stays put once logged in");
  check(/noken|seth/.test(admin.says),
        "and the sheet is filled in before the tab is opened, not on opening it");
  check(/seth/.test(admin.says), "and says who: " + admin.says);
  check(/remove people/i.test(admin.can),
        "and what it may do, in words: " + admin.can);

  const panel = await page.evaluate(() => {
    showTab("session");
    const on = (id) => !document.getElementById(id).hidden;
    return { limit: on("session-limit"), lock: on("session-lock"),
             kick: on("session-kick"), grant: on("session-grant"),
             reshare: on("session-reshare"),
             count: document.getElementById("limit-count").value,
             lockWord: document.getElementById("lock-now").textContent.trim(),
             lockLit: [...document.querySelectorAll(".acct-choices .choice")]
                        .filter((b) => b.classList.contains("is-on")).length,
             kicks: document.getElementById("kick-list").children.length };
  });
  check(panel.limit && panel.lock && panel.kick && panel.grant && panel.reshare,
        "every part of the panel it was given is drawn");
  check(panel.count === "4", "the limit box shows what the host said: " + panel.count);
  check(/link and PIN/i.test(panel.lockWord),
        "the panel says who may join, in words: " + panel.lockWord);
  check(panel.lockLit === 1,
        "and exactly one of the three settings is shown as the current one");
  check(panel.kicks === 1, "one other person to remove, not including me");

  // A lesser account sees only what it was given.
  const lesser = await page.evaluate(() => {
    loggedIn({ t: "loggedin", name: "mate", fresh: true, can: ["steam:274190"] });
    showTab("session");
    const on = (id) => !document.getElementById(id).hidden;
    return { limit: on("session-limit"), lock: on("session-lock"),
             kick: on("session-kick"), grant: on("session-grant"),
             reshare: on("session-reshare"),
             says: document.getElementById("login-can").textContent };
  });
  check(!lesser.limit && !lesser.lock && !lesser.kick && !lesser.grant
        && !lesser.reshare,
        "an account given only a game gets none of the owner's controls");
  check(/Steam/.test(lesser.says),
        "and is told what it does have: " + lesser.says);

  const partial = await page.evaluate(() => {
    loggedIn({ t: "loggedin", name: "mate", fresh: true, can: ["kick"] });
    showTab("session");
    const on = (id) => !document.getElementById(id).hidden;
    return { kick: on("session-kick"), lock: on("session-lock"),
             grant: on("session-grant") };
  });
  check(partial.kick && !partial.lock && !partial.grant,
        "an account given only kick sees only the removing");

  // An action that needs a fresh code asks for one, and finishes afterwards.
  const asked = await page.evaluate(() => {
    loggedIn({ t: "loggedin", name: "seth", fresh: false,
               can: ["kick", "lock", "slots", "reshare", "grant", "steam"] });
    limitsFrom({ t: "limits", limit: 4, slots: 4, locked: "", here: 2 });
    const out = [];
    window.send = (m) => out.push(m);
    gate.hidden = true;                 // in the session, as you would be
    showTab("session");
    window.confirm = () => true;
    document.getElementById("lock-named").click();
    onError({ t: "error", reason: "code",
              message: "Enter your authenticator code first." });
    return { sent: out, form: !document.getElementById("login-form").hidden,
             note: document.getElementById("login-code-note").textContent,
             noteShown: !document.getElementById("login-code-note").hidden,
             tab: !document.getElementById("tab-session").hidden };
  });
  check(asked.sent.length === 1 && asked.sent[0].t === "lock",
        "the lock was asked for: " + JSON.stringify(asked.sent));
  check(asked.form && asked.noteShown,
        "being told a code is needed opens the login and says so");
  check(/authenticator/i.test(asked.note), "in the host's words: " + asked.note);

  const finished = await page.evaluate(() => {
    const out = [];
    window.send = (m) => out.push(m);
    loggedIn({ t: "loggedin", name: "seth", fresh: true,
               can: ["kick", "lock", "slots", "reshare", "grant", "steam"] });
    return { sent: out,
             note: !document.getElementById("login-code-note").hidden };
  });
  check(finished.sent.some((m) => m.t === "lock"),
        "and giving the code finishes what was asked for: "
        + JSON.stringify(finished.sent));
  check(!finished.note, "the request for a code is taken down");

  const notTwice = await page.evaluate(() => {
    const out = [];
    window.send = (m) => out.push(m);
    loggedIn({ t: "loggedin", name: "seth", fresh: true, can: ["lock"] });
    return out;
  });
  check(!notTwice.some((m) => m.t === "lock"),
        "and it is not done again on the next login: " + JSON.stringify(notTwice));

  // Locked, and the join screen offering a way back in.
  const shut = await page.evaluate(() => {
    onError({ t: "error", reason: "shut",
              message: "This session is open to named accounts only." });
    return { fields: !document.getElementById("gate-account").hidden,
             why: document.getElementById("gate-account-why").textContent,
             gate: !document.getElementById("gate").hidden };
  });
  check(shut.fields, "being refused from a locked session offers the account fields");
  check(shut.gate, "on the join screen, which is put back up");
  check(/named accounts/.test(shut.why), "with the host's reason: " + shut.why);

  // A Steam game nobody gave them.
  const held = await page.evaluate(() => {
    holdInput({ t: "hold", held: true, driving: false,
                because: "Broforce is a Steam game, and you have not been given it." });
    return document.getElementById("notice").innerHTML;
  });
  check(/Controls paused/.test(held), "a held controller says so");
  check(/Broforce/.test(held), "and says which game: " + held.replace(/<[^>]+>/g, " ").slice(0, 120));
  check(!/television is in a menu/.test(held),
        "and does not claim the television is in a menu");

  const menu = await page.evaluate(() => {
    holdInput({ t: "hold", held: true, driving: false, driver_label: "" });
    return document.getElementById("notice").innerHTML;
  });
  check(/television is in a menu/.test(menu),
        "while an ordinary menu hold still says what it always did");

  check(errors.length === 0, "and nothing threw along the way: " + errors.slice(0, 3));
  await browser.close();
  console.log("");
  if (fails.length) { console.log(fails.length + " FAILED"); process.exit(1); }
  console.log("all good");
})();
