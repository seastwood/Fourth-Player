/* The sound report saying something about the sound.
 *
 * Every sound figure in the host's log was taken about one second after the
 * connection came up, and never again. One second is mostly the stream
 * starting: the concealment number is dominated by it, and "the sound is poor
 * after a while" had nothing at all to measure against.
 *
 * So the first report is kept -- a stream that starts badly is worth knowing
 * about -- and a second is taken a minute in, counted against the first, so
 * it says what happened during that minute rather than including the startup
 * that swamps it.
 */
import { readFileSync } from "node:fs";

const app = readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");

let fails = 0;
const check = (c, m) => { console.log((c ? "  ok   " : "  FAIL ") + m); if (!c) fails++; };

const body = app.slice(app.indexOf("let soundTold = false;"),
                       app.indexOf("async function watchMedia"));
check(body.length > 200, "the sound report was found");

console.log("it reports twice and no more");
check(/if \(!soundTold && !soundAgain\)/.test(body),
      "the baseline is only taken on the first pass -- without the soundAgain "
      + "guard the follow-up would rebaseline and schedule another of itself, "
      + "and the log would carry one of these every minute for ever");
check(/setTimeout\(\(\) => \{ soundTold = false; soundAgain = true; \}, 60000\)/
        .test(body),
      "and the follow-up is a minute later");

console.log("\nthe second report is counted against the first");
check(/total - soundFirst\.samples/.test(body)
      && /concealed - soundFirst\.concealed/.test(body)
      && /lost - soundFirst\.lost/.test(body),
      "samples, concealment and loss are all differenced; leaving any of them "
      + "cumulative would mix the startup back in");
// These used to assert that the divisor was clamped to 1 and the concealment
// to 0. That prevented Infinity and produced nonsense instead -- the log
// carried "382700.00% of samples invented" -- so the clamps are gone and the
// difference is simply not used unless it is real.
check(/grown >= 48000/.test(body),
      "a difference is only reported when a real amount of sound arrived, so "
      + "a divisor near zero never reaches the arithmetic at all");
check(/hidden >= 0/.test(body),
      "and never when the concealment counter went backwards, which is what a "
      + "renegotiation does to it");

console.log("\ncounters that restarted are not reported as a measurement");
// A renegotiation restarts them, so the difference can be negative or nearly
// nothing while the concealment difference is large. Clamping the divisor to 1
// stopped that being Infinity and left it nonsense: the log carried
// "382700.00% of samples invented", which reads as a measurement rather than
// as obviously broken.
check(/grown >= 48000 && hidden >= 0/.test(body),
      "the difference is only used when the counters actually went forward");
check(/the counters restarted/.test(body),
      "and otherwise it says so, rather than printing a percentage nobody "
      + "can believe");
check(!/Math\.max\(1, total - soundFirst\.samples\)/.test(body),
      "the divisor is no longer clamped into a number that looks real");

console.log("\nand it says which of the two it is");
check(/the last minute, not the start/.test(body),
      "the line distinguishes itself, so two numbers an order of magnitude "
      + "apart do not look like a contradiction");

console.log("\nthe figures reported are the differenced ones");
check(!/\(sound\.packetsLost \|\| 0\) \+ \" packets lost/.test(body),
      "packets lost is not read straight off the cumulative stat");
check(/\+ \", \" \+ lost \+ \" packets lost/.test(body),
      "it uses the local, possibly-differenced value");

process.exit(fails ? 1 : 0);
