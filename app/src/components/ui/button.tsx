import { forwardRef, type ButtonHTMLAttributes, type ComponentProps } from "react";
import { Link } from "@/i18n/routing";
import { cn } from "@/lib/cn";

// DESIGN.md §7 Buttons: one primary (denim) per view, quiet for the rest,
// outline for a secondary choice, destructive as danger text with an icon.
// 44px targets on touch, 40px where a pointer is precise.
type Variant = "primary" | "quiet" | "outline" | "destructive" | "discord";
type Size = "md" | "sm" | "icon" | "icon-sm";

const base =
  "inline-flex select-none items-center justify-center gap-2 whitespace-nowrap font-semibold " +
  "transition-[background-color,color,border-color,transform,opacity] duration-100 ease-nora " +
  "active:translate-y-px disabled:pointer-events-none disabled:opacity-45 " +
  "[&_svg]:size-4 [&_svg]:shrink-0";

const variants: Record<Variant, string> = {
  primary: "bg-denim text-surface hover:bg-denim-dim",
  quiet: "text-ink-soft hover:bg-hoodie hover:text-ink",
  outline: "border border-seam text-ink hover:bg-hoodie",
  destructive: "border border-hairline text-danger hover:bg-hoodie",
  // Third-party sign-in keeps the third party's own colour: their mark.
  discord: "bg-[#5865F2] text-white hover:bg-[#4752C4]",
};

const sizes: Record<Size, string> = {
  md: "h-11 rounded-control px-4 text-[0.9375rem] lg:h-10",
  sm: "h-9 rounded-control px-3 text-[0.8125rem]",
  icon: "size-11 rounded-control lg:size-10",
  "icon-sm": "size-9 rounded-control",
};

export function buttonClass(variant: Variant = "quiet", size: Size = "md", className?: string) {
  return cn(base, variants[variant], sizes[size], className);
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: Size };

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "quiet", size = "md", className, type = "button", ...props },
  ref,
) {
  return <button ref={ref} type={type} className={buttonClass(variant, size, className)} {...props} />;
});

type LinkProps = ComponentProps<typeof Link> & { variant?: Variant; size?: Size };

export function ButtonLink({ variant = "quiet", size = "md", className, ...props }: LinkProps) {
  return <Link className={buttonClass(variant, size, className)} {...props} />;
}
