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

    // The submission now sits in R2; the scoring workflow sweeps it up on its daily cron
    // (score-from-r2.yml). The Worker does nothing else — no GitHub token, no trigger.
    return json({ submission_id: id, status: "accepted", key }, 201);
  },
};

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json" },
  });
}
