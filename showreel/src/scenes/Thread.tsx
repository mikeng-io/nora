// 0–8 s: the cold open, the room and the tag. One graphic thread runs through all three:
// it leaves the listening-detail frame, becomes the thread the messages hang on, and the
// line her answer arrives on.
import React from "react";
import { Img, staticFile } from "remotion";
import { Cable, Cutout, Headline, Mono, Plate } from "../components";
import {
  Abs,
  COPY,
  CUE,
  FONT,
  Kind,
  Lang,
  NIGHT,
  aspect,
  clamp01,
  easeOut,
  expoInOut,
  expoOut,
  lerp,
  noWidow,
  personColor,
  pick,
  prog,
  smooth,
  springAt,
  uiFont,
  useFmt,
  useT,
} from "../lib";

const N = NIGHT;

// Accessory-free 2D listening plates, 2026-10-26. The graphic thread aligns
// below the Bauhinia clasp; it is not a physical earphone cable in the art.
export const MACRO = {
  wide: { src: "art/macro-earbud-wide.webp", iw: 1536, ih: 1024, threadX: 728 },
  tall: { src: "art/macro-earbud-tall.webp", iw: 1024, ih: 1536, threadX: 290 },
};

// Chat skins: the room changes, she doesn't.
const SKINS = [
  { name: "Signal", accent: "#3A76F0", bubble: "#24272C", radius: [22, 22, 22, 22], bubbles: true },
  { name: "WhatsApp", accent: "#25D366", bubble: "#1F2C34", radius: [6, 20, 20, 20], bubbles: true },
  { name: "Instagram", accent: "#D6477E", bubble: "#262626", radius: [26, 26, 26, 26], bubbles: true },
  { name: "Discord", accent: "#5865F2", bubble: "rgba(0,0,0,0)", radius: [8, 8, 8, 8], bubbles: false },
];
const skinAt = (t: number) => {
  const r = CUE.reskin;
  return t < r[0] ? 0 : t < r[1] ? 1 : t < r[2] ? 2 : 3;
};

type L = {
  spineX: number;
  colW: number;
  colBottom: number;
  headerY: number;
  text: number;
  nora: { cx: number; bottom: number; h: number };
  head: { x: number; y: number; w: number; size: number };
};
const layout = (kind: Kind, w: number, h: number, u: number): L =>
  pick(kind, {
    wide: {
      spineX: 1040, colW: 800, colBottom: 1010, headerY: 90, text: 38,
      nora: { cx: 470, bottom: 1080, h: 700 },
      head: { x: 110, y: 110, w: 760, size: 100 },
    },
    tall: {
      spineX: 64, colW: 940, colBottom: 1225, headerY: 450, text: 42,
      nora: { cx: 730, bottom: 1895, h: 600 },
      head: { x: 110, y: 150, w: 900, size: 108 },
    },
    square: {
      spineX: 56, colW: 640, colBottom: 1040, headerY: 250, text: 30,
      nora: { cx: 865, bottom: 1070, h: 470 },
      head: { x: 96, y: 56, w: 940, size: 74 },
    },
    feed: {
      spineX: 56, colW: 660, colBottom: 1290, headerY: 300, text: 32,
      nora: { cx: 850, bottom: 1335, h: 560 },
      head: { x: 96, y: 70, w: 940, size: 80 },
    },
  }) as L;

// ── the cold open ───────────────────────────────────────────────────────────
const Open: React.FC<{ t: number; L: L }> = ({ t, L }) => {
  const { w, h, u, kind } = useFmt();
  const m = kind === "tall" ? MACRO.tall : MACRO.wide;
  const s0 = Math.max(w / m.iw, h / m.ih);
  // camera: settle in, then tilt down into the graphic thread
  const push = lerp(1.1, 1.0, expoOut(prog(t, 0, 1.6)));
  const tilt = expoInOut(prog(t, 0.95, 2.15));
  const s = s0 * push;
  const ox = (w - m.iw * s) / 2;
  const oy = (h - m.ih * s) / 2 - tilt * h * 0.62;
  const fade = smooth(prog(t, 0.0, 0.55));
  const focus = lerp(16, 0, easeOut(prog(t, 0.15, 1.0))) + tilt * 10;
  const bottom = oy + m.ih * s;
  return (
    <Abs style={{ background: N.bg }}>
      <div
        style={{
          position: "absolute",
          left: ox,
          top: oy,
          width: m.iw * s,
          height: m.ih * s,
          opacity: fade * (1 - prog(t, 1.75, 2.2)),
          filter: `blur(${focus}px)`,
        }}
      >
        <Img src={staticFile(m.src)} style={{ width: "100%", height: "100%" }} />
        <div
          style={{
            position: "absolute",
            left: 0,
            right: 0,
            bottom: 0,
            height: "48%",
            background: `linear-gradient(to bottom, rgba(20,17,18,0) 0%, rgba(20,17,18,0.55) 45%, ${N.bg} 92%)`,
          }}
        />
      </div>
      {/* the graphic thread starts at the fading frame, not a drawn accessory */}
      <CableRun t={t} L={L} fromX={ox + m.threadX * s} fromY={oy + m.ih * s * 0.8} />
    </Abs>
  );
};

