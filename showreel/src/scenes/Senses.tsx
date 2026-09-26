// 11–14 s: what she can see, one beat each. Five shots on the beat, one label
// each, and the line turning into whatever the shot needs: a route, rain, a
// waveform, the scan of a photo; the last, her reminder, holds for two beats.
import React from "react";
import { Img, random, staticFile } from "remotion";
import { Cutout, Mono } from "../components";
import {
  Abs,
  COPY,
  CUE,
  FONT,
  Kind,
  Lang,
  NIGHT,
  STUDIO,
  aspect,
  clamp01,
  easeOut,
  expoOut,
  lerp,
  mixHex,
  noWidow,
  personColor,
  pick,
  prog,
  springAt,
  uiFont,
  useFmt,
  useT,
} from "../lib";

const S = STUDIO;
type Stage = { x: number; y: number; w: number; h: number };
type ShotProps = { t: number; a: number; p: number; st: Stage; u: number; lang: Lang; kind: Kind; c: any };

// ── 1. where you are: a map, the route as the line ──────────────────────────
const Place: React.FC<ShotProps> = ({ t, a, st, u, c }) => {
  const draw = easeOut(prog(t, a + 0.02, a + 0.3));
  const streets = Array.from({ length: 16 }, (_, i) => {
    const vertical = i % 2 === 0;
    const k = random(`st${i}`);
    const pos = (0.08 + 0.84 * random(`sp${i}`)) * (vertical ? st.w : st.h);
    const wob = (random(`sw${i}`) - 0.5) * 120 * u;
    return vertical
      ? `M ${st.x + pos} ${st.y} C ${st.x + pos + wob} ${st.y + st.h * 0.35} ${st.x + pos - wob} ${st.y + st.h * 0.65} ${st.x + pos + wob * 0.3} ${st.y + st.h}`
      : `M ${st.x} ${st.y + pos} C ${st.x + st.w * 0.35} ${st.y + pos + wob} ${st.x + st.w * 0.65} ${st.y + pos - wob} ${st.x + st.w} ${st.y + pos + wob * 0.3}` +
          (k > 2 ? "" : "");
  });
  const A = [st.x + st.w * 0.18, st.y + st.h * 0.78];
  const B = [st.x + st.w * 0.8, st.y + st.h * 0.24];
  const route = `M ${A[0]} ${A[1]} C ${A[0] + st.w * 0.25} ${A[1] - st.h * 0.05} ${A[0] + st.w * 0.2} ${B[1] + st.h * 0.3} ${st.x + st.w * 0.55} ${st.y + st.h * 0.42} S ${B[0] - st.w * 0.1} ${B[1]} ${B[0]} ${B[1]}`;
  const L = 2400 * u;
  const pin = springAt(t, a + 0.14, 10, 220, 0.6);
  return (
    <>
      <div
        style={{
          position: "absolute",
          left: st.x,
          top: st.y,
          width: st.w,
          height: st.h,
          borderRadius: 24 * u,
          background: "#E9E3E1",
          overflow: "hidden",
          border: `${1.5 * u}px solid ${S.hairline}`,
        }}
      />
      <svg style={{ position: "absolute", inset: 0, overflow: "visible" }} width="100%" height="100%">
        <defs>
          <clipPath id="mapclip">
            <rect x={st.x} y={st.y} width={st.w} height={st.h} rx={24 * u} />
          </clipPath>
        </defs>
        <g clipPath="url(#mapclip)">
          <path
            d={`M ${st.x - 50} ${st.y + st.h * 0.62} C ${st.x + st.w * 0.3} ${st.y + st.h * 0.5} ${st.x + st.w * 0.5} ${st.y + st.h * 0.95} ${st.x + st.w + 50} ${st.y + st.h * 0.8}`}
            stroke="rgba(72,88,122,0.13)"
            strokeWidth={70 * u}
            fill="none"
          />
          {streets.map((d, i) => (
            <path key={i} d={d} stroke="#FBF8F7" strokeWidth={(i % 3 === 0 ? 16 : 8) * u} fill="none" />
          ))}
          <path d={route} stroke={S.denim} strokeWidth={5 * u} fill="none" strokeLinecap="round" strokeDasharray={L} strokeDashoffset={L * (1 - draw)} />
        </g>
      </svg>
      {/* Mei, in her hue, where she is now */}
      <div
        style={{
          position: "absolute",
          left: A[0] - 13 * u,
          top: A[1] - 13 * u,
          width: 26 * u,
          height: 26 * u,
          borderRadius: 99,
          background: personColor("Mei", false),
          border: `${4 * u}px solid ${S.surface}`,
          boxShadow: `0 0 0 ${(8 + 14 * ((t * 1.6) % 1)) * u}px rgba(0,0,0,${0.08 * (1 - ((t * 1.6) % 1))})`,
        }}
      />
      <div
        style={{
          position: "absolute",
          left: B[0],
          top: B[1],
          transform: `translate(-50%, -115%) scale(${pin})`,
          transformOrigin: "50% 100%",
          padding: `${8 * u}px ${16 * u}px`,
          borderRadius: 999,
          background: S.ink,
          color: S.surface,
          fontFamily: FONT.mono,
          fontSize: 22 * u,
          whiteSpace: "nowrap",
        }}
      >
        {c.detail}
      </div>
      <Cutout
        src="art/walking.webp"
        cx={st.x + st.w * 0.3}
        bottom={st.y + st.h + 30 * u}
        height={st.h * 0.62}
        aspect={aspect("walking")}
        shadow={0.25}
        style={{ transform: `translateX(${(t - a) * 60 * u}px)` }}
      />
    </>
  );
};

