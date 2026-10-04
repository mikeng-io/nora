import type { ReactNode } from "react";
import type { z } from "zod";
import type { components } from "@/lib/documents/catalog";

/** The props of one catalog component, with its defaults applied. */
export type Props<N extends keyof typeof components> = z.infer<(typeof components)[N]["props"]>;
export type Render<N extends keyof typeof components> = { props: Props<N>; children?: ReactNode };