/** The graphic connector from the listening frame to the message spine. */
const CableRun: React.FC<{ t: number; L: L; fromX: number; fromY: number }> = ({ t, L, fromX, fromY }) => {
  const { h, u } = useFmt();
  const [a, b] = CUE.cableDraw;
  const swing = expoInOut(prog(t, 1.55, 2.3));
  const x = lerp(fromX, L.spineX, swing);
  const y0 = lerp(fromY, -20, swing);
  const bow = Math.sin(swing * Math.PI) * 120 * u * Math.sign(L.spineX - fromX || 1);
  const d = `M ${x} ${y0} Q ${x - bow} ${(y0 + h) / 2} ${x} ${h + 40}`;
  return <Cable d={d} draw={easeOut(prog(t, a, b))} width={3.2 * u} color={N.cable} shadow={0.6} length={h * 1.6} />;
};

// ── the room and the tag ────────────────────────────────────────────────────
type Item =
  | { kind: "seed"; who: string; text: string; at: number }
  | { kind: "you"; text: string; mention: string; at: number }
  | { kind: "nora"; text: string; kicker: string; source: string; at: number };

const Header: React.FC<{ t: number; L: L; lang: Lang; x: number; y: number }> = ({ t, L, lang, x, y }) => {
  const { u: u0 } = useFmt();
  const u = u0 * (L.text / 30);
  const c = COPY[lang].room;
  const k = skinAt(t);
  const sk = SKINS[k];
  const flip = k > 0 ? prog(t, CUE.reskin[k - 1] - 0.02, CUE.reskin[k - 1] + 0.16) : 1;
  const enter = easeOut(prog(t, 2.05, 2.5));
  const f = uiFont(lang);
  return (
    <div
      style={{
        position: "absolute",
        left: x,
        top: y,
        width: L.colW,
        opacity: enter,
        transform: `translateY(${(1 - enter) * 20 * u}px)`,
        fontFamily: f,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 14 * u }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10 * u,
            padding: `${6 * u}px ${14 * u}px`,
            borderRadius: 999,
            background: "rgba(255,255,255,0.06)",
            border: `${1.5 * u}px solid ${sk.accent}`,
            transform: `perspective(600px) rotateX(${(1 - easeOut(flip)) * 90}deg)`,
          }}
        >
          <div style={{ width: 12 * u, height: 12 * u, borderRadius: 99, background: sk.accent }} />
          <div style={{ fontFamily: FONT.ui, fontWeight: 600, fontSize: 21 * u, color: N.ink }}>{sk.name}</div>
        </div>
        <div style={{ fontWeight: 650, fontSize: 30 * u, color: N.ink, letterSpacing: "-0.01em" }}>{c.name}</div>
        <div style={{ fontWeight: 500, fontSize: 20 * u, color: N.inkMuted }}>{c.members}</div>
      </div>
      {/* her, in the member list: the one thing the skin never touches */}
      <div style={{ display: "flex", alignItems: "center", gap: 12 * u, marginTop: 16 * u }}>
        <div
          style={{
            width: 44 * u,
            height: 44 * u,
            borderRadius: 99,
            background: N.blushWash,
            border: `${2 * u}px solid ${N.blush}`,
            overflow: "hidden",
          }}
        >
          <Img src={staticFile("art/nora-mini-icon.webp")} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
        </div>
        <div style={{ fontFamily: FONT.word, fontWeight: 600, fontSize: 24 * u, color: N.ink }}>Nora</div>
        <div style={{ width: 9 * u, height: 9 * u, borderRadius: 99, background: N.blush }} />
        <div style={{ fontSize: 19 * u, color: N.inkSoft, fontWeight: 500 }}>
          {t < CUE.tag ? (lang === "en" ? "listening" : "聽緊") : lang === "en" ? "tagged" : "有人 tag 佢"}
        </div>
      </div>
      {/* the room's point, readable: every skin changes, her pill above does not */}
      <div
        style={{
          marginTop: 16 * u,
          fontSize: 25 * u,
          fontWeight: 600,
          color: N.inkSoft,
          letterSpacing: lang === "yue" ? "0.02em" : "-0.01em",
          opacity: prog(t, CUE.reskin[0], CUE.reskin[0] + 0.3) * (1 - prog(t, 5.9, 6.1)),
        }}
      >
        {c.changes}
      </div>
    </div>
  );
};

