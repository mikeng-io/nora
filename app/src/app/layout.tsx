import type { ReactNode } from "react";

// The real root is app/[locale]/layout.tsx (it owns <html lang>); this one
// exists because Next.js needs a layout above the API routes too.
export default function RootLayout({ children }: { children: ReactNode }) {
  return children;
}
