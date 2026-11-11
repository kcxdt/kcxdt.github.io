// Run with: node tests/test_worker_quotes.mjs  (checks the delayed-price picker in timer/worker.js)
import assert from "node:assert/strict";
import { pick } from "../timer/worker.js";

const tz = -5 * 3600, ny = (d, hm) => Date.UTC(2026, 10, d, +hm.slice(0, 2), +hm.slice(3)) / 1000 - tz;
const ts = [], close = [];
for (const d of [9, 10]) for (let t = ny(d, "04:00"); t < ny(d, "20:00"); t += 300) { ts.push(t); close.push(d * 1000 + (t - ny(d, "00:00")) / 300); }
const val = (d, hm) => d * 1000 + (ny(d, hm) - ny(d, "00:00")) / 300;
const r = { timestamp: ts, indicators: { quote: [{ close }] },
  meta: { gmtoffset: tz, currentTradingPeriod: { regular: { start: ny(10, "09:30"), end: ny(10, "16:00") } } } };

let q = pick(r, ny(10, "10:00"));  // regular hours: price from the bar that ended 09:45, change from Monday's close
assert.equal(q.sess, "reg"); assert.equal(q.p, val(10, "09:40")); assert.equal(q.at, ny(10, "09:45")); assert.equal(q.prev, val(9, "15:55"));
q = pick(r, ny(10, "09:00"));      // pre-market
assert.equal(q.sess, "pre"); assert.equal(q.p, val(10, "08:40")); assert.equal(q.prev, val(9, "15:55"));
q = pick(r, ny(10, "17:00"));      // after hours: change from today's 16:00 close
assert.equal(q.sess, "post"); assert.equal(q.p, val(10, "16:40")); assert.equal(q.prev, val(10, "15:55"));
q = pick(r, ny(10, "09:44"));      // only bars that ended by 09:29 count, so still pre-market
assert.equal(q.sess, "pre"); assert.equal(q.p, val(10, "09:20"));
close[close.length - 1] = null;    // a missing last bar is skipped
assert.equal(pick(r, ny(11, "03:00")).p, val(10, "19:50"));
assert.equal(pick(r, ny(9, "04:10")), null); // nothing 15 minutes old yet
console.log("worker quotes: all checks passed");
// Yahoo's unfinished newest bar is stamped with its last trade time: that price counts once it is 15 minutes old
ts.push(ny(10, "20:02") + 28); close.push(42);
assert.equal(pick(r, ny(10, "20:10")).p, val(10, "19:50"));
q = pick(r, ny(10, "20:18"));
assert.equal(q.p, 42); assert.equal(q.at, ny(10, "20:02") + 28);
console.log("worker quotes: unfinished bar checks passed");

// 日內交易資訊: pre-market high/low, regular open, VWAP and volume against the earlier days at the same time
{
  const T = [], O = [], H = [], L = [], C = [], V = [];
  for (const d of [9, 10]) for (let t = ny(d, "04:00"); t < ny(d, "20:00"); t += 300) {
    const reg = t >= ny(d, "09:30") && t < ny(d, "16:00"), pre = t < ny(d, "09:30");
    const px = pre ? 100 + (t - ny(d, "04:00")) / 3600 : 110;  // pre-market climbs 100 -> 105.5, then flat 110
    T.push(t); O.push(px); C.push(px); H.push(px + 1); L.push(px - 1); V.push(reg ? (d === 9 ? 100 : 200) : 5);
  }
  const r2 = { timestamp: T, indicators: { quote: [{ open: O, high: H, low: L, close: C, volume: V }] },
    meta: { gmtoffset: tz, currentTradingPeriod: { regular: { start: ny(10, "09:30"), end: ny(10, "16:00") } } } };
  let x = pick(r2, ny(10, "10:15")).x;  // bars that ended by 10:00 count: 09:30-09:55, six bars
  assert.deepEqual(x.pre, [106.4167, 99]); assert.equal(x.o, 110); assert.equal(x.hi, 111); assert.equal(x.lo, 109);
  assert.equal(x.vwap, 110); assert.equal(x.vol, 1200); assert.equal(x.rv, 2); assert.equal(x.rvn, 1);
  x = pick(r2, ny(10, "09:00")).x;      // pre-market: no regular session yet
  assert.equal(x.o, null); assert.equal(x.vwap, null); assert.equal(x.rv, null); assert.equal(x.pre[1], 99);
  x = pick(r2, ny(10, "18:00")).x;      // after the close: the whole day against whole days
  assert.equal(x.vol, 78 * 200); assert.equal(x.rv, 2);
  console.log("worker quotes: day figure checks passed");
}
