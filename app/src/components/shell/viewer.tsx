"use client";

import { createContext, useContext, type ReactNode } from "react";

export interface Viewer {
  personId: string;
  displayName: string;
  image: string | null;
  // How they signed in, for the account chip: the person is the same either
  // way (DEC-287).
  via: "discord" | "code";
}

const ViewerContext = createContext<Viewer | null>(null);

export function ViewerProvider({ viewer, children }: { viewer: Viewer; children: ReactNode }) {
  return <ViewerContext.Provider value={viewer}>{children}</ViewerContext.Provider>;
}

/** The signed-in person. Only rendered inside the (portal) layout, which
 * has already checked the session and the allowlist. */
export function useViewer(): Viewer {
  const v = useContext(ViewerContext);
  if (!v) throw new Error("useViewer outside the portal layout");
  return v;
}
