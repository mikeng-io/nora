import type { DocumentSpec } from "../catalog";
import { comparison } from "./comparison.ts";
import { evening } from "./evening.ts";
import { report } from "./report.ts";
import { runbook } from "./runbook.ts";
import { trip } from "./trip.ts";

/** Demo documents: invented content, one per scenario in the design (§14). They
 * are fixtures for the viewer and part of the valid corpus, and every one is
 * validated by the same schema a real document is. */
export const DEMOS: Record<string, DocumentSpec> = { trip, report, comparison, evening, runbook };
export const DEMO_NAMES = Object.keys(DEMOS);
