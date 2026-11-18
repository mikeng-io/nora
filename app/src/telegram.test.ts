import { beforeAll, afterEach, describe, expect, it, vi } from "vitest";
import { exportJWK, generateKeyPair, SignJWT } from "jose";
import { Auth } from "@auth/core";
import { createTelegramProvider } from "./telegram";

let signingKey: CryptoKey;
let publicJwk: Awaited<ReturnType<typeof exportJWK>>;
const credentials = { clientId: "synthetic-client", clientSecret: "synthetic-secret" };

beforeAll(async () => {
  const keys = await generateKeyPair("RS256");
  signingKey = keys.privateKey;
  publicJwk = { ...await exportJWK(keys.publicKey), kid: "synthetic-key", alg: "RS256" };
});

afterEach(() => vi.unstubAllGlobals());

async function token(claims: Record<string, unknown> = {}, key = signingKey) {
  return new SignJWT({ id: 42, nonce: "synthetic-nonce", name: "Synthetic", ...claims })
    .setProtectedHeader({ alg: "RS256", kid: "synthetic-key" })
    .setIssuer(typeof claims.iss === "string" ? claims.iss : "https://oauth.telegram.org")
    .setAudience(typeof claims.aud === "string" ? claims.aud : credentials.clientId)
    .setSubject("different-oidc-subject").setIssuedAt()
    .setExpirationTime(typeof claims.exp === "number" ? claims.exp : "2m")
    .sign(key);
}

function keyServer() {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = input instanceof Request ? input.url : String(input);
    if (url === "https://oauth.telegram.org/.well-known/jwks.json") {
      return Response.json({ keys: [publicJwk] });
    }
    return new Response("not found", { status: 404 });
  }));
}

function conform() {
  const provider = createTelegramProvider(credentials);
  return (provider.token as { conform(response: Response): Promise<Response> }).conform;
}

describe("Telegram identity proof", () => {
  it("accepts a signed profile id without equating it to OIDC sub", async () => {
    keyServer();
    const response = Response.json({ id_token: await token() });
    expect(await conform()(response)).toBe(response);
    const provider = createTelegramProvider(credentials);
    const user = await provider.profile!({ sub: "different-oidc-subject", id: 42 }, {});
    expect(user.id).toBe("42");
  });

  it.each([undefined, null, "", "042", 0, -1, true, 1.5, Number.MAX_SAFE_INTEGER + 1])(
    "refuses unusable profile id %s instead of falling back to sub", async (id) => {
      const provider = createTelegramProvider(credentials);
      await expect(async () => provider.profile!({ sub: "42", id }, {})).rejects.toThrow(
        "valid user id",
      );
    },
  );

  it("refuses a token signed by an untrusted key", async () => {
    keyServer();
    const wrong = await generateKeyPair("RS256");
    const response = Response.json({ id_token: await token({}, wrong.privateKey) });
    await expect(conform()(response)).rejects.toThrow("Invalid Telegram identity proof");
  });

  it.each([{ iss: "https://wrong.example.invalid" }, { aud: "another-client" }, { exp: 0 }])(
    "refuses wrong issuer, audience or expiry: %s", async (claims) => {
      keyServer();
      const response = Response.json({ id_token: await token(claims) });
      await expect(conform()(response)).rejects.toThrow("Invalid Telegram identity proof");
    },
  );

  it("refuses missing profile scope and nonce claims", async () => {
    keyServer();
    for (const claims of [{ id: undefined }, { nonce: undefined }]) {
      const response = Response.json({ id_token: await token(claims) });
      await expect(conform()(response)).rejects.toThrow("Invalid Telegram identity proof");
    }
  });

  it("does not echo an invalid token into the error", async () => {
    const response = Response.json({ id_token: "synthetic-private-token-marker" });
    await expect(conform()(response)).rejects.toThrow(/^Invalid Telegram identity proof$/);
  });
});

function cookies(response: Response): string[] {
  return response.headers.getSetCookie().map((value) => value.split(";", 1)[0]);
}

async function callbackJourney(wrongNonce: boolean) {
  const signedIn = vi.fn();
  const provider = createTelegramProvider(credentials);
  const config = {
    secret: "synthetic-auth-session-secret-for-local-test-only",
    trustHost: true, basePath: "/api/auth", providers: [provider],
    events: { signIn: signedIn },
    logger: { error: vi.fn(), warn: vi.fn(), debug: vi.fn() },
  };
  const csrf = await Auth(new Request("https://nora.test/api/auth/csrf"), config);
  const { csrfToken } = await csrf.json();
  const start = await Auth(new Request("https://nora.test/api/auth/signin/telegram", {
    method: "POST", headers: {
      "content-type": "application/x-www-form-urlencoded", cookie: cookies(csrf).join("; "),
    }, body: new URLSearchParams({ csrfToken, callbackUrl: "https://nora.test/" }),
  }), config);
  const authorization = new URL(start.headers.get("location")!);
  expect(authorization.origin + authorization.pathname).toBe("https://oauth.telegram.org/auth");
  expect(authorization.searchParams.get("scope")).toBe("openid profile");
  expect(authorization.searchParams.get("code_challenge_method")).toBe("S256");
  const nonce = authorization.searchParams.get("nonce");
  expect(nonce).toBeTruthy();
  const fetcher = vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const url = input instanceof Request ? input.url : String(input);
    if (url === "https://oauth.telegram.org/.well-known/jwks.json") {
      return Response.json({ keys: [publicJwk] });
    }
    if (url === "https://oauth.telegram.org/token") {
      const body = new URLSearchParams(String(options?.body));
      expect(body.get("code_verifier")).toBeTruthy();
      return Response.json({
        access_token: "synthetic-access", token_type: "bearer", expires_in: 120,
        id_token: await token({ nonce: wrongNonce ? "wrong-nonce" : nonce }),
      });
    }
    throw new Error(`Unexpected provider request: ${url}`);
  });
  vi.stubGlobal("fetch", fetcher);
  const url = new URL("https://nora.test/api/auth/callback/telegram");
  url.searchParams.set("code", "synthetic-code");
  url.searchParams.set("state", authorization.searchParams.get("state")!);
  const response = await Auth(new Request(url, {
    headers: { cookie: [...cookies(csrf), ...cookies(start)].join("; ") },
  }), config);
  return { response, signedIn, fetcher };
}

describe("the actual Auth.js callback", () => {
  it("completes a PKCE/state/nonce flow without inventing a UserInfo endpoint", async () => {
    const { response, signedIn, fetcher } = await callbackJourney(false);
    expect(new URL(response.headers.get("location")!).href).toBe("https://nora.test/");
    expect(signedIn).toHaveBeenCalledWith(expect.objectContaining({
      account: expect.objectContaining({ providerAccountId: "42" }),
    }));
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("refuses receiving a valid signed token from a different nonce flow", async () => {
    const { response, signedIn } = await callbackJourney(true);
    expect(response.headers.get("location")).toContain("error=");
    expect(signedIn).not.toHaveBeenCalled();
  });
});
