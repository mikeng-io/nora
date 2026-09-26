// The whole reel: seven scenes on one clock, grain over everything.
import React from "react";
import { AbsoluteFill } from "remotion";
import { Grain } from "./components";
import { Lang, NIGHT, useFonts } from "./lib";
import { Memory } from "./scenes/Memory";
import { End, Quiet } from "./scenes/Quiet";
import { Senses } from "./scenes/Senses";
import { Thread } from "./scenes/Thread";

export type ShowreelProps = { lang: Lang };

export const Showreel: React.FC<ShowreelProps> = ({ lang }) => {
  useFonts();
  return (
    <AbsoluteFill style={{ background: NIGHT.bg, overflow: "hidden" }}>
      <Thread lang={lang} />
      <Memory lang={lang} />
      <Senses lang={lang} />
      <Quiet lang={lang} />
      <End lang={lang} />
      <Grain />
    </AbsoluteFill>
  );
};
