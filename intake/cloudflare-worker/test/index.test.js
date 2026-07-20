import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { exportJWK, generateKeyPair, SignJWT } from "jose";

import worker from "../src/index.js";

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

function submissionRequest(headers = {}, submission = {
  model: "test/model", submitted_by: "claimed", shortlist: [], triage: {}, episodes: [],
}) {
  return new Request("https://intake.example/submit", {
    method: "POST",
    headers: { "content-type": "application/json", ...headers },
    body: JSON.stringify(submission),
  });
}

function environment(overrides = {}) {
  const writes = [];
  return {
    writes,
    env: {
      SUBMISSIONS: {
        async put(key, body, options) {
          writes.push({ key, body, options });
        },
      },
      ...overrides,
    },
  };
}

async function accessToken({
  expectedAudience = "expected-aud",
  tokenAudience = expectedAudience,
  identity = { email: "submitter@example.com" },
  subject = "access-user-id",
} = {}) {
  const teamDomain = `https://test-${crypto.randomUUID()}.cloudflareaccess.com`;
  const { publicKey, privateKey } = await generateKeyPair("RS256");
  const jwk = await exportJWK(publicKey);
  Object.assign(jwk, { kid: "test-key", alg: "RS256", use: "sig" });
  globalThis.fetch = async (url) => {
    assert.equal(String(url), `${teamDomain}/cdn-cgi/access/certs`);
    return Response.json({ keys: [jwk] });
  };
  const token = await new SignJWT({ type: "app", ...identity })
    .setProtectedHeader({ alg: "RS256", kid: "test-key" })
    .setIssuer(teamDomain)
    .setAudience(tokenAudience)
    .setSubject(subject)
    .setExpirationTime("5m")
    .sign(privateKey);
  return { token, teamDomain, expectedAudience };
}

test("accepts a valid Access assertion and records the authenticated identity", async () => {
  const auth = await accessToken();
  const { env, writes } = environment({
    TEAM_DOMAIN: auth.teamDomain,
    POLICY_AUD: auth.expectedAudience,
  });

  const response = await worker.fetch(
    submissionRequest({ "cf-access-jwt-assertion": auth.token }), env);

  assert.equal(response.status, 201);
  assert.equal(writes.length, 1);
  assert.deepEqual(writes[0].options.customMetadata, {
    model: "test/model",
    submitted_by: "claimed",
    authenticated_subject: "access-user-id",
    authenticated_email: "submitter@example.com",
  });
});

test("rejects an assertion issued for a different Access application", async () => {
  const auth = await accessToken({ tokenAudience: "wrong-aud" });
  const { env, writes } = environment({
    TEAM_DOMAIN: auth.teamDomain,
    POLICY_AUD: auth.expectedAudience,
  });

  const response = await worker.fetch(submissionRequest({
    "cf-access-jwt-assertion": auth.token,
  }), env);

  assert.equal(response.status, 403);
  assert.equal(writes.length, 0);
});

test("rejects a valid application token with no user identity", async () => {
  const auth = await accessToken({ identity: {}, subject: "" });
  const { env, writes } = environment({
    TEAM_DOMAIN: auth.teamDomain,
    POLICY_AUD: auth.expectedAudience,
  });

  const response = await worker.fetch(
    submissionRequest({ "cf-access-jwt-assertion": auth.token }), env);

  assert.equal(response.status, 403);
  assert.equal(writes.length, 0);
});

test("rejects bearer authorization without an Access assertion", async () => {
  const { env, writes } = environment();

  const response = await worker.fetch(
    submissionRequest({ authorization: "Bearer obsolete-secret" }), env);

  assert.equal(response.status, 401);
  assert.equal(writes.length, 0);
});

test("rejects a declared oversized body before Access verification", async () => {
  const { env, writes } = environment();

  const response = await worker.fetch(submissionRequest({
    "cf-access-jwt-assertion": "not-a-jwt",
    "content-length": String(25 * 1024 * 1024 + 1),
  }), env);

  assert.equal(response.status, 413);
  assert.equal(writes.length, 0);
});

test("rejects unauthenticated requests", async () => {
  const { env, writes } = environment();

  const response = await worker.fetch(submissionRequest(), env);

  assert.equal(response.status, 401);
  assert.equal(writes.length, 0);
});

test("rejects an incomplete submission shape", async () => {
  const auth = await accessToken();
  const { env, writes } = environment({
    TEAM_DOMAIN: auth.teamDomain,
    POLICY_AUD: auth.expectedAudience,
  });
  const response = await worker.fetch(submissionRequest(
    { "cf-access-jwt-assertion": auth.token },
    { model: "test/model", episodes: [] },
  ), env);
  assert.equal(response.status, 400);
  assert.equal(writes.length, 0);
});
