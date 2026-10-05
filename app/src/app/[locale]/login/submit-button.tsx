"use client";

import type { ReactNode } from "react";
import { useFormStatus } from "react-dom";
import { buttonClass } from "@/components/ui/button";

/** A form's submit button that says it is working while the redirect to
 * Discord is being prepared, and cannot be pressed twice. */
export function SubmitButton({
  children,
  pendingLabel,
  variant = "primary",
  size = "md",
  name,
  value,
}: {
  children: ReactNode;
  pendingLabel?: string;
  variant?: "primary" | "discord" | "outline";
  size?: "md" | "sm";
  // For a form with more than one submit: which one was pressed.
  name?: string;
  value?: string;
}) {
  const { pending } = useFormStatus();
  return (
    <button
      type="submit"
      name={name}
      value={value}
      disabled={pending}
      aria-busy={pending}
      className={buttonClass(variant, size, size === "md" ? "h-12 w-full text-base lg:h-12" : "")}
    >
      {pending ? (
        <>
          <span
            className="size-4 rounded-full border-2 border-current border-r-transparent animate-spin"
            aria-hidden="true"
          />
          {pendingLabel ?? children}
        </>
      ) : (
        children
      )}
    </button>
  );
}