const Bubble: React.FC<{ it: Item; t: number; L: L; lang: Lang; dim: number }> = ({ it, t, L, lang, dim }) => {
  const { u } = useFmt();
  const sk = SKINS[skinAt(t)];
  const p = springAt(t, it.at, 13, 190, 0.7);
  const f = uiFont(lang);
  const size = L.text * u;
  const grow = clamp01(p * 1.2);
  const nodeR = (it.kind === "nora" ? 13 : 7) * u;
  const nodeColor = it.kind === "seed" ? personColor(it.who) : it.kind === "you" ? N.denim : N.blush;
  let body: React.ReactNode;
  if (it.kind === "seed") {
    body = (
      <div
        style={{
          background: sk.bubble,
          borderRadius: sk.radius.map((r) => r * u + "px").join(" "),
          padding: sk.bubbles ? `${12 * u}px ${20 * u}px ${14 * u}px` : `${2 * u}px 0 ${6 * u}px`,
          maxWidth: L.colW - 60 * u,
        }}
      >
        <div style={{ fontFamily: FONT.ui, fontWeight: 650, fontSize: size * 0.62, color: personColor(it.who) }}>
          {it.who}
        </div>
        <div style={{ fontSize: size, color: N.ink, fontWeight: 450, lineHeight: 1.3 }}>{it.text}</div>
      </div>
    );
  } else if (it.kind === "you") {
    body = (
      <div
        style={{
          background: "rgba(154,169,202,0.16)",
          border: `${1.5 * u}px solid rgba(154,169,202,0.45)`,
          borderRadius: 22 * u,
          padding: `${14 * u}px ${20 * u}px`,
          maxWidth: L.colW - 60 * u,
          fontSize: size,
          color: N.ink,
          lineHeight: 1.3,
        }}
      >
        <span
          style={{
            background: N.denim,
            color: N.bg,
            fontWeight: 650,
            borderRadius: 8 * u,
            padding: `${1 * u}px ${8 * u}px`,
            marginRight: 10 * u,
            fontFamily: FONT.ui,
          }}
        >
          {it.mention}
        </span>
        {it.text}
      </div>
    );
  } else {
    // her answer: typed on, then the kicker, then how she knew
    const chars = [...it.text];
    const typed = Math.round(chars.length * easeOut(prog(t, it.at + 0.05, it.at + 0.45)));
    const kick = springAt(t, CUE.kicker, 12, 200, 0.6);
    const src = easeOut(prog(t, CUE.kicker + 0.05, CUE.kicker + 0.35));
    body = (
      <div style={{ maxWidth: L.colW - 40 * u }}>
        <div
          style={{
            background: N.blushWash,
            border: `${2 * u}px solid ${N.blush}`,
            borderRadius: 22 * u,
            padding: `${16 * u}px ${22 * u}px`,
            fontSize: size * 1.06,
            color: N.ink,
            lineHeight: 1.32,
            fontWeight: 500,
          }}
        >
          <div style={{ fontFamily: FONT.word, fontWeight: 600, fontSize: size * 0.66, color: N.blush, marginBottom: 4 * u }}>
            Nora
          </div>
          {chars.slice(0, typed).join("")}
          <span style={{ opacity: 0 }}>{chars.slice(typed).join("")}</span>{" "}
          <span
            style={{
              display: "inline-block",
              fontWeight: 700,
              opacity: kick,
              transform: `scale(${lerp(0.6, 1, kick)})`,
              transformOrigin: "0% 60%",
            }}
          >
            {it.kicker}
          </span>
        </div>
        <Mono size={size * 0.62} color={N.inkSoft} style={{ marginTop: 10 * u, opacity: src, lineHeight: 1.35 }}>
          {noWidow(it.source)}
        </Mono>
      </div>
    );
  }
  return (
    <div
      style={{
        position: "relative",
        maxHeight: 520 * u * grow,
        marginTop: 16 * u * grow,
        opacity: clamp01(p * 1.6) * dim,
        fontFamily: f,
      }}
    >
      <div
        style={{
          position: "absolute",
          left: -nodeR - 1.6 * u,
          top: 26 * u - nodeR,
          width: nodeR * 2,
          height: nodeR * 2,
          borderRadius: 99,
          background: nodeColor,
          boxShadow: it.kind === "nora" ? `0 0 0 ${5 * u}px rgba(239,164,151,0.18)` : undefined,
          transform: `scale(${p})`,
        }}
      />
      <div
        style={{
          marginLeft: 26 * u,
          transformOrigin: "0% 30%",
          transform: `scale(${lerp(0.7, 1, p)}) translateX(${(1 - p) * -14 * u}px)`,
          display: "inline-block",
          // Cantonese wraps at its punctuation, never inside a word like 訂枱
          wordBreak: lang === "yue" ? "keep-all" : "normal",
          overflowWrap: "anywhere",
        }}
      >
        {body}
      </div>
    </div>
  );
};

