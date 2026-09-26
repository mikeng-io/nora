import { NoraAvatar } from "@/components/ui/avatar";
import type { NavItem } from "./nav";

export function NavIcon({ item, active, size = 20 }: { item: NavItem; active: boolean; size?: number }) {
  if (item.icon === "nora") return <NoraAvatar size={size + 2} active={active} />;
  const Icon = item.icon;
  return <Icon aria-hidden="true" strokeWidth={active ? 2.1 : 1.8} style={{ width: size, height: size }} />;
}
