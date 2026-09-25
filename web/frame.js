/* Building one pad frame, kept apart from the page so it can be tested.
 *
 * This is the twin of `fourthplayer/protocol.py`. Two hand-written struct
 * layouts in two languages will drift eventually, so `tests/test_webframe.py`
 * runs this exact file under node and decodes the result with the real Python
 * decoder. Nothing here may touch the DOM.
 */
(function (root) {
  "use strict";

  const FRAME_BYTES = 20;
  const VERSION = 1;
  const FLAG_RELEASE_ALL = 0x01;
  /* Motion, as six more signed 16-bit values after the axes: gyro x, y, z then
     accelerometer x, y, z. A flag and a longer frame rather than a new
     VERSION -- see the same note in protocol.py -- so a frame without it is
     byte-for-byte what it always was. */
  const FLAG_MOTION = 0x02;
  const MOTION_BYTES = FRAME_BYTES + 12;
  const BUTTON_COUNT = 17;

  /* The wire's units, which are neither end's own.
     A browser reports rotation in degrees per second and acceleration in
     m/s^2; a DualShock reports raw sensor counts. Sixteenths of a degree per
     second fits +/-2048 deg/s in an int16, and thousandths of gravity fits
     +/-32 g -- both far past what a hand does, both exact at the resolution
     anybody can feel. */
  const GYRO_PER_DEG_SEC = 16;
  const ACCEL_PER_G = 1000;
  const GRAVITY = 9.80665;

  /* The device's frame is not the player's frame.
   *
   * DeviceMotionEvent reports about the *device's* axes: x across the short
   * edge, y along the long one, z out of the glass. Those are fixed to the
   * hardware and take no notice of which way round anybody is holding it. Turn
   * a phone to landscape and x now runs up the screen -- so tilting the top of
   * the screen away from you, which is pitch however you hold it, arrives as
   * rotation about x in portrait and about y in landscape.
   *
   * Left uncorrected, a guest who rotated their phone would find their aim
   * had swapped pitch for roll, with nothing on either end to say why. So the
   * pair is rotated into the frame the screen is actually in.
   *
   * z is untouched: the screen turning about its own normal does not move the
   * normal.
   *
   * The angles are the ones screen.orientation.angle reports -- how far the
   * *content* is rotated from the device's natural orientation -- and the
   * mapping below is that rotation undone. Getting a sign wrong here is an
   * axis that reads backwards in one orientation and correctly in another,
   * which is why the four cases are written out and tested rather than
   * derived from a sine at runtime. */
  function toScreenFrame(x, y, angle) {
    switch (((angle % 360) + 360) % 360) {
      case 90: return [-y, x];
      case 180: return [-x, -y];
      case 270: return [y, -x];
      default: return [x, y];
    }
  }

  /* Which way round the screen is, from whichever of the two the browser has.
     screen.orientation is the current one; window.orientation is what older
     Safari has and is deprecated rather than absent. Neither means 0, which
     is right for a desktop that cannot turn at all. */
  function screenAngle(win) {
    const w = win || (typeof window !== "undefined" ? window : null);
    if (!w) return 0;
    try {
      const o = w.screen && w.screen.orientation;
      if (o && typeof o.angle === "number") return o.angle;
    } catch (_) { /* some browsers throw reading it in a frame */ }
    const legacy = Number(w.orientation);
    return Number.isFinite(legacy) ? legacy : 0;
  }

  /* One motion sample from what a browser hands over, in wire units.
     `rotation` is a DeviceMotionEvent.rotationRate (deg/s) and `accel` an
     accelerationIncludingGravity (m/s^2) -- including gravity, deliberately:
     it is what tells a game which way is down, and a DualShock's
     accelerometer reads gravity too.

     `angle` is how far the screen is turned; see toScreenFrame. */
  /* Whether to rotate the browser's readings into the screen's frame. On.
   *
   * It was off, on the strength of this reasoning: "with the rotation
   * applied, portrait was correct and landscape had x and y swapped, which is
   * the signature of rotating something already rotated". The observation was
   * real. The conclusion did not follow, and the giveaway is that turning it
   * off produced *the same symptom*: portrait correct, landscape swapped,
   * reported in those words. A switch whose two positions have the same
   * effect is not the thing being measured -- the axis order on the host was,
   * and it has since been worked out by playing: roll,-pitch,-yaw.
   *
   * In portrait the angle is zero and this correction is the identity, so it
   * cannot touch an order somebody has just confirmed in portrait. Landscape
   * is the only case it changes, which is the case that is wrong.
   *
   * Note what this is *not* for. A physical controller's own gyroscope
   * reports in the controller's frame and is already what a game expects --
   * it must not come through here at all. This function exists for a phone
   * being waved about, and only for that. */
  const ROTATE_TO_SCREEN = true;

  function motionSample(rotation, accel, angle) {
    const g = (v) => clampShort(Math.round((v || 0) * GYRO_PER_DEG_SEC));
    const a = (v) => clampShort(Math.round((v || 0) / GRAVITY * ACCEL_PER_G));
    const turn = !ROTATE_TO_SCREEN ? 0
                 : (angle === undefined ? screenAngle() : angle);
    // beta/gamma/alpha is x/y/z: beta is rotation about the device's x axis,
    // gamma about y, alpha about z.
    const [gx, gy] = toScreenFrame(g(rotation && rotation.beta),
                                   g(rotation && rotation.gamma), turn);
    const [ax, ay] = toScreenFrame(a(accel && accel.x),
                                   a(accel && accel.y), turn);
    return [gx, gy, g(rotation && rotation.alpha),
            ax, ay, a(accel && accel.z)];
  }

  const clampShort = (v) => Math.max(-32768, Math.min(32767, v | 0));

  /* Eight-way direction from an offset within the d-pad, normalised so the
   * edge of the pad is 1. Kept here, away from the DOM, because it is the part
   * most worth testing: a diagonal must be two directions rather than a fight
   * between them, and the dead zone in the middle has to be big enough that
   * resting a thumb does not steer. */
  const DEADZONE = 0.3;
  const OCTANTS = {
    "0": ["right"], "1": ["down", "right"], "2": ["down"], "3": ["down", "left"],
    "4": ["left"], "-4": ["left"], "-3": ["up", "left"], "-2": ["up"],
    "-1": ["up", "right"],
  };

  function direction(dx, dy) {
    if (Math.hypot(dx, dy) < DEADZONE) return [];
    const octant = Math.round(Math.atan2(dy, dx) / (Math.PI / 4));
    return OCTANTS[String(octant)] || [];
  }

  const toAxis = (v) => Math.max(-32768, Math.min(32767, Math.round(v * 32767)));
  const toTrigger = (b) =>
    b ? Math.max(0, Math.min(32767, Math.round(b.value * 32767))) : 0;

  /* What a physical pad is currently saying, as plain numbers. Split out so
   * an on-screen pad can be merged with it -- somebody may hold a phone in one
   * hand and a controller in the other, and neither should cancel the other. */
  function padState(pad) {
    let buttons = 0;
    const axes = [0, 0, 0, 0, 0, 0];
    if (!pad) return { buttons, axes };

    const count = Math.min(pad.buttons.length, BUTTON_COUNT);
    for (let i = 0; i < count; i++) {
      if (pad.buttons[i].pressed) buttons |= (1 << i);
    }
    for (let i = 0; i < 4; i++) axes[i] = toAxis(pad.axes[i] || 0);
    // In the standard mapping the triggers are buttons, and their analogue
    // travel lives in .value rather than in an axis.
    axes[4] = toTrigger(pad.buttons[6]);
    axes[5] = toTrigger(pad.buttons[7]);
    return { buttons, axes };
  }

  function buildRaw(buttons, axes, seq, releaseAll, motion) {
    if (releaseAll) {
      buttons = 0;
      axes = [0, 0, 0, 0, 0, 0];
      // Released means released. A frame that lets go of everything carries
      // no attitude either: the guest has gone, and the last thing they were
      // pointing at is not where anything should be left aiming.
      motion = null;
    }
    const long = Boolean(motion);
    const buffer = new ArrayBuffer(long ? MOTION_BYTES : FRAME_BYTES);
    const view = new DataView(buffer);

    view.setUint8(0, VERSION);
    view.setUint8(1, (releaseAll ? FLAG_RELEASE_ALL : 0)
                     | (long ? FLAG_MOTION : 0));
    view.setUint16(2, seq & 0xffff, true);
    view.setUint32(4, buttons >>> 0, true);
    for (let i = 0; i < 6; i++) view.setInt16(8 + i * 2, axes[i] || 0, true);
    if (long) {
      for (let i = 0; i < 6; i++) {
        view.setInt16(FRAME_BYTES + i * 2, clampShort(motion[i] || 0), true);
      }
    }
    return buffer;
  }

  function buildFrame(pad, seq, releaseAll, motion) {
    const state = padState(releaseAll ? null : pad);
    return buildRaw(state.buttons, state.axes, seq, releaseAll, motion);
  }

  // toAxis is exported because the on-screen sticks produce their own -1..1
  // values and must land on the wire in exactly the units the physical pad
  // does. Doing that conversion in app.js would be a second copy of the
  // protocol, which is the thing this file exists to prevent.
  // Full travel on a trigger, in the units the wire uses. Exported because an
  // on-screen shoulder button has no travel of its own to report and has to
  // stand in for a trigger pressed all the way.
  const TRIGGER_FULL = 32767;

  const api = { buildFrame, buildRaw, padState, direction, toAxis, TRIGGER_FULL,
                DEADZONE, FRAME_BYTES, VERSION, FLAG_RELEASE_ALL, BUTTON_COUNT,
                motionSample, MOTION_BYTES, FLAG_MOTION,
                toScreenFrame, screenAngle, ROTATE_TO_SCREEN,
                GYRO_PER_DEG_SEC, ACCEL_PER_G, GRAVITY };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.FPFrame = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
