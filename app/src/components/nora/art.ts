// Every image of Nora the portal shows, in one place. The canonical image
// and the cut-out icon are the repo's assets/ (DESIGN.md §2 names them her
// sources), re-encoded as WebP (the icon trimmed to its alpha and 512px) so
// a phone never pulls the 2 MB originals; the poses are the public site's
// renders (site/src/assets/art/, provenance in its README.md), used here
// for empty and error states and never re-drawn.
import canonical from "@/assets/art/nora-canonical-self.webp";
import icon from "@/assets/art/nora-mini-icon.webp";
import nightPortrait from "@/assets/art/night-portrait.webp";
import groupChat from "@/assets/art/group-chat.webp";
import notebook from "@/assets/art/notebook.webp";
import quiet from "@/assets/art/quiet.webp";
import wave from "@/assets/art/wave.webp";
import lost from "@/assets/art/lost.webp";

export const art = { canonical, icon, nightPortrait, groupChat, notebook, quiet, wave, lost };
export type ArtName = keyof typeof art;
