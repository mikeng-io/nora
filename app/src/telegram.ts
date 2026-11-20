import type { OIDCConfig } from "next-auth/providers";
import { createRemoteJWKSet, jwtVerify } from "jose";

interface TelegramClaims extends Record<string, unknown> {
  sub: string;
  id?: unknown;
  name?: unknown;
  picture?: unknown;
}

/** Telegram's profile id names the Bot API account; OIDC sub does not. */
function telegramUser(profile: TelegramClaims) {
  const id = typeof profile.id === "number" && Number.isSafeInteger(profile.id)
    ? String(profile.id) : profile.id;
  if (typeof id !== "string" || !/^[1-9][0-9]*$/.test(id)) {
    throw new Error("Telegram profile lacks a valid user id");
  }
  return {
    id,
    name: typeof profile.name === "string" ? profile.name : null,
    image: typeof profile.picture === "string" ? profile.picture : null,
  };
}

/** Server-only provider configuration; authentication does not approve a link. */
export function createTelegramProvider(
  credentials: { clientId: string; clientSecret: string },
): OIDCConfig<TelegramClaims> {
  if (!credentials.clientId.trim() || !credentials.clientSecret.trim()) {
    throw new Error("Telegram client credentials are required");
  }
  const keys = createRemoteJWKSet(new URL("https://oauth.telegram.org/.well-known/jwks.json"));
  return {
    id: "telegram",
    name: "Telegram",
    type: "oidc",
    issuer: "https://oauth.telegram.org",
    ...credentials,
    authorization: {
      url: "https://oauth.telegram.org/auth",
      params: { scope: "openid profile" },
    },
    // Telegram has no UserInfo endpoint. Explicit endpoints avoid this
    // Auth.js version's discovery-time requirement for one; the token itself
    // is verified against Telegram's keys before Auth.js checks flow/nonce.
    token: {
      url: "https://oauth.telegram.org/token",
      async conform(response: Response) {
        if (response.status !== 200) return response;
        try {
          const body = await response.clone().json();
          const { payload } = await jwtVerify(body.id_token, keys, {
            algorithms: ["RS256"],
            issuer: "https://oauth.telegram.org",
            audience: credentials.clientId,
            requiredClaims: ["iss", "sub", "aud", "exp", "iat", "nonce", "id"],
          });
          telegramUser(payload as TelegramClaims);
          return response;
        } catch {
          throw new Error("Invalid Telegram identity proof");
        }
      },
    },
    client: { token_endpoint_auth_method: "client_secret_basic", id_token_signed_response_alg: "RS256" },
    checks: ["pkce", "state", "nonce"],
    idToken: true,
    profile: telegramUser,
  };
}
