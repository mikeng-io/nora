import "server-only";
import { auth } from "@/auth";
import { authenticationAssertion, identityBackend, managedSessionSchema,
  managementURL } from "./management-session";

/** Bind portal data requests to the original server-verified account session. */
export const privateApiFetch: typeof fetch = async (input, init) => {
  if (identityBackend() === "file") return fetch(input, init);
  if (typeof input !== "string" || (init?.body !== undefined && typeof init.body !== "string")) {
    throw new Error("Unsupported private API request");
  }
  const url = new URL(input);
  const path = url.pathname + url.search;
  if (input !== managementURL(path) || !url.pathname.startsWith("/app/v1/")) {
    throw new Error("Unexpected private API destination");
  }
  const session = await auth();
  const retained = managedSessionSchema.parse(session?.user?.managedIdentity);
  const assertion = await authenticationAssertion(retained.authentication, {
    purpose: "session", path, method: init?.method ?? "GET", body: init?.body ?? "",
    binding: retained.binding,
  });
  const headers = new Headers(init?.headers);
  headers.set("Authorization", `Nora-Authentication ${assertion}`);
  return fetch(input, { ...init, headers, cache: "no-store", redirect: "error" });
};
