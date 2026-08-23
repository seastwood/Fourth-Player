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

  const toAxis = (v) => Math.max(-32768, Math.min(32767, Math.round(v * 32767)));
  const toTrigger = (b) =>
    b ? Math.max(0, Math.min(32767, Math.round(b.value * 32767))) : 0;

  function buildFrame(pad, seq, releaseAll) {
    const buffer = new ArrayBuffer(FRAME_BYTES);
    const view = new DataView(buffer);

    let buttons = 0;
    const axes = [0, 0, 0, 0, 0, 0];

    if (pad && !releaseAll) {
      const count = Math.min(pad.buttons.length, BUTTON_COUNT);
      for (let i = 0; i < count; i++) {
        if (pad.buttons[i].pressed) buttons |= (1 << i);
      }
      for (let i = 0; i < 4; i++) axes[i] = toAxis(pad.axes[i] || 0);
      // In the standard mapping the triggers are buttons, and their analogue
      // travel lives in .value rather than in an axis.
      axes[4] = toTrigger(pad.buttons[6]);
      axes[5] = toTrigger(pad.buttons[7]);
    }

    view.setUint8(0, VERSION);
    view.setUint8(1, releaseAll ? FLAG_RELEASE_ALL : 0);
    view.setUint16(2, seq & 0xffff, true);
    view.setUint32(4, buttons >>> 0, true);
    for (let i = 0; i < 6; i++) view.setInt16(8 + i * 2, axes[i], true);
    return buffer;
  }

  const api = { buildFrame, FRAME_BYTES, VERSION, FLAG_RELEASE_ALL, BUTTON_COUNT };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.FPFrame = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
