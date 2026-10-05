import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { SendThrottle, clientKey, codeSignInEnabled, requestCode, verifyCode } from "./otp";

type Seen = { url?: string; init?: RequestInit };

function nora(status: number, body: unknown, seen: Seen = {}) {
  return (async (url: string | URL | Request, init?: RequestInit) => {
    seen.url = String(url);
    seen.init = init;
    return new Response(JSON.stringify(body), { status });
  }) as typeof fetch;
}

const unreachable = (async () => {
  throw new TypeError("fetch failed");
}) as typeof fetch;

describe("one-time code client", () => {
  beforeEach(() => {
    process.env.NORA_OTP_API_TOKEN = "t0ken";
    process.env.NORA_API_BASE = "http://nora.test:9100";
  });
  afterEach(() => {
    delete process.env.NORA_OTP_API_TOKEN;
    delete process.env.NORA_API_BASE;
  });

  it("asks Nora for a sign-in code with the bearer token and returns the challenge", async () => {
    const seen: Seen = {};
    const id = await requestCode(
      { identifier: "+852 9123 4567", platform: "whatsapp" },
      nora(202, { challengeId: "c-1" }, seen),
    );
    expect(id).toBe("c-1");
    expect(seen.url).toBe("http://nora.test:9100/app/v1/otp/request");
    expect((seen.init?.headers as Record<string, string>).Authorization).toBe("Bearer t0ken");
    expect(JSON.parse(String(seen.init?.body))).toEqual({
      identifier: "+852 9123 4567", platform: "whatsapp", purpose: "sign_in",
    });
  });

  it("returns the person only for a 200 that names one", async () => {
    expect(await verifyCode({ challengeId: "c-1", code: "123456" }, nora(200, { personId: "miles" })))
      .toBe("miles");
    expect(await verifyCode({ challengeId: "c-1", code: "000000" }, nora(401, { error: "refused" })))
      .toBeNull();
    expect(await verifyCode({ challengeId: "c-1", code: "123456" }, nora(200, { personId: "" })))
      .toBeNull();
    expect(await verifyCode({ challengeId: "c-1", code: "123456" }, unreachable)).toBeNull();
  });

  it("is off, and calls nothing, without a token", async () => {
    delete process.env.NORA_OTP_API_TOKEN;
    const seen: Seen = {};
    expect(codeSignInEnabled()).toBe(false);
    expect(await requestCode({ identifier: "miles", platform: "signal" }, nora(202, {}, seen)))
      .toBeNull();
    expect(seen.url).toBeUndefined();
  });
});

describe("the per-visitor limit on sending codes", () => {
  it("lets one address ask three times in fifteen minutes, then not until the window moves", () => {
    const throttle = new SendThrottle();
    const t0 = 1_000_000;
    expect([0, 1, 2].map((i) => throttle.allow("1.2.3.4", t0 + i))).toEqual([true, true, true]);
    expect(throttle.allow("1.2.3.4", t0 + 60_000)).toBe(false);
    expect(throttle.allow("5.6.7.8", t0 + 60_000)).toBe(true);
    expect(throttle.allow("1.2.3.4", t0 + 15 * 60_000 + 3)).toBe(true);
  });

  it("names the visitor by the tunnel's header first, then the first proxy hop", () => {
    const h = (o: Record<string, string>) => new Headers(o);
    expect(clientKey(h({ "cf-connecting-ip": "9.9.9.9", "x-forwarded-for": "1.1.1.1" }))).toBe("9.9.9.9");
    expect(clientKey(h({ "x-forwarded-for": "1.1.1.1, 10.0.0.1" }))).toBe("1.1.1.1");
    expect(clientKey(h({}))).toBe("unknown");
  });
});
