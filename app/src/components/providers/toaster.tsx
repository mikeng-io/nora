"use client";

import { Toaster as Sonner } from "sonner";
import { useTheme } from "@/lib/theme";
import { useIsDesktop } from "@/hooks/use-media";

export function Toaster() {
  const { resolved } = useTheme();
  const desktop = useIsDesktop();
  return (
    <Sonner
      theme={resolved}
      position={desktop ? "bottom-right" : "top-center"}
      offset={desktop ? 24 : 16}
      mobileOffset={{ top: "calc(env(safe-area-inset-top) + 12px)" }}
      toastOptions={{ classNames: { actionButton: "!bg-denim !text-surface !rounded-control" } }}
    />
  );
}