// ── 2. what the sky's doing: the line as rain ───────────────────────────────
const Weather: React.FC<ShotProps> = ({ t, a, st, u, c, kind }) => {
  const deg = Math.round(lerp(21, 26, easeOut(prog(t, a, a + 0.35))));
  const drops = Array.from({ length: 46 }, (_, i) => {
    const x0 = st.x + st.w * (0.42 + 0.58 * random(`rx${i}`));
    const sp = 1600 + random(`rs${i}`) * 900;
    const y = st.y + ((random(`ry${i}`) * st.h + (t - a) * sp * u) % st.h);
    return { x: x0 - (y - st.y) * 0.18, y, l: (40 + random(`rl${i}`) * 50) * u };
  });
  const big = pick(kind, { wide: 270, tall: 300, square: 220 }) * u;
  return (
    <>
      <svg style={{ position: "absolute", inset: 0 }} width="100%" height="100%">
        {drops.map((d, i) => (
          <line key={i} x1={d.x} y1={d.y} x2={d.x - d.l * 0.18} y2={d.y + d.l} stroke={S.denimDim} strokeWidth={2 * u} opacity={0.5} strokeLinecap="round" />
        ))}
      </svg>
      <div
        style={{
          position: "absolute",
          left: st.x,
          top: st.y + st.h * 0.12,
          fontFamily: FONT.ui,
          fontWeight: 650,
          fontSize: big,
          letterSpacing: "-0.06em",
          color: S.ink,
          fontFeatureSettings: "'tnum' 1",
          lineHeight: 1,
        }}
      >
        {deg}°
      </div>
      <Mono size={24 * u} color={S.inkSoft} style={{ position: "absolute", left: st.x + 10 * u, top: st.y + st.h * 0.12 + big * 1.05 }}>
        {c.detail}
        <br />
        <span style={{ color: S.inkMuted }}>{c.source}</span>
      </Mono>
      <Cutout
        src="art/umbrella.webp"
        cx={st.x + st.w * (kind === "wide" ? 0.8 : kind === "tall" ? 0.62 : 0.74)}
        bottom={st.y + st.h}
        height={st.h * (kind === "tall" ? 0.62 : kind === "wide" ? 0.8 : 0.8)}
        aspect={aspect("umbrella")}
        shadow={0.22}
        style={{ transform: `translateY(${(1 - springAt(t, a, 14, 180, 0.8)) * 40 * u}px)` }}
      />
    </>
  );
};

