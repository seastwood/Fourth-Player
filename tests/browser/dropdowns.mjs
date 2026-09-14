/* That every dropdown on the page draws readable rows.
 *
 * The controller picker came out white on white on a desktop: only the row
 * under the pointer could be read, because only that one gets a background of
 * its own. Its select is `.padpick-select` rather than `.chip`, so it missed
 * the `select.chip option` pairing, and its own `background: transparent`
 * was what the browser's popup took its colours from.
 *
 * What this can and cannot see is worth being straight about. The popup a
 * native select opens is a browser widget, not part of the document -- it
 * cannot be opened, screenshotted or measured from here, so nothing in this
 * file proves what the list actually looks like. What it does prove is that
 * the colours the popup is built from are set and readable, on every select
 * rather than the ones somebody remembered. That is the whole of the bug: an
 * unset colour and a white default underneath it.
 */
import puppeteer from "puppeteer-core";

const fails = [];
const check = (cond, msg) => {
  console.log((cond ? "  ok   " : "  FAIL ") + msg);
  if (!cond) fails.push(msg);
};

// 4.5 is the usual bar for body text. These are short labels at a normal
// weight, so hold them to it rather than the 3.0 allowed for large text.
const WANT_CONTRAST = 4.5;

const browser = await puppeteer.launch({
  executablePath: process.env.FP_CHROME || "/usr/bin/google-chrome",
  args: ["--no-sandbox", "--disable-gpu"],
});

try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.setViewport({ width: 1440, height: 900 });
  await page.goto(process.env.FP_PAGE, { waitUntil: "domcontentloaded" });
  await new Promise((r) => setTimeout(r, 700));

  const found = await page.evaluate(() => {
    const parse = (value) => {
      const bits = (value || "").match(/[\d.]+/g);
      if (!bits) return null;
      const [r, g, b, a] = bits.map(Number);
      return { r, g, b, a: a === undefined ? 1 : a };
    };
    // sRGB relative luminance, the way the contrast formula wants it.
    const lum = (c) => {
      const f = (v) => {
        v /= 255;
        return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
      };
      return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
    };
    const ratio = (a, b) => {
      const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
      return (hi + 0.05) / (lo + 0.05);
    };

    const out = [];
    for (const select of document.querySelectorAll("select")) {
      // Several of these are filled in by the host, so they are empty in the
      // static page. Give them a row to measure; the styling is what is being
      // read, and that does not depend on the text.
      let temporary = null;
      if (!select.options.length) {
        temporary = document.createElement("option");
        temporary.textContent = "measure me";
        select.appendChild(temporary);
      }
      const option = select.options[0];
      const styleOf = (el) => {
        const s = getComputedStyle(el);
        return { color: parse(s.color), background: parse(s.backgroundColor) };
      };
      const row = styleOf(option);
      const box = styleOf(select);
      // An option with no background of its own falls back to the select's,
      // which is what the popup paints behind it.
      const behind = row.background && row.background.a > 0
        ? row.background : box.background;
      out.push({
        id: select.id || select.className || "(unnamed)",
        optionOpaque: !!(row.background && row.background.a > 0),
        selectOpaque: !!(box.background && box.background.a > 0),
        contrast: behind && behind.a > 0 && row.color
          ? Math.round(ratio(row.color, behind) * 100) / 100 : 0,
      });
      if (temporary) temporary.remove();
    }
    return out;
  });

  check(found.length > 0, `there are selects on the page to check: ${found.length}`);

  for (const select of found) {
    check(select.selectOpaque,
          `#${select.id}: the select has a background of its own, which is `
          + "what the browser builds its popup from");
    check(select.contrast >= WANT_CONTRAST,
          `#${select.id}: its rows are readable without hovering them -- `
          + `contrast ${select.contrast}, wanted ${WANT_CONTRAST}`);
  }

  check(errors.length === 0, "no script errors: " + errors.join(" | "));
} finally {
  await browser.close();
}

console.log("");
if (fails.length) {
  console.log(`dropdowns: ${fails.length} FAILED`);
  process.exit(1);
}
console.log("dropdowns: all ok");
