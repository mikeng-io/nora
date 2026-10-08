// Small pure helpers for the Artifacts pages, apart from the views so they are tested.

/** Raster pictures the portal may show in place (nora/web/artifacts.py INLINE_TYPES).
 * Anything else is offered as a download only, never drawn: an SVG can carry script. */
const PREVIEW_IMAGES = new Set(["image/png", "image/jpeg", "image/gif", "image/webp"]);

export type Preview = "image" | "audio" | "video" | "none";

/** How the detail page may show a file before it is downloaded. */
export function previewOf(mediaType: string): Preview {
  const type = mediaType.toLowerCase();
  if (PREVIEW_IMAGES.has(type)) return "image";
  if (type.startsWith("audio/")) return "audio";
  if (type.startsWith("video/")) return "video";
  return "none";
}

/** A short name for a file's type: its subtype in capitals ("PNG", "PDF", "CSV"), or the
 * whole type when it has no subtype. */
export function typeLabel(mediaType: string): string {
  const [major, minor] = mediaType.toLowerCase().split("/");
  if (!minor) return mediaType;
  if (minor === "octet-stream") return "FILE";
  const plain = minor.replace(/^x-/, "").replace(/\+xml$/, "");
  if (plain === "mpeg" && major === "audio") return "MP3";
  return plain.slice(0, 8).toUpperCase();
}