// ── 3. what's playing: the line as a waveform ──────────────────────────────
const Voice: React.FC<ShotProps> = ({ t, a, st, u, c, kind }) => {
  const y = st.y + st.h * 0.5;
  const n = 120;
  const amp = st.h * 0.2 * easeOut(prog(t, a, a + 0.15));
  const pts = Array.from({ length: n + 1 }, (_, i) => {
    const x = i / n;
    const env = Math.sin(Math.PI * x);
    const v =
      Math.sin(x * 38 + t * 22) * 0.55 + Math.sin(x * 91 - t * 31) * 0.3 + Math.sin(x * 13 + t * 9) * 0.35;
    return `${st.x + x * st.w},${y + v * amp * env}`;
  });
  return (
    <>
      <svg style={{ position: "absolute", inset: 0 }} width="100%" height="100%">
        <polyline points={pts.join(" ")} fill="none" stroke={S.denim} strokeWidth={4 * u} strokeLinejoin="round" />
      </svg>
      <Mono size={32 * u} color={S.inkSoft} style={{ position: "absolute", left: st.x, top: st.y + st.h * 0.05 }}>
        ♪ {c.detail}
      </Mono>
      <Cutout
        src="art/music.webp"
        cx={st.x + st.w * (kind === "wide" ? 0.8 : 0.72)}
        bottom={st.y + st.h}
        height={st.h * 0.62}
        aspect={aspect("music")}
        shadow={0.22}
        style={{
          transform: `rotate(${Math.sin((t - a) * 9) * 3}deg)`,
          transformOrigin: "50% 100%",
        }}
      />
    </>
  );
};

// ── 4. what you sent: the photo, and her looking at it ─────────────────────
const Picture: React.FC<ShotProps> = ({ t, a, st, u, c }) => {
  const p = springAt(t, a, 12, 170, 0.8);
  const pw = Math.min(st.w * (st.h > st.w ? 1 : 0.86), st.h * 1.25);
  const ph = pw / 1.5;
  const scan = prog(t, a + 0.12, a + 0.5);
  const cx = st.x + st.w / 2;
  const cy = st.y + st.h * 0.5;
  return (
    <>
      <div
        style={{
          position: "absolute",
          left: cx - pw / 2,
          top: cy - ph / 2,
          width: pw,
          height: ph,
          borderRadius: 20 * u,
          overflow: "hidden",
          border: `${10 * u}px solid ${S.surface}`,
          boxShadow: "0 30px 60px rgb(43 34 32 / 0.18)",
          transform: `translateY(${(1 - p) * 120 * u}px) rotate(${lerp(-9, -3, p)}deg)`,
        }}
      >
        <Img src={staticFile("art/hotpot-photo.webp")} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
        {scan > 0 && scan < 1 && (
          <div
            style={{
              position: "absolute",
              left: 0,
              right: 0,
              top: `${scan * 100}%`,
              height: 3 * u,
              background: S.blush,
              boxShadow: `0 0 ${24 * u}px ${6 * u}px rgba(239,164,151,0.35)`,
            }}
          />
        )}
      </div>
      <div
        style={{
          position: "absolute",
          left: cx - pw / 2 + 20 * u,
          top: cy + ph / 2 + 30 * u,
          display: "flex",
          alignItems: "center",
          gap: 12 * u,
          opacity: easeOut(prog(t, a + 0.15, a + 0.35)),
        }}
      >
        <div style={{ width: 14 * u, height: 14 * u, borderRadius: 99, background: personColor("Mei", false) }} />
        <Mono size={24 * u} color={S.inkSoft}>
          {c.detail}
        </Mono>
      </div>
    </>
  );
};

