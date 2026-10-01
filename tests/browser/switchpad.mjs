/* A Switch Pro controller's IMU, read from its reports.
 *
 * Why any of this exists: the Gamepad API carries buttons and axes and nothing
 * else, on every browser, for every pad. So a controller with a six-axis IMU
 * inside it is, to an ordinary page, a controller with no IMU -- and a guest on
 * a laptop had no motion at all, because the laptop has no sensors of its own
 * either. The only way in is raw HID.
 *
 * Tested against bytes rather than a device, because the parts that go wrong
 * are arithmetic: which half of the frame is the gyroscope, how many counts
 * make a degree, and whether the offsets account for WebHID taking the report
 * id away. A real pad would tell you none of those were wrong until somebody
 * aimed with it.
 */
import { readFileSync } from "node:fs";

const src = readFileSync(new URL("../../web/switchpad.js", import.meta.url), "utf8");
const root = {};
new Function("self", src)(root);
const S = root.FPSwitch;

let bad = 0;
const check = (cond, what) => {
  console.log((cond ? "  ok   " : "  FAIL ") + what);
  if (!cond) bad += 1;
};

/* A 0x30 report as WebHID hands it over: the report id already removed, so the
   timer is at 0 and the IMU at 12. */
function report(frames) {
  const bytes = new Uint8Array(49);
  frames.forEach((six, i) => {
    const view = new DataView(bytes.buffer);
    six.forEach((v, j) => view.setInt16(S.IMU_AT + i * 12 + j * 2, v, true));
  });
  return new DataView(bytes.buffer);
}

console.log("a Nintendo pad is recognised by what it is");
check(S.padName(0x057e, 0x2009) === "Switch Pro Controller", "the Pro Controller");
check(S.padName(0x057e, 0x2006) !== "", "a left Joy-Con, which has its own IMU");
check(S.padName(0x054c, 0x09cc) === "", "and a DualShock is not one of these");

console.log("\nthe IMU is read from the full report and no other");
check(S.readMotion(S.SIMPLE_REPORT, report([[0, 0, 0, 0, 0, 0]])).length === 0,
      "the cut-down report a pad sends until it is asked has no motion in it");
check(S.readMotion(0x21, report([[0, 0, 0, 0, 0, 0]])).length === 0,
      "nor does a reply to a subcommand");
check(S.readMotion(S.FULL_REPORT, report([[0, 0, 0, 0, 0, 0]])).length === 3,
      "and the full one carries three samples, because the pad reads faster "
      + "than it sends");
check(S.readMotion(S.FULL_REPORT, new DataView(new ArrayBuffer(8))).length === 0,
      "a short report is refused rather than read past its end");

console.log("\nacceleration first in the frame, then rotation");
// The single likeliest way to get this file wrong, and it cannot be caught by
// looking at a pad: both halves are plausible int16s and swapping them gives
// numbers that move when you move it.
const one = S.readMotion(S.FULL_REPORT,
                         report([[4096, 0, 0, 1000, 0, 0]]))[0];
check(Math.abs(one.accelerationIncludingGravity.x - S.GRAVITY) < 0.01,
      "4096 counts is one gravity, in metres per second squared: "
      + one.accelerationIncludingGravity.x.toFixed(3));
check(Math.abs(one.rotationRate.beta - 70) < 0.01,
      "and 1000 counts is 70 degrees a second: " + one.rotationRate.beta);
check(one.rotationRate.gamma === 0 && one.rotationRate.alpha === 0,
      "with nothing bleeding into the axes that did not move");

console.log("\nand it is shaped like the event it stands in for");
// So the existing motion path can take it without knowing where it came from.
// beta/gamma/alpha is rotation about x, y and z, which is what
// DeviceMotionEvent reports and what the wire format already expects.
const keys = Object.keys(one).sort().join(",");
check(keys === "accelerationIncludingGravity,rotationRate",
      "a DeviceMotionEvent's two fields and no others: " + keys);
