// Every image of Nora the portal shows, in one place: copies of masters in
// the private nora-assets repository (assets/visual-assets.json names each
// source and version), re-encoded as WebP so a phone never pulls the 2 MB
// originals. The poses are for empty and error states and never re-drawn.
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