// ── 5. what you forgot: the reminder she sends on her own ──────────────────
const Later: React.FC<ShotProps> = ({ t, a, st, u, c, lang, kind }) => {
  const p = springAt(t, a + 0.06, 13, 190, 0.7);
  const size = pick(kind, { wide: 50, tall: 50, square: 40 }) * u;
  const hi = springAt(t, a + 0.3, 14, 170, 0.8);
  return (
    <>
    {/* she says hi on her own: the one unprompted message of the reel */}
    <Cutout
      src="art/wave.webp"
      cx={st.x + st.w * (kind === "wide" ? 0.78 : 0.72)}
      bottom={st.y + st.h}
      height={st.h * (kind === "wide" ? 0.5 : kind === "tall" ? 0.46 : 0.42)}
      aspect={aspect("wave")}
      shadow={0.2}
      opacity={clamp01(hi * 1.4)}
      style={{ transform: `translateY(${(1 - hi) * 60 * u}px)` }}
    />
    <div style={{ position: "absolute", left: st.x, top: st.y + st.h * 0.08, width: st.w }}>
      <div style={{ display: "flex", alignItems: "center", gap: 16 * u, opacity: easeOut(prog(t, a, a + 0.15)) }}>
        <Mono size={24 * u} color={S.ink} style={{ padding: `${8 * u}px ${16 * u}px`, borderRadius: 999, border: `${1.5 * u}px solid ${S.seam}` }}>
          {c.detail}
        </Mono>
        <div style={{ display: "flex", alignItems: "center", gap: 10 * u, fontFamily: uiFont(lang), fontSize: 24 * u, fontWeight: 600, color: S.inkSoft }}>
          <div style={{ width: 12 * u, height: 12 * u, borderRadius: 99, background: S.blush }} />
          {c.badge}
        </div>
      </div>
      <div
        style={{
          marginTop: 26 * u,
          display: "flex",
          gap: 20 * u,
          alignItems: "flex-start",
          transform: `translateY(${(1 - p) * 50 * u}px) scale(${lerp(0.92, 1, p)})`,
          transformOrigin: "0% 0%",
          opacity: clamp01(p * 1.5),
        }}
      >
        <div
          style={{
            flex: "0 0 auto",
            width: size * 1.9,
            height: size * 1.9,
            borderRadius: 99,
            background: S.blushWash,
            border: `${2.5 * u}px solid ${S.blush}`,
            overflow: "hidden",
          }}
        >
          <Img src={staticFile("art/nora-mini-icon.webp")} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
        </div>
        <div
          style={{
            background: S.blushWash,
            borderRadius: 26 * u,
            padding: `${22 * u}px ${28 * u}px`,
            fontFamily: uiFont(lang),
            fontSize: size,
            fontWeight: 500,
            lineHeight: 1.3,
            color: S.ink,
            maxWidth: st.w * 0.86,
            // Cantonese breaks at its punctuation, never inside a word like 訂枱
            wordBreak: lang === "yue" ? "keep-all" : "normal",
            overflowWrap: "anywhere",
          }}
        >
          {noWidow(c.reply)}
        </div>
      </div>
    </div>
    </>
  );
};

const SHOTS = [Place, Weather, Voice, Picture, Later];

