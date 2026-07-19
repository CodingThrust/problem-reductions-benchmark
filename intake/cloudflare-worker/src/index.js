// prb submission intake — a Cloudflare Worker (the `prb submit` endpoint).
//
// It is the write-only, confidential broker: a submitter POSTs their submission.json (which
// carries the answer key — certificates + submit ledger) over HTTPS; the Worker verifies the
// Cloudflare Access identity and deposits the raw body into a PRIVATE R2 bucket. The
// submitter never reads the bucket. The public repo / leaderboard only ever see the
// aggregate the scoring worker derives later (see .github/workflows/score-from-r2.yml).
//
// Bindings (wrangler.toml): R2 bucket `SUBMISSIONS` plus the Access issuer/audience.

import { createRemoteJWKSet, jwtVerify } from "jose";

const MAX_BYTES = 25 * 1024 * 1024; // guard against oversized bodies
const remoteJwks = new Map();

export default {
  async fetch(request, env) {
    if (request.method !== "POST") return json({ error: "POST only" }, 405);
    if (new URL(request.url).pathname !== "/submit") return json({ error: "not found" }, 404);

    const authentication = await authenticate(request, env);
    if (authentication.response) return authentication.response;
    const identity = authentication.identity;

    const declaredBytes = Number(request.headers.get("content-length"));
    if (Number.isFinite(declaredBytes) && declaredBytes > MAX_BYTES) {
      return json({ error: "submission too large" }, 413);
    }
    const bodyBytes = await request.arrayBuffer();
    if (bodyBytes.byteLength > MAX_BYTES) return json({ error: "submission too large" }, 413);
    const body = new TextDecoder().decode(bodyBytes);

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
        auth_method: identity.method,
        authenticated_subject: identity.subject,
        authenticated_email: identity.email,
      },
    });

    // The submission now sits in R2; the scoring workflow sweeps it up on its daily cron
    // (score-from-r2.yml). The Worker does nothing else — no GitHub token, no trigger.
    return json({ submission_id: id, status: "accepted" }, 201);
  },
};

export async function authenticate(request, env) {
  const assertion = request.headers.get("cf-access-jwt-assertion");
  if (assertion) {
    if (!env.TEAM_DOMAIN || !env.POLICY_AUD) {
      return { response: json({ error: "Access authentication is not configured" }, 503) };
    }
    try {
      const payload = await verifyAccessAssertion(assertion, env);
      return {
        identity: {
          method: "cloudflare-access",
          subject: String(payload.sub || "").slice(0, 128),
          email: String(payload.email || "").slice(0, 128),
        },
      };
    } catch {
      return { response: json({ error: "invalid Access identity" }, 403) };
    }
  }
  return { response: json({ error: "unauthorized" }, 401) };
}

export async function verifyAccessAssertion(token, env) {
  const teamDomain = new URL(env.TEAM_DOMAIN).origin;
  let jwks = remoteJwks.get(teamDomain);
  if (!jwks) {
    jwks = createRemoteJWKSet(new URL(`${teamDomain}/cdn-cgi/access/certs`));
    remoteJwks.set(teamDomain, jwks);
  }
  const { payload } = await jwtVerify(token, jwks, {
    issuer: teamDomain,
    audience: env.POLICY_AUD,
  });
  if (payload.type !== "app" || !payload.sub || !payload.email) {
    throw new Error("Access token has no user identity");
  }
  return payload;
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json" },
  });
}