const spin = S.readMotion(S.FULL_REPORT,
                          report([[0, 0, 0, 100, 200, 300]]))[0].rotationRate;
check(spin.beta < spin.gamma && spin.gamma < spin.alpha,
      "in the frame's own order: x, y, z");

console.log("\nthree samples arrive oldest first");
const batch = S.readMotion(S.FULL_REPORT, report([
  [0, 0, 0, 100, 0, 0], [0, 0, 0, 200, 0, 0], [0, 0, 0, 300, 0, 0]]));
check(batch.length === 3, "all three");
check(batch[0].rotationRate.beta < batch[2].rotationRate.beta,
      "in the order the pad wrote them, so the last is the newest");

console.log("\nand the pad is told what to send, without being made to buzz");
const on = S.enableMotion(1);
const full = S.sendFullReports(2);
check(on[0] === 1 && full[0] === 2,
      "each subcommand carries its own counter");
check(Array.from(on.slice(1, 9)).join(",") === S.NEUTRAL_RUMBLE.join(","),
      "with the rumble bytes present and neutral -- they are not optional, and "
      + "a wrong one buzzes the pad every time it is asked a question");
check(on[9] === 0x40 && on[10] === 0x01, "0x40 turns the IMU on");
check(full[9] === 0x03 && full[10] === S.FULL_REPORT,
      "and 0x03 asks for the report that has room for it");
check(S.subcommand(0x1f, 0, []) [0] === 0x0f,
      "the counter is four bits, so it wraps rather than overflowing into the "
      + "rumble");

console.log("\nand the offsets account for WebHID taking the report id away");
// Every table in the reverse-engineering notes counts the report id as byte 0.
// WebHID hands over everything after it, so each offset here is one less. Off
// by one reads the vibrator byte as half an accelerometer axis.
check(S.IMU_AT === 12,
      "the IMU starts at 12, not 13: " + S.IMU_AT);
check(/WebHID takes the report id off/.test(src),
      "and the file says why, because the next person will check");

console.log("\nand the page can tell when none of this is available");
const app = readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");
check(/function hidMotionPossible\(\)/.test(app),
      "there is one place that asks whether HID is reachable");
check(/!!navigator\.hid/.test(app),
      "by looking for navigator.hid, which Safari and Firefox do not define");
check(/typeof FPSwitch !== "undefined"/.test(app),
      "and for the protocol itself, so a missing script is not a crash");
// The guard that mattered. gyroPossible() is now true on a machine whose only
// motion is a controller -- and on that machine DeviceMotionEvent does not
// exist, so reaching through it for requestPermission is a ReferenceError
// rather than a false. That would have broken the switch on every desktop.
const asks = app.slice(app.indexOf("function gyroNeedsAsking"));
check(/typeof DeviceMotionEvent !== "undefined"\s*\n\s*&& typeof DeviceMotionEvent\.requestPermission/
        .test(asks.slice(0, 400)),
      "and the iOS permission check asks about DeviceMotionEvent itself, not "
      + "about whether any motion is possible");

console.log("\nand turning motion off lets go of the pad");
const stop = app.slice(app.indexOf("function stopGyro"));
check(/hidClosePad\(\);/.test(stop.slice(0, 500)),
      "the listener comes off with the device motion listener");
const close = app.slice(app.indexOf("function hidClosePad"));
check(!/\.close\(\)/.test(close.slice(0, 600)),
      "but the device is left open: closing it drops the pad back to its "
      + "simple report mode and the next start has to do the whole handshake "
      + "again");

console.log("\nand a pad already granted is not asked for twice");
const open = app.slice(app.indexOf("async function hidOpenPad"));
check(open.indexOf("navigator.hid.getDevices()")
      < open.indexOf("navigator.hid.requestDevice"),
      "the devices already granted are looked at before the chooser is shown, "
      + "so doing this once is enough");

console.log(bad ? `\n${bad} FAILED` : "\nall ok");
process.exit(bad ? 1 : 0);
