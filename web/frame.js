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
  const BUTTON_COUNT = 17;

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

  function buildRaw(buttons, axes, seq, releaseAll) {
    const buffer = new ArrayBuffer(FRAME_BYTES);
    const view = new DataView(buffer);

    if (releaseAll) {
      buttons = 0;
      axes = [0, 0, 0, 0, 0, 0];
    }

    view.setUint8(0, VERSION);
    view.setUint8(1, releaseAll ? FLAG_RELEASE_ALL : 0);
    view.setUint16(2, seq & 0xffff, true);
    view.setUint32(4, buttons >>> 0, true);
    for (let i = 0; i < 6; i++) view.setInt16(8 + i * 2, axes[i] || 0, true);
    return buffer;
  }

  function buildFrame(pad, seq, releaseAll) {
    const state = padState(releaseAll ? null : pad);
    return buildRaw(state.buttons, state.axes, seq, releaseAll);
  }

  const api = { buildFrame, buildRaw, padState, direction,
                DEADZONE, FRAME_BYTES, VERSION, FLAG_RELEASE_ALL, BUTTON_COUNT };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.FPFrame = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
