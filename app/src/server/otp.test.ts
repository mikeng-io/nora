import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { codeSignInEnabled, requestCode, verifyCode } from "./otp";

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