const Thinking: React.FC<{ t: number; u: number }> = ({ t, u }) => {
  const [a, b, c] = CUE.thinking;
  const on = prog(t, a - 0.15, a) * (1 - prog(t, CUE.reply - 0.05, CUE.reply + 0.05));
  if (on <= 0) return null;
  return (
    <div
      style={{
        display: "inline-flex",
        alignSelf: "flex-start",
        gap: 9 * u,
        marginLeft: 26 * u,
        marginTop: 16 * u,
        padding: `${16 * u}px ${20 * u}px`,
        borderRadius: 22 * u,
        background: N.blushWash,
        opacity: on,
      }}
    >
      {[a, b, c].map((at, i) => {
        const k = springAt(t, at, 9, 260, 0.5);
        return (
          <div
            key={i}
            style={{
              width: 12 * u,
              height: 12 * u,
              borderRadius: 99,
              background: N.blush,
              opacity: 0.35 + 0.65 * k,
              transform: `translateY(${-6 * u * Math.sin(Math.PI * clamp01((t - at) / 0.3))}px)`,
            }}
          />
        );
      })}
    </div>
  );
};

const Room: React.FC<{ t: number; L: L; lang: Lang }> = ({ t, L, lang }) => {
  const { w, h, u, kind } = useFmt();
  const c = COPY[lang];
  const items: Item[] = [
    ...c.room.seed.map((s, i) => ({ kind: "seed" as const, who: s.who, text: s.text, at: CUE.bubbles[i] })),
    { kind: "you", text: c.tag.ask, mention: c.tag.mention, at: CUE.tag },
    { kind: "nora", text: c.tag.reply, kicker: c.tag.replyKicker, source: c.tag.source, at: CUE.reply },
  ];
  const plateIn = smooth(prog(t, 1.85, 2.5));
  const tagPunch = expoOut(prog(t, CUE.tag, CUE.tag + 0.5));
  const cam = 1 + 0.05 * tagPunch + 0.03 * prog(t, 6.5, 8);
  const dimOld = 1 - 0.55 * easeOut(prog(t, CUE.tag, CUE.tag + 0.3));
  const noraSrc = t < CUE.tag ? "art/group-chat.webp" : "art/thinking.webp";
  const noraAsp = aspect(t < CUE.tag ? "group-chat" : "thinking");
  const noraIn = springAt(t, 2.0, 16, 120, 1);
  const swap = t >= CUE.tag ? springAt(t, CUE.tag, 12, 220, 0.6) : 1;
  const glow = 0.55 + 0.25 * Math.sin(t * 5.1) * 0.2;
  const whip = prog(t, 7.9, 8.0);
  return (
    <Abs style={{ opacity: 1 - whip }}>
      <Plate
        src={kind === "tall" ? "art/room-night-tall.webp" : "art/room-night-wide.webp"}
        scale={lerp(1.14, 1.06, prog(t, 2, 8))}
        blur={lerp(26, 12, easeOut(prog(t, 1.9, 3)))}
        brightness={0.42}
        opacity={plateIn}
      />
      <Abs
        style={{
          background: `linear-gradient(${kind === "tall" ? "180deg" : "90deg"}, rgba(20,17,18,0.2) 0%, rgba(20,17,18,0.55) 55%, rgba(20,17,18,0.8) 100%)`,
          opacity: plateIn,
        }}
      />
      <Abs
        style={{
          // the punch-in pivots on the frame's bottom edge, so her sneakers stay in frame
          transformOrigin: `${L.spineX + L.colW * 0.3}px ${h * 0.98}px`,
          transform: `scale(${cam})`,
        }}
      >
        {/* Nora, lit by her phone */}
        <div style={{ opacity: noraIn, transform: `translateY(${(1 - noraIn) * 40 * u}px)` }}>
          <div
            style={{
              position: "absolute",
              left: L.nora.cx * u - 260 * u,
              top: L.nora.bottom * u - L.nora.h * u * 0.8,
              width: 520 * u,
              height: 520 * u,
              borderRadius: "50%",
              background: `radial-gradient(circle, rgba(239,200,180,${0.16 * glow}) 0%, rgba(239,200,180,0) 65%)`,
            }}
          />
          <Cutout
            src={noraSrc}
            cx={L.nora.cx * u}
            bottom={L.nora.bottom * u}
            height={L.nora.h * u * (t < CUE.tag || kind === "tall" ? 1 : 1.08)}
            aspect={noraAsp}
            shadow={0.5}
            style={{ transform: `scale(${lerp(0.94, 1, swap)})`, transformOrigin: "50% 100%" }}
          />
        </div>
        <Header t={t} L={L} lang={lang} x={(L.spineX + 26) * u} y={L.headerY * u} />
        {/* the thread: bottom-anchored, so each message pushes the older ones up */}
        <div
          style={{
            position: "absolute",
            left: (L.spineX - 40) * u,
            paddingLeft: 40 * u,
            width: (L.colW + 40) * u,
            top: (L.headerY + 175 * (L.text / 30)) * u,
            bottom: h - L.colBottom * u,
            display: "flex",
            flexDirection: "column",
            justifyContent: "flex-end",
            overflow: "hidden",
            // older messages fade out under the header as new ones push them up
            WebkitMaskImage: "linear-gradient(to bottom, transparent 0%, #000 16%)",
          }}
        >
          {items.map((it, i) =>
            t >= it.at - 0.02 ? (
              <Bubble key={i} it={it} t={t} L={L} lang={lang} dim={it.kind === "seed" ? dimOld : 1} />
            ) : null,
          )}
          {t < CUE.reply + 0.05 && <Thinking t={t} u={u} />}
        </div>
      </Abs>
    </Abs>
  );
};