export const Senses: React.FC<{ lang: Lang }> = ({ lang }) => {
  const t = useT();
  const { w, h, u, kind } = useFmt();
  if (t < 10.9 || t > 14.9) return null;
  const c = COPY[lang].senses;
  const s = CUE.senses;
  const i = s.filter((x) => t >= x).length ? s.filter((x) => t >= x).length - 1 : 0;
  const st: Stage = pick(kind, {
    wide: { x: 820, y: 170, w: 1000, h: 820 },
    tall: { x: 80, y: 620, w: 920, h: 1180 },
    square: { x: 60, y: 330, w: 960, h: 690 },
    feed: { x: 60, y: 370, w: 960, h: 900 },
  });
  const stP = { x: st.x * u, y: st.y * u, w: st.w * u, h: st.h * u };
  const head = pick(kind, {
    wide: { x: 110, y: 150, w: 660, size: 104 },
    tall: { x: 80, y: 180, w: 920, size: 112 },
    square: { x: 60, y: 70, w: 960, size: 80 },
    feed: { x: 60, y: 90, w: 960, size: 86 },
  });
  const into = easeOut(prog(t, s[i], s[i] + 0.16));
  const Shot = SHOTS[i];
  // the collapse: everything is pulled back into one line
  const col = prog(t, CUE.collapse, CUE.collapse + 0.3);
  const colE = Math.pow(col, 2.4);
  const bg = mixHex(S.bg, NIGHT.bg, prog(t, CUE.collapse + 0.05, CUE.collapse + 0.35));
  const enter = expoOut(prog(t, 10.93, 11.3));
  return (
    <Abs style={{ background: bg, overflow: "hidden" }}>
      <Abs
        style={{
          transformOrigin: "50% 50%",
          transform: `translateX(${(1 - enter) * w * 0.8}px) scale(${lerp(1, 0.55, colE)}, ${lerp(1, 0.004, colE)})`,
          filter: enter < 1 ? `blur(${(1 - enter) * 22}px)` : undefined,
          opacity: 1 - prog(t, CUE.collapse + 0.22, CUE.collapse + 0.34),
        }}
      >
        <div
          key={i}
          style={{
            position: "absolute",
            inset: 0,
            transform: `translateX(${(1 - into) * 140 * u}px)`,
            filter: into < 1 ? `blur(${(1 - into) * 14}px)` : undefined,
            opacity: lerp(0.2, 1, into),
          }}
        >
          <Shot t={t} a={s[i]} p={into} st={stP} u={u} lang={lang} kind={kind} c={c[i]} />
        </div>
        {/* the kinetic list: one label per beat */}
        <div
          style={{
            position: "absolute",
            left: head.x * u,
            top: head.y * u,
            width: head.w * u,
            fontFamily: uiFont(lang),
            fontWeight: 650,
            fontSize: head.size * u * (lang === "yue" ? 0.92 : 1),
            letterSpacing: lang === "yue" ? "0.02em" : "-0.04em",
            lineHeight: lang === "yue" ? 1.2 : 1.0,
            color: S.ink,
          }}
        >
          {c.map((row, k) => {
            const on = k === i;
            const past = k < i;
            if (!on) return null;
            const e = easeOut(prog(t, s[k] - 0.02, s[k] + 0.14));
            return (
              <div
                key={k}
                style={{
                  opacity: e * (past ? 0 : 1),
                  transform: `translateY(${(1 - e) * 40 * u}px)`,
                  filter: e < 1 ? `blur(${(1 - e) * 8}px)` : undefined,
                }}
              >
                {row.label}
              </div>
            );
          })}
          <div style={{ display: "flex", gap: 10 * u, marginTop: 26 * u }}>
            {c.map((_, k) => (
              <div
                key={k}
                style={{
                  width: (k === i ? 44 : 14) * u,
                  height: 6 * u,
                  borderRadius: 99,
                  background: k === i ? S.ink : S.seam,
                }}
              />
            ))}
          </div>
        </div>
      </Abs>
      {col > 0 && (
        <div
          style={{
            position: "absolute",
            left: lerp(0, w * 0.225, colE),
            right: lerp(0, w * 0.225, colE),
            top: h / 2 - 2 * u,
            height: 4 * u,
            borderRadius: 99,
            background: NIGHT.cable,
            // the line holds until her close-up has come up behind it
            opacity: prog(t, CUE.collapse + 0.12, CUE.collapse + 0.24) * (1 - prog(t, CUE.collapse + 0.5, CUE.collapse + 0.85)),
          }}
        />
      )}
    </Abs>
  );
};
