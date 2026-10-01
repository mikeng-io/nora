// Small pure helpers for the history pages (design/artifact §34), apart from the views so
// they are tested.

import { RestoreRefusedError } from "@/lib/api/document-history";
import { isNotFound } from "@/lib/api/client";

/** A revision number from the URL: a positive integer written plainly, else `null` (the
 * page then answers like an unknown document, without asking Nora). */
export function revisionParam(value: string): number | null {
  if (!/^[1-9][0-9]{0,8}$/.test(value)) return null;
  return Number(value);
}

/** Whether revision `n` is an old one: the read-only view, with the banner and Restore. */
export function isOldRevision(n: number, current: number): boolean {
  return n < current;
}

/** The message key for why a restore did not happen. */
export type RestoreProblem = "restoreConflict" | "restoreArchived" | "restoreInvalid" | "restoreGone" | "restoreFailed";

export function restoreProblem(error: unknown): RestoreProblem {
  if (error instanceof RestoreRefusedError) {
    if (error.reason === "conflict") return "restoreConflict";
    if (error.reason === "not_active") return "restoreArchived";
    return "restoreInvalid";
  }
  if (isNotFound(error)) return "restoreGone";
  return "restoreFailed";
}

/** The first characters of a revision's sha256, enough to tell two apart by eye. */
export const shortSha = (sha: string) => sha.slice(0, 8);
