import { NextResponse } from "next/server";
import { checkSession } from "@/lib/require-session";

const NORA_API_BASE = process.env.NORA_API_BASE ?? "http://nora:9100";

// User-scoped (operator, 2026-08-19): calls checkSession() directly, not the
// generic requireApiSession() every other route uses, because this route
// needs the resolved personId itself, not just the allow/deny it implies —
// Nora's own /app/v1/memory now REQUIRES personId and returns only that
// person's entry (nora/web/memory.py's module docstring has the reversal's
// full reasoning).
export async function GET() {
  const check = await checkSession();
  if (check.status !== "ok") {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }

  const res = await fetch(
    `${NORA_API_BASE}/app/v1/memory?personId=${encodeURIComponent(check.personId)}`,
    { cache: "no-store" },
  );
  const body = await res.json();
  return NextResponse.json(body, { status: res.status });
}
