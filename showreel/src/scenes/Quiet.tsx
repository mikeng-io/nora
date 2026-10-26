// 14–20 s: the line leads back to her. "And when there's nothing worth
// saying…" — she pauses, the music stops, "I don't." Then home.
import React from "react";
import { Img, staticFile } from "remotion";
import { Headline, Presence } from "../components";
import {
  Abs,
  COPY,
  CUE,
  FONT,
  Lang,
  NIGHT,
  aspect,
  easeOut,
  expoOut,
  lerp,
  noWidow,
  pick,
  prog,
  smooth,
  uiFont,
  useFmt,
  useT,
} from "../lib";

const N = NIGHT;

// 2D Bauhinia clasp centres, measured on the 2026-10-26 artwork. The pause
// pulse is a graphic accent; no remote, cable or button is depicted.
export const QUIET = {
  wide: { src: "art/quiet-closeup-wide.webp", iw: 1536, ih: 1024, focus: [780, 158] },
  tall: { src: "art/quiet-closeup-tall.webp", iw: 1024, ih: 1536, focus: [314, 318] },
};

export const Quiet: React.FC<{ lang: Lang }> = ({ lang }) => {
  const t = useT();
  const { w, h, u, kind } = useFmt();
  if (t < CUE.collapse + 0.1 || t > CUE.finalChord + 0.2) return null;
  // 4:5 takes the vertical close-up: the wide one would crop her face
  const q = kind === "tall" || kind === "feed" ? QUIET.tall : QUIET.wide;
  const click = CUE.click;
  const s0 = Math.max(w / q.iw, h / q.ih);
  const push = lerp(1.0, 1.16, smooth(prog(t, CUE.collapse + 0.2, click)));
  const s = s0 * push;
  // keep the clasp where it is on screen while the camera pushes in
  const rx0 = (w - q.iw * s0) / 2 + q.focus[0] * s0;
  const ry0 = (h - q.ih * s0) / 2 + q.focus[1] * s0;
  const ox = rx0 - q.focus[0] * s;
  const oy = ry0 - q.focus[1] * s;
  const inP = easeOut(prog(t, CUE.collapse + 0.15, CUE.collapse + 0.8));
  const dark = t >= click ? 1 - Math.pow(prog(t, click, click + 0.06), 0.5) * 0.93 : 1;
  const gone = prog(t, click + 0.05, click + 0.5);
  const ring = prog(t, click, click + 0.8);
  const head = pick(kind, {
    wide: { x: 110, y: 780, w: 900, size: 62 },
    tall: { x: 80, y: 1560, w: 920, size: 66 },
    square: { x: 60, y: 60, w: 960, size: 50 },
    feed: { x: 60, y: 1110, w: 960, size: 54 },
  });
  return (
    <Abs>
      {/* no background of its own: the collapse underneath must stay visible */}
      <div
        style={{
          position: "absolute",
          left: ox,
          top: oy,
          width: q.iw * s,
          height: q.ih * s,
          opacity: inP * (1 - gone),
          filter: `blur(${lerp(22, 0, inP)}px) brightness(${dark})`,
        }}
      >
        <Img src={staticFile(q.src)} style={{ width: "100%", height: "100%" }} />
      </div>
      <Abs
        style={{
          // a scrim where the words sit: the dark side in 16:9, above her in 1:1,
          // below her hands in the vertical frames
          background: `linear-gradient(${kind === "wide" ? "90deg" : kind === "square" ? "180deg" : "0deg"}, rgba(20,17,18,0.92) 0%, rgba(20,17,18,0.6) 24%, rgba(20,17,18,0) ${kind === "wide" ? 55 : 46}%)`,
          opacity: inP * (1 - gone),
        }}
      />
      {/* the pause: a graphic ring from the clasp, with existing audio timing */}
      {ring > 0 && ring < 1 && (
        <div
          style={{
            position: "absolute",
            left: rx0 - 30 * u * (1 + 7 * expoOut(ring)),
            top: ry0 - 30 * u * (1 + 7 * expoOut(ring)),
            width: 60 * u * (1 + 7 * expoOut(ring)),
            height: 60 * u * (1 + 7 * expoOut(ring)),
            borderRadius: "50%",
            border: `${2 * u}px solid ${N.cable}`,
            opacity: 0.8 * (1 - ring),
          }}
        />
      )}
      <Headline
        lang={lang}
        id="vo4"
        x={head.x * u}
        y={head.y * u}
        width={head.w * u}
        size={head.size * u}
        color={N.ink}
        weight={500}
        soft
        outAt={click + 0.02}
      />
    </Abs>
  );
};

