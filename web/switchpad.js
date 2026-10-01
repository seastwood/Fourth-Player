/* A Nintendo Switch Pro controller's gyroscope, over WebHID.
 *
 * Why this file exists at all: the Gamepad API carries buttons and axes and
 * nothing else. There is no motion in it, on any browser, for any pad -- so a
 * controller with a six-axis IMU inside it is, to an ordinary web page, a
 * controller with no IMU. The only way to the readings is to open the device
 * as raw HID and speak its protocol, which is what this does.
 *
 * Pure functions only. The device plumbing -- asking for permission, opening,
 * listening -- is in app.js, because it needs a user gesture and the page's
 * own state; everything here is bytes in, numbers out, and can be run in node
 * against captured reports.
 *
 * The protocol is Nintendo's and undocumented; the constants and layouts below
 * come from the reverse-engineering work the Linux hid-nintendo driver and the
 * joycon libraries are built on. Where a number here is a guess rather than a
 * measurement it says so.
 */
(function (root) {
  "use strict";

  /* Nintendo's vendor, and the products that have an IMU.
   *
   * The Joy-Cons are listed because each one has its own gyroscope and a page
   * cannot tell which half somebody is holding; a left Joy-Con waved about is
   * as good a source of rotation as a Pro Controller. The charging grip is
   * there because a pair in the grip enumerates as one device. */
  const NINTENDO = 0x057e;
  const PRODUCTS = {
    0x2006: "Joy-Con (L)",
    0x2007: "Joy-Con (R)",
    0x2009: "Switch Pro Controller",
    0x200e: "Joy-Con charging grip",
  };

  function padName(vendorId, productId) {
    if (vendorId !== NINTENDO) return "";
    return PRODUCTS[productId] || "";
  }

  /* The report the IMU arrives in, and the one the pad sends until it is told
     otherwise. 0x3f is the cut-down "simple HID" mode a Switch pad uses over
     Bluetooth by default: buttons and a hat, no motion at all. 0x30 is the
     full one, sixty times a second, with three IMU samples in each. */
  const FULL_REPORT = 0x30;
  const SIMPLE_REPORT = 0x3f;

  /* Where the IMU sits in a 0x30 report, as WebHID presents it.
   *
   * WebHID takes the report id off and hands over the rest, so every offset
   * here is one less than the tables in the reverse-engineering notes: the
   * timer is at 0 rather than 1, and the IMU starts at 12 rather than 13. That
   * off-by-one is the single likeliest way to read this file wrong, which is
   * why it is a named constant with this paragraph attached. */
  const IMU_AT = 12;
  const IMU_FRAMES = 3;
  const IMU_FRAME_BYTES = 12;

  /* Counts to real units.
   *
   * Both are the standard sensitivities: the accelerometer at its 8g range and
   * the gyroscope at 2000 degrees a second. A pad's own factory calibration
   * lives in its SPI flash and is not read here -- it trims a few per cent of
   * scale and a small zero offset, and neither changes which way a stick
   * points. If drift at rest ever matters, that is where to go next.
   *
   * Accelerometer in g, because that is what the rest of the page wants to
   * multiply; gyroscope in degrees a second, which is what DeviceMotionEvent
   * reports and therefore what the wire format already expects. */
  const G_PER_COUNT = 1 / 4096;
  const DPS_PER_COUNT = 0.07;
  const GRAVITY = 9.80665;

  /* One IMU sample, in the units DeviceMotionEvent would have used.
   *
   * Shaped deliberately like a DeviceMotionEvent so the existing motion path
   * can take it without knowing where it came from: `rotationRate` as
   * beta/gamma/alpha -- rotation about x, y and z -- and acceleration in metres
   * per second squared, gravity included, because that is what the pad reads
   * and what says which way is down. */
  function readSample(view, at) {
    const int16 = (i) => view.getInt16(at + i * 2, true);
    return {
      // Accelerometer first in the frame, then the gyroscope. The other way
      // round gives numbers that look plausible and are the wrong sensor.
      accelerationIncludingGravity: {
        x: int16(0) * G_PER_COUNT * GRAVITY,
        y: int16(1) * G_PER_COUNT * GRAVITY,
        z: int16(2) * G_PER_COUNT * GRAVITY,
      },
      rotationRate: {
        beta: int16(3) * DPS_PER_COUNT,
        gamma: int16(4) * DPS_PER_COUNT,
        alpha: int16(5) * DPS_PER_COUNT,
      },
    };
  }

  /* Every IMU sample in one report, oldest first, or [] if there are none.
   *
   * Three per report rather than one: the pad samples at about 200 Hz and sends
   * at 60, so it batches. Only the newest is sent on -- the wire carries one
   * attitude per frame and a game asked to apply three in a row would turn
   * three times as far -- but they are all returned, because the two older ones
   * are what a smoother or a rate estimate would want and throwing them away
   * here would be the sort of decision that is hard to undo later. */
  function readMotion(reportId, data) {
    if (reportId !== FULL_REPORT || !data) return [];
    const view = data.buffer ? data : new DataView(data);
    const need = IMU_AT + IMU_FRAMES * IMU_FRAME_BYTES;
    if (view.byteLength < need) return [];
    const out = [];
    for (let i = 0; i < IMU_FRAMES; i += 1) {
      out.push(readSample(view, IMU_AT + i * IMU_FRAME_BYTES));
    }
    return out;
  }

  /* Whether a report is one of the pad's own, as opposed to a reply to
     something we asked. 0x21 carries subcommand answers and no IMU. */
  function hasMotion(reportId) { return reportId === FULL_REPORT; }

  /* The two things a Switch pad has to be told before it reports any motion.
   *
   * It says nothing about rotation until asked, twice: once to turn the IMU on
   * and once to send the report that has room for it. Neither is optional and
   * the order does not matter, but both must be sent *after* the device opens
   * and they can be refused while it is still settling -- so app.js sends them
   * more than once rather than assuming.
   *
   * Every subcommand rides on output report 0x01, which is the rumble report:
   * a counter, eight bytes of rumble, then the subcommand. The rumble has to
   * be there and has to be neutral, or the pad buzzes every time it is asked a
   * question. */
  const NEUTRAL_RUMBLE = [0x00, 0x01, 0x40, 0x40, 0x00, 0x01, 0x40, 0x40];
  const SUBCOMMAND_REPORT = 0x01;

  function subcommand(counter, id, args) {
    return new Uint8Array([counter & 0x0f].concat(NEUTRAL_RUMBLE, [id],
                                                 args || []));
  }

  /* 0x40: the IMU itself, on. 0x03: which input report to send. */
  function enableMotion(counter) { return subcommand(counter, 0x40, [0x01]); }
  function sendFullReports(counter) {
    return subcommand(counter, 0x03, [FULL_REPORT]);
  }

  root.FPSwitch = {
    NINTENDO, PRODUCTS, padName,
    FULL_REPORT, SIMPLE_REPORT, SUBCOMMAND_REPORT,
    IMU_AT, IMU_FRAMES, IMU_FRAME_BYTES,
    G_PER_COUNT, DPS_PER_COUNT, GRAVITY,
    readMotion, readSample, hasMotion,
    subcommand, enableMotion, sendFullReports, NEUTRAL_RUMBLE,
  };
})(typeof self !== "undefined" ? self : this);
