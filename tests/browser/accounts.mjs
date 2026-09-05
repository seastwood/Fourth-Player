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
    sheet: document.getElementById("login-sheet").hidden,
    gateAccount: document.getElementById("gate-account").hidden,
  }));
  check(start.sessionTab, "no Session tab before anybody logs in");
  check(start.sheet, "and the login sheet is out of sight");
  check(start.gateAccount, "and the join screen says nothing about accounts");

  // The host says who is here, and the page opens my own row.
  const own = await page.evaluate(() => {
    mySlot = 0;
    people = [{ slot: 0, name: "Seth", pad: 0, seconds: 5 },
              { slot: 1, name: "Mate", pad: 1, seconds: 5 }];
    personOpen = 0;
    paintPeople();
    const sheet = document.getElementById("login-sheet");
    return { hidden: sheet.hidden,
             inMyRow: document.getElementById("chat-person").contains(sheet),
             offer: !document.getElementById("login-open").hidden,
             form: !document.getElementById("login-form").hidden };
  });
  check(!own.hidden && own.inMyRow, "tapping my own name shows the login sheet there");
  check(own.offer && !own.form, "with a Log in button rather than a form");

  const other = await page.evaluate(() => {
    personOpen = 1;
    paintPeople();
    return { hidden: document.getElementById("login-sheet").hidden,
             inRow: document.getElementById("chat-person")
                      .contains(document.getElementById("login-sheet")) };
  });
  check(other.hidden && !other.inRow, "somebody else's row has no login in it");

  const opened = await page.evaluate(() => {
    personOpen = 0; paintPeople();
    document.getElementById("login-open").click();
    return { form: !document.getElementById("login-form").hidden,
             offer: !document.getElementById("login-open").hidden };
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
    loggedIn({ t: "loggedin", name: "seth", fresh: true,
               can: ["kick", "lock", "slots", "reshare", "grant", "steam"] });
    limitsFrom({ t: "limits", limit: 4, slots: 4, locked: false, here: 2 });
    return { tab: !document.getElementById("tab-session-pick").hidden,
             says: document.getElementById("login-as").textContent,
             can: document.getElementById("login-can").textContent };
  });
  check(admin.tab, "an account with owner powers gets the Session tab");
  check(/seth/.test(admin.says), "and its own row says who: " + admin.says);
  check(/remove people/i.test(admin.can),
        "and what it may do, in words: " + admin.can);

  const panel = await page.evaluate(() => {
    showTab("session");
    const on = (id) => !document.getElementById(id).hidden;
    return { limit: on("session-limit"), lock: on("session-lock"),
             kick: on("session-kick"), grant: on("session-grant"),
             reshare: on("session-reshare"),
             count: document.getElementById("limit-count").value,
             lockWord: document.getElementById("lock-toggle").textContent.trim(),
             kicks: document.getElementById("kick-list").children.length };
  });
  check(panel.limit && panel.lock && panel.kick && panel.grant && panel.reshare,
        "every part of the panel it was given is drawn");
  check(panel.count === "4", "the limit box shows what the host said: " + panel.count);
  check(/Lock to accounts/.test(panel.lockWord), "and the lock offers to lock");
  check(panel.kicks === 1, "one other person to remove, not including me");

  // A lesser account sees only what it was given.
  const lesser = await page.evaluate(() => {
    loggedIn({ t: "loggedin", name: "mate", fresh: true, can: ["steam:274190"] });
    const on = (id) => !document.getElementById(id).hidden;
    return { tab: !document.getElementById("tab-session-pick").hidden,
             panel: !document.getElementById("tab-session").hidden };
  });
  check(!lesser.tab, "an account given only a game gets no Session tab");
  check(!lesser.panel, "and is put back to the controls if it was looking at one");

  const partial = await page.evaluate(() => {
    loggedIn({ t: "loggedin", name: "mate", fresh: true, can: ["kick"] });
    showTab("session");
    const on = (id) => !document.getElementById(id).hidden;
    return { kick: on("session-kick"), lock: on("session-lock"),
             grant: on("session-grant") };
  });
  check(partial.kick && !partial.lock && !partial.grant,
        "an account given only kick sees only the removing");

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
