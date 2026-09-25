/* Motion from a phone, in the units the wire carries and the frame the player
 * is actually in.
 *
 * Two translations, and each is wrong in a way nothing in a log would show.
 *
 * The units are neither end's own: a browser reports degrees per second and
 * m/s^2, a DualShock reports raw sensor counts. Sixteenths of a degree per
 * second and thousandths of gravity sit in the middle.
 *
 * And the axes. DeviceMotionEvent reports about the *device's* axes -- x
 * across the short edge, y along the long one, z out of the glass -- which are
 * fixed to the hardware and take no notice of which way round anybody is
 * holding it. Turn a phone to landscape and x runs up the screen, so tilting
 * the top of the screen away arrives about x in portrait and about y in
 * landscape. A guest who rotated their phone would find pitch and roll had
 * traded places, with nothing at either end to say why.
 *
 * What is asserted here is the geometry, not a guess at the feel: that the
 * rotation is a rotation. Whether 90 should turn one way or the other is a
 * property of what screen.orientation.angle means on a given browser, and the
 * only honest test of that is a hand and a phone.
 */
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const F = require("../../web/frame.js");
const src = readFileSync(new URL("../../web/frame.js", import.meta.url), "utf8");

let bad = 0;
const check = (cond, what) => {
  console.log((cond ? "  ok   " : "  FAIL ") + what);
  if (!cond) bad += 1;
};

console.log("the units in the middle");
let m = F.motionSample({ beta: 10, gamma: 0, alpha: 0 }, { x: 0, y: 0, z: 0 }, 0);
check(m[0] === 10 * F.GYRO_PER_DEG_SEC,
      "10 deg/s is " + 10 * F.GYRO_PER_DEG_SEC + ", got " + m[0]);
m = F.motionSample({}, { x: 0, y: 0, z: -F.GRAVITY }, 0);
check(m[5] === -F.ACCEL_PER_G,
      "one gravity is " + F.ACCEL_PER_G + ", got " + m[5]);
m = F.motionSample({ beta: 99999 }, {}, 0);
check(m[0] === 32767, "and a wild reading is clamped, not wrapped: " + m[0]);
m = F.motionSample(null, null, 0);
check(m.length === 6 && m.every((v) => v === 0),
      "a browser that reports nothing gives six zeros, not six NaNs: " + m);

console.log("\nthe rotation is a rotation");
// Four right angles are the identity. If any single case has a sign wrong,
// going all the way round will not come home.
for (const [x, y] of [[1, 0], [0, 1], [3, -7]]) {
  let v = [x, y];
  for (const a of [90, 90, 90, 90]) v = F.toScreenFrame(v[0], v[1], a);
  check(v[0] === x && v[1] === y,
        "four quarter turns return (" + x + "," + y + "): got " + v);
}
// Length is preserved, which no sign error can fake.
for (const a of [0, 90, 180, 270]) {
  const [x, y] = F.toScreenFrame(3, 4, a);
  check(Math.hypot(x, y) === 5, "angle " + a + " keeps the magnitude: " + [x, y]);
}
// 90 and 270 are opposites, and 180 is the negation. Both follow from the
// mapping being a rotation and neither survives a transposed case.
check(String(F.toScreenFrame(1, 2, 90).map((v) => -v))
      === String(F.toScreenFrame(1, 2, 270)),
      "90 and 270 are opposite turns");
check(String(F.toScreenFrame(1, 2, 180)) === String([-1, -2]),
      "and 180 is a straight negation");

console.log("\nangles that are not one of the four");
check(String(F.toScreenFrame(1, 2, 450)) === String(F.toScreenFrame(1, 2, 90)),
      "450 is 90 -- the angle is taken modulo a turn");
check(String(F.toScreenFrame(1, 2, -90)) === String(F.toScreenFrame(1, 2, 270)),
      "and -90 is 270, which is what older Safari reports for one landscape");
check(String(F.toScreenFrame(1, 2, 37)) === String([1, 2]),
      "anything not a right angle is left alone rather than half-turned: a "
      + "screen is never at 37 degrees, and guessing is worse than not");

