// `npm run dev:all`: the mock API and the portal's dev server together,
// one Ctrl-C for both. Needs .env.local (see .env.example, "Local
// development").
import { spawn } from "node:child_process";

const procs = [
  spawn("node", ["dev/mock-nora.mjs"], { stdio: "inherit", env: { ...process.env, MOCK_LATENCY_MS: process.env.MOCK_LATENCY_MS ?? "150" } }),
  spawn("npx", ["next", "dev", "--port", process.env.PORT ?? "3400"], { stdio: "inherit" }),
];
const stop = () => procs.forEach((p) => p.kill("SIGTERM"));
process.on("SIGINT", stop);
process.on("SIGTERM", stop);
procs.forEach((p) => p.on("exit", stop));
