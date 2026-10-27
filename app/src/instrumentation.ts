// Next.js calls register() once per server start (nodejs runtime only — the
// edge runtime gets a separate call we ignore). Warming the singleton here is
// what makes a queued or interrupted download resume on boot: without it the
// runner is built lazily on the first /api/v1/downloads request, so a restart
// would strand every queued job until somebody opened the page.
export function register() {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    void import("@/server/downloads/runner").then((m) => m.downloadRunner());
  }
}