console.log("\nturning the phone turns what it sends, so aim follows the picture");
// This was off, on the reasoning that the browser had already rotated the
// readings: "with that rotation applied, portrait was correct and landscape
// had x and y swapped". The observation was real; the conclusion did not
// follow. Turning it off produced the same symptom -- portrait correct,
// landscape swapped -- and a switch whose two positions have the same effect
// is not the thing being measured. The axis order on the host was.
check(F.ROTATE_TO_SCREEN === true,
      "the page rotates the readings into the frame the screen is in");
const still = { beta: 40, gamma: -15, alpha: 7 };
const gravity = { x: 1, y: -2, z: -9 };
const shapes = [0, 90, 180, 270].map(
  (a) => JSON.stringify(F.motionSample(still, gravity, a)));
check(new Set(shapes).size === 4,
      "one motion, four orientations, four answers");

// The property that makes this safe to turn on: portrait is untouched. An
// axis order confirmed by somebody playing in portrait cannot be disturbed by
// a correction that is the identity there.
check(JSON.stringify(F.motionSample(still, gravity, 0))
      === JSON.stringify(F.motionSample(still, gravity, undefined)),
      "and portrait is the identity, so a confirmed portrait order stands");
// A quarter turn swaps the pair and negates one of them -- a rotation, not a
// swap. A plain swap would be a mirror, and no way of holding a phone makes
// one.
const flat = { beta: 10, gamma: 0, alpha: 0 };
const upright = F.motionSample(flat, {}, 0);
const turned = F.motionSample(flat, {}, 90);
check(turned[1] === upright[0] && turned[0] === -upright[1],
      "a quarter turn moves x into y and negates the other: "
      + upright.slice(0, 2) + " -> " + turned.slice(0, 2));

console.log("\nbut the rotation is kept, for a browser that does need it");
// Deleted, it would have to be worked out again from scratch the first time a
// device reports in its own frame -- and the signs are the hard part.
check(typeof F.toScreenFrame === "function", "toScreenFrame is still here");
check(String(F.toScreenFrame(1, 0, 90)) !== String([1, 0]),
      "and still a rotation rather than a stub that returns its input");

console.log("\nthe screen's own normal is not moved by the screen turning");
for (const a of [0, 90, 180, 270]) {
  check(F.motionSample({ alpha: 50 }, {}, a)[2] === 50 * F.GYRO_PER_DEG_SEC,
        "angle " + a + " leaves z alone");
}
// True of the rotation itself as well as of the path, which is what would
// matter again if it were ever switched back on.
for (const a of [0, 90, 180, 270]) {
  const [x, y] = F.toScreenFrame(0, 0, a);
  check(x === 0 && y === 0, "and a still device stays still at " + a);
}

console.log("\nand the angle is read from whichever the browser has");
check(F.screenAngle({ screen: { orientation: { angle: 90 } } }) === 90,
      "screen.orientation when it is there");
check(F.screenAngle({ orientation: -90 }) === -90,
      "window.orientation when it is not -- older Safari has that one, "
      + "deprecated rather than absent");
check(F.screenAngle({}) === 0,
      "and nothing means nothing turned, which is right for a desktop that "
      + "cannot turn at all");
check(F.screenAngle({ screen: { get orientation() { throw new Error("no"); } } }) === 0,
      "a browser that throws reading it is not a crash");

console.log("\nthe wire carries what was sampled");
const buf = F.buildRaw(0, [0, 0, 0, 0, 0, 0], 1, false,
                       F.motionSample({ beta: 10 }, { z: -F.GRAVITY }, 0));
check(buf.byteLength === F.MOTION_BYTES,
      "a frame with motion is " + F.MOTION_BYTES + " bytes");
const view = new DataView(buf);
check((view.getUint8(1) & F.FLAG_MOTION) !== 0, "and says so in its flags");
check(view.getInt16(20, true) === 160, "with the sample behind the axes");

console.log("\nand the orientation question is written down where it is made");
check(/screen\.orientation\.angle/.test(src),
      "the source names what the angle means");
check(/only honest test|hand and a phone|a hand/.test(src)
      || /getting a sign wrong/i.test(src),
      "and that a sign here is a thing to check on a device, not to derive");

console.log(bad ? `\n${bad} FAILED` : "\nall ok");
process.exit(bad ? 1 : 0);
