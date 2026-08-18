import BoringAvatar from "boring-avatars";

// Generative identicons (https://boringavatars.com) — deterministic from
// `seed` alone (a person_id, playerKey, or steamId), so the same person
// reads as the same avatar everywhere without storing or fetching a real
// image. Palette picked to sit inside this app's own accent family
// (--accent/--accent-dim's blue-slate register) rather than the library's
// bright defaults, so it doesn't clash with the rest of the UI.
const PALETTE = ["#3d5a80", "#5b7ca3", "#98c1d9", "#e0fbfc", "#ee6c4d"];

export function PersonAvatar({
  seed,
  size = 32,
  variant = "beam",
  className,
}: {
  seed: string;
  size?: number;
  variant?: "beam" | "marble" | "pixel" | "sunset" | "ring" | "bauhaus";
  className?: string;
}) {
  return (
    <div className={className} style={{ width: size, height: size, flexShrink: 0 }}>
      <BoringAvatar size={size} name={seed} variant={variant} colors={PALETTE} square={false} />
    </div>
  );
}