export const End: React.FC<{ lang: Lang }> = ({ lang }) => {
  const t = useT();
  const { w, h, u, kind } = useFmt();
  if (t < CUE.click) return null;
  const c = COPY[lang].end;
  const fin = CUE.finalChord;
  const zh = lang === "yue";
  const img = easeOut(prog(t, fin - 0.05, fin + 1.1));
  const word = expoOut(prog(t, fin + 0.05, fin + 0.8));
  const tag = easeOut(prog(t, fin + 0.35, fin + 0.95));
  const line = easeOut(prog(t, fin + 0.55, fin + 1.35));
  const plat = easeOut(prog(t, fin + 0.9, fin + 1.4));
  const fade = prog(t, CUE.fadeOut[0], CUE.fadeOut[1]);
  const L = pick(kind, {
    // the portrait always shows her whole, sneakers included; the words never sit on her
    wide: { img: { right: 60, h: 1080, top: 0 }, tx: 150, ty: 330, word: 200, tag: 60, tw: 900, glow: "78% 55%" },
    tall: { img: { right: 146, h: 1180, top: 40 }, tx: 80, ty: 1290, word: 170, tag: 58, tw: 920, glow: "50% 32%" },
    square: { img: { right: -40, h: 1040, top: 30 }, tx: 60, ty: 500, word: 140, tag: 42, tw: 440, glow: "72% 50%" },
    feed: { img: { right: 253, h: 860, top: 20 }, tx: 60, ty: 905, word: 130, tag: 46, tw: 960, glow: "50% 33%" },
  });
  const iw = L.img.h * aspect("night-portrait") * u;
  const dontId = "vo5";
  const dontOut = fin + 0.15;
  const dot = L.word * 0.085 * u;
  return (
    <Abs style={{ opacity: 1 - fade }}>
      {/* sampled from night-portrait.webp's backdrop (#2B201D left-mid), so the
          plate has no edge; DESIGN.md §8 allows a sampled colour for this */}
      <Abs
        style={{
          opacity: img,
          background: `radial-gradient(ellipse ${kind === "wide" ? "70% 110%" : "90% 60%"} at ${L.glow}, #2B201D 0%, #1D1615 55%, #141112 100%)`,
        }}
      />
      {/* "I don't." in the silence */}
      <Headline
        lang={lang}
        id={dontId}
        x={0}
        y={h / 2 - 50 * u}
        width={w}
        size={72 * u}
        color={N.ink}
        align="center"
        outAt={dontOut}
        whole
      />
      {t >= fin - 0.1 && (
        <>
          <div
            style={{
              position: "absolute",
              right: L.img.right * u,
              top: L.img.top * u,
              width: iw,
              height: L.img.h * u,
              opacity: img,
              transform: `scale(${lerp(1.06, 1.0, img)})`,
              transformOrigin: "50% 70%",
              // soft on all four edges, so the plate has no edge; the bottom
              // keeps her sneakers and loses only the floor below them
              WebkitMaskImage:
                "linear-gradient(to right, transparent 0%, #000 22%, #000 80%, transparent 100%), linear-gradient(to bottom, transparent 0%, #000 10%, #000 93%, transparent 100%)",
              WebkitMaskComposite: "source-in",
              maskComposite: "intersect",
            }}
          >
            <Img src={staticFile("art/night-portrait.webp")} style={{ width: "100%", height: "100%" }} />
          </div>
          <div style={{ position: "absolute", left: L.tx * u, top: L.ty * u, width: L.tw * u }}>
            <div style={{ display: "flex", alignItems: "flex-end", gap: dot * 1.4 }}>
              <div
                style={{
                  fontFamily: FONT.word,
                  fontWeight: 600,
                  fontSize: L.word * u,
                  letterSpacing: "-0.03em",
                  lineHeight: 0.9,
                  color: N.bun,
                  clipPath: `inset(${(1 - word) * 100}% 0 -20% 0)`,
                  transform: `translateY(${(1 - word) * 30 * u}px)`,
                }}
              >
                {c.wordmark}
              </div>
              <div style={{ position: "relative", width: dot * 2, height: dot * 2, marginBottom: L.word * 0.1 * u, opacity: word }}>
                <Presence x={dot} y={dot} r={dot} color={N.blush} pulseAt={CUE.presence} />
              </div>
            </div>
            <div
              style={{
                marginTop: 30 * u,
                fontFamily: uiFont(lang),
                fontWeight: 650,
                fontSize: L.tag * u,
                letterSpacing: zh ? "0.02em" : "-0.025em",
                lineHeight: 1.15,
                color: N.ink,
                opacity: tag,
                transform: `translateY(${(1 - tag) * 24 * u}px)`,
              }}
            >
              {noWidow(c.tagline)}
            </div>
            <div
              style={{
                marginTop: 28 * u,
                height: 2.5 * u,
                width: L.tw * u * 0.72 * line,
                background: N.cable,
                borderRadius: 99,
              }}
            />
            <div
              style={{
                marginTop: 24 * u,
                fontFamily: FONT.ui,
                fontWeight: 500,
                fontSize: L.tag * 0.5 * u,
                color: N.inkMuted,
                opacity: plat,
              }}
            >
              {c.platforms}
            </div>
          </div>
        </>
      )}
    </Abs>
  );
};
