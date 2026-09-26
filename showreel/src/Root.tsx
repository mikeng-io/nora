// Eight compositions: two languages × four frames. One Showreel component
// recomposes itself for each frame; nothing is cropped from another.
import React from "react";
import { Composition } from "remotion";
import { Showreel, ShowreelProps } from "./Showreel";
import { FPS, TL } from "./lib";

export const FORMATS = {
  "16x9": { width: 1920, height: 1080 },
  "9x16": { width: 1080, height: 1920 },
  "4x5": { width: 1080, height: 1350 },
  "1x1": { width: 1080, height: 1080 },
} as const;

export const Root: React.FC = () => (
  <>
    {(["en", "yue"] as const).flatMap((lang) =>
      Object.entries(FORMATS).map(([name, size]) => (
        <Composition<any, ShowreelProps>
          key={`${lang}-${name}`}
          id={`nora-${lang}-${name}`}
          component={Showreel}
          durationInFrames={Math.round(TL.duration * FPS)}
          fps={FPS}
          width={size.width}
          height={size.height}
          defaultProps={{ lang }}
        />
      )),
    )}
  </>
);
