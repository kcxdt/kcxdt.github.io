// KCDT outside timer (Cloudflare Worker, free plan).
// GitHub's own schedule often starts hours late or skips a run, so this Worker asks GitHub to start the
// update on time. It passes if_due=true, so the workflow itself decides: it stops in seconds on a holiday,
// outside the time window, or when today's update is already done. Extra calls are harmless.
//
// Setup (see timer/README.md): secret GH_TOKEN = fine-grained GitHub token for kcxdt/kcxdt.github.io with
// "Actions: Read and write" only. Cron triggers (UTC):
//   5 10,11,13 * * MON-FRI   pre-market brief (06:05 New York summer / winter, plus one retry)
//   40 20,21,23 * * MON-FRI  close update (16:40 New York summer / winter, plus one retry)
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
    const hour = new Date(event.scheduledTime).getUTCHours();
    ctx.waitUntil(start(env, hour >= 9 && hour < 15 ? "premarket.yml" : "daily.yml"));
  },
  // Opening the Worker's address does nothing on purpose, so nobody else can start updates.
  async fetch() {
    return new Response("KCDT timer is running.");
  },
};
