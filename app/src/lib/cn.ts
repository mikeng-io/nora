import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

// tailwind-merge taught this app's own scale names (globals.css @theme), so
// a caller's `text-meta` replaces a component's `text-body` instead of both
// landing in the class list and the stylesheet order deciding.
const merge = extendTailwindMerge({
  extend: {
    theme: {
      text: ["label", "meta", "body", "title", "lead", "heading", "display"],
      color: [
        "studio",
        "surface",
        "hoodie",
        "hairline",
        "seam",
        "ink",
        "ink-soft",
        "ink-muted",
        "denim",
        "denim-dim",
        "blush",
        "blush-wash",
        "bun",
        "success",
        "caution",
        "danger",
      ],
      radius: ["control", "panel", "sheet"],
      ease: ["nora", "exit"],
    },
  },
});

export function cn(...inputs: ClassValue[]): string {
  return merge(clsx(inputs));
}