const TagEcho: React.FC<{ t: number; head: L["head"] }> = ({ t, head }) => {
  const { u } = useFmt();
  const a = CUE.tag;
  const inn = springAt(t, a, 11, 210, 0.7);
  const out = easeOut(prog(t, CUE.reply - 0.15, CUE.reply + 0.1));
  if (t < a || out >= 1) return null;
  const size = head.size * 1.7 * u;
  return (
    <div
      style={{
        position: "absolute",
        left: head.x * u,
        top: head.y * u - size * 0.12,
        fontFamily: FONT.ui,
        fontWeight: 650,
        fontSize: size,
        letterSpacing: "-0.05em",
        lineHeight: 1,
        color: N.denim,
        opacity: clamp01(inn * 1.4) * (1 - out),
        transform: `translateY(${(1 - inn) * size * 0.4 - out * size * 0.2}px) scale(${lerp(1.25, 1, inn)})`,
        transformOrigin: "0% 50%",
        filter: out > 0 ? `blur(${out * 12}px)` : undefined,
        whiteSpace: "nowrap",
      }}
    >
      @nora
    </div>
  );
};

export const Thread: React.FC<{ lang: Lang }> = ({ lang }) => {
  const t = useT();
  const { w, h, u, kind } = useFmt();
  if (t > 8.05) return null;
  const L = layout(kind, w, h, u);
  const Ls = { ...L, spineX: L.spineX * u, colW: L.colW * u, colBottom: L.colBottom * u };
  const head = L.head;
  const spineTop = -20;
  const whipP = prog(t, 7.9, 8.05);
  return (
    <Abs style={{ background: N.bg }}>
      {t < 2.3 && <Open t={t} L={Ls} />}
      {t >= 1.8 && <Room t={t} L={L} lang={lang} />}
      {t >= 2.2 && (
        <Cable
          d={`M ${Ls.spineX} ${spineTop} L ${Ls.spineX} ${h + 40}`}
          width={3.2 * u}
          color={N.cable}
          shadow={0.55}
          length={h * 1.4}
        />
      )}
      <Headline lang={lang} id="vo1" x={head.x * u} y={head.y * u} width={head.w * u} size={head.size * u} color={N.ink} outAt={4.05} />
      <Headline lang={lang} id="vo2" x={head.x * u} y={head.y * u} width={head.w * u} size={head.size * u} color={N.ink} outAt={5.95} />
      <TagEcho t={t} head={head} />
      {whipP > 0 && <Abs style={{ background: "#FFF7F2", opacity: whipP }} />}
    </Abs>
  );
};
