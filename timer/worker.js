// KCDT outside timer (Cloudflare Worker, free plan).
// GitHub's own schedule often starts hours late or skips a run, so this Worker asks GitHub to start the
// update on time. It passes if_due=true, so the workflow itself decides: it stops in seconds on a holiday,
// outside the time window, or when today's update is already done. Extra calls are harmless.
//
// Setup (see timer/README.md): secret GH_TOKEN = fine-grained GitHub token for kcxdt/kcxdt.github.io with
// "Actions: Read and write" only. Cron triggers (UTC):
//   5 10,11,13 * * MON-FRI   pre-market brief (06:05 New York summer / winter, plus one retry)
//   40 20,21,23 * * MON-FRI  close update (16:40 New York summer / winter, plus one retry)
//   35 17,19 * * MON-FRI     midday fundamentals refresh (warm.yml; the second one is a retry), plus the
//                            economic data and news refresh (econ.yml), after the 08:30 / 10:00 / 14:00 releases
//   50 13,15 * * MON-FRI     optional: economic data refresh soon after the 08:30 and 10:00 New York releases
// The minute of each trigger says which update it is for, so the hours can change without touching this file.
const REPO = "kcxdt/kcxdt.github.io";

async function start(env, workflow) {
  const res = await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/${workflow}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "kcdt-timer",
    },
    body: JSON.stringify({ ref: "main", inputs: { if_due: "true" } }),
  });
  if (!res.ok) throw new Error(`${workflow}: GitHub answered ${res.status} ${await res.text()}`);
  console.log(`${workflow}: asked GitHub to start`);
}

export default {
  async scheduled(event, env, ctx) {
    const minute = new Date(event.scheduledTime).getUTCMinutes();
    const workflows = { 5: ["premarket.yml"], 40: ["daily.yml"], 35: ["warm.yml", "econ.yml"], 50: ["econ.yml"] }[minute];
    if (!workflows) {
      console.log(`no update is set for minute ${minute}`);
      return;
    }
    ctx.waitUntil(Promise.all(workflows.map((w) => start(env, w))));
  },
  // Opening the Worker's address never starts an update, so nobody else can start one.
  // /quotes?s=AAPL,MSFT gives prices that are at least 15 minutes old, for the site to show (see quotes()).
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.pathname === "/quotes") return quotes(request, url, ctx);
    return new Response("KCDT timer is running.");
  },
};

// ---------- delayed prices ----------
// Yahoo's 5-minute bars, but only bars that ended 15 or more minutes ago are used, so every price shown is
// delayed by at least 15 minutes (15 to 20). Daily signals on the site still come from the closing prices.
const SITES = ["https://kcxdt.github.io", "http://127.0.0.1:4318", "http://localhost:4318"];
const MAX_SYMBOLS = 25; // the free plan allows 50 outside calls per request; the site asks in groups of 25
const DELAY = 15 * 60;
const MEM = new Map();

async function quotes(request, url, ctx) {
  const origin = request.headers.get("Origin") || "";
  const cors = {
    "Access-Control-Allow-Origin": SITES.includes(origin) ? origin : SITES[0],
    Vary: "Origin",
    "Content-Type": "application/json; charset=utf-8",
  };
  if (request.method === "OPTIONS") return new Response(null, { headers: { ...cors, "Access-Control-Allow-Methods": "GET" } });
  const syms = [...new Set((url.searchParams.get("s") || "").toUpperCase().split(",").map((x) => x.trim())
    .filter((x) => /^[A-Z0-9.^=-]{1,12}$/.test(x)))].sort().slice(0, MAX_SYMBOLS);
  if (!syms.length) return new Response(JSON.stringify({ error: "no symbols" }), { status: 400, headers: cors });
  // Each symbol is kept for a minute in this Worker's memory, so many visitors cause few calls to Yahoo.
  // (Cloudflare's own cache does not work on a workers.dev address, so memory is used instead.)
  const now = Math.floor(Date.now() / 1000);
  const q = {};
  await Promise.all(syms.map(async (s) => {
    const m = MEM.get(s);
    if (m && now - m.t < 60) { q[s] = m.v; return; }
    try { q[s] = await delayed(s, now); MEM.set(s, { t: now, v: q[s] }); } catch (e) { console.log(`${s}: ${e.message}`); }
  }));
  if (MEM.size > 2000) MEM.clear();
  const body = JSON.stringify({ t: now, delay: DELAY, q });
  return new Response(body, { headers: cors });
}

async function delayed(sym, now) {
  const ysym = sym.replace(/\./g, "-"); // BRK.B on the site is BRK-B on Yahoo
  const res = await fetch(`https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(ysym)}?range=5d&interval=5m&includePrePost=true`,
    { headers: { "User-Agent": "Mozilla/5.0 (compatible; kcdt-quotes)" } });
  if (!res.ok) throw new Error(`Yahoo answered ${res.status}`);
  return pick((await res.json()).chart.result[0], now);
}

// The last bar that ended at least DELAY seconds ago, the regular-session close before it, and which session it was in.
// Exported for the test in tests/test_worker_quotes.mjs.
export function pick(r, now) {
  const ts = r.timestamp || [], c = (r.indicators.quote[0] || {}).close || [], gran = 300;
  // A bar's close is the price at its end; Yahoo's newest bar is unfinished and stamped with its last trade's time
  const end = (k) => (ts[k] % gran ? ts[k] : ts[k] + gran);
  let i = ts.length - 1;
  while (i >= 0 && (c[i] == null || end(i) > now - DELAY)) i--;
  if (i < 0) return null;
  const tz = r.meta.gmtoffset || 0, day = (t) => Math.floor((t + tz) / 86400);
  // Regular hours are 09:30 to 16:00 New York; Yahoo gives the day's own times in currentTradingPeriod, but for
  // older days only the clock time is the same, so compare the time of day.
  const tod = (t) => (((t + tz) % 86400) + 86400) % 86400;
  const reg = r.meta.currentTradingPeriod && r.meta.currentTradingPeriod.regular;
  const open = reg ? tod(reg.start) : 34200, close = reg ? tod(reg.end) : 57600;
  const inReg = (t) => tod(t) >= open && tod(t) < close;
  const d = day(ts[i]);
  let sess = inReg(ts[i]) ? "reg" : tod(ts[i]) < open ? "pre" : "post";
  // Previous close = last regular-session bar on an earlier day than the price, for pre-market and regular hours;
  // after the close, it is that day's own regular close (so after-hours moves are measured from 16:00).
  let base = null;
  for (let j = i; j >= 0; j--) {
    if (c[j] == null || !inReg(ts[j])) continue;
    if (sess === "post" ? day(ts[j]) <= d : day(ts[j]) < d) { base = c[j]; break; }
  }
  return { p: round(c[i]), at: end(i), sess, prev: base == null ? null : round(base) };
}
const round = (x) => Math.round(x * 10000) / 10000;
