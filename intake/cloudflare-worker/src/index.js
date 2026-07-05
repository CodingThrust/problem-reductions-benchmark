// prb submission intake — a Cloudflare Worker (the `prb submit` endpoint).
//
// It is the write-only, confidential broker: a submitter POSTs their submission.json (which
// carries the answer key — certificate + trajectory) over HTTPS; the Worker checks a bearer
// key and deposits the raw body into a PRIVATE R2 bucket. The submitter never reads the
// bucket. The public repo / leaderboard only ever see the aggregate the scoring worker
// derives later (see .github/workflows/score-from-r2.yml).
//
// Bindings (wrangler.toml): R2 bucket `SUBMISSIONS`.
// Secret: `PRB_API_KEY` (set with `wrangler secret put PRB_API_KEY`).

const MAX_BYTES = 25 * 1024 * 1024; // guard against oversized bodies

export default {
  async fetch(request, env) {
    if (request.method !== "POST") return json({ error: "POST only" }, 405);
    if (new URL(request.url).pathname !== "/submit") return json({ error: "not found" }, 404);

    // Auth — constant-ish bearer check against the configured secret.
    const token = (request.headers.get("authorization") || "").replace(/^Bearer\s+/i, "");
    if (!env.PRB_API_KEY || token !== env.PRB_API_KEY) return json({ error: "unauthorized" }, 401);

    const body = await request.text();
    if (body.length > MAX_BYTES) return json({ error: "submission too large" }, 413);

    // Light shape check — the backend re-verifies every certificate with pred regardless.
    let sub;
    try { sub = JSON.parse(body); } catch { return json({ error: "invalid JSON" }, 400); }
    if (!sub || typeof sub !== "object" || !sub.model || !Array.isArray(sub.results)) {
      return json({ error: "not a submission (need model + results[])" }, 400);
    }

    // Deposit the raw submission (answer key) privately in R2, pending scoring.
    const id = crypto.randomUUID();
    const key = `incoming/${Date.now()}-${id}.json`;
    await env.SUBMISSIONS.put(key, body, {
      httpMetadata: { contentType: "application/json" },
      customMetadata: {
        model: String(sub.model).slice(0, 128),
        submitted_by: String(sub.submitted_by || "").slice(0, 128),
      },
    });

    // Event-driven trigger: nudge the GitHub scoring workflow to pick up the new
    // submission (it still waits for maintainer approval via the `scoring` environment).
    // Best-effort — the submission is already safe in R2, so a dispatch failure is
    // non-fatal (the maintainer can always trigger score-from-r2 manually).
    await triggerScoring(env, key);

    return json({ submission_id: id, status: "accepted", key }, 201);
  },
};

// Fire a GitHub repository_dispatch so score-from-r2.yml runs. Requires:
//   var    GH_DISPATCH_REPO  = "owner/repo"          (wrangler.toml [vars])
//   secret GH_DISPATCH_TOKEN = fine-grained PAT, Contents: write on that repo
// Skips silently if either is unset; never throws (best-effort).
async function triggerScoring(env, key) {
  if (!env.GH_DISPATCH_TOKEN || !env.GH_DISPATCH_REPO) return;
  try {
    await fetch(`https://api.github.com/repos/${env.GH_DISPATCH_REPO}/dispatches`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GH_DISPATCH_TOKEN}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "prb-intake",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ event_type: "prb-submission", client_payload: { key } }),
    });
  } catch (_) {
    /* non-fatal: submission is already in R2; maintainer can trigger manually */
  }
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json" },
  });
}
