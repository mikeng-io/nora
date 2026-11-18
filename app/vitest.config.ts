import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  test: { environment: "node", include: ["src/**/*.test.ts", "dev/**/*.test.ts"],
    // `next-auth` imports `next/server` by bare specifier, which Node ESM
    // cannot resolve; inlining lets Vite resolve it so tests can mock it.
    server: { deps: { inline: ["next-auth"] } } },
});
