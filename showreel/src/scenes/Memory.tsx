// 8–11 s: lights on. The one line splits into five, each moving at its own
// speed: the words in seconds, the room in hours, what's open in days, what
// the room has become in weeks, who people are in months.
import React from "react";
import { Cutout, Headline, Mono } from "../components";
import {
  Abs,
  COPY,
  CUE,
  FONT,
  Lang,
  STUDIO,
  aspect,
  easeOut,
  expoOut,
  lerp,
  personColor,
  pick,
  prog,
  springAt,
  uiFont,
  useFmt,
  useT,
} from "../lib";

const S = STUDIO;
const SPEED = [1100, 300, 70, 26, 10]; // px/s: seconds … months

export const Memory: React.FC<{ lang: Lang }> = ({ lang }) => {
  const t = useT();
  const { w, h, u, kind } = useFmt();
  if (t < 7.95 || t > 11.25) return null;
  const c = COPY[lang].memory;
  const zh = lang === "yue";
  const L = pick(kind, {
    wide: { lanes: [370, 495, 620, 745, 870], labelX: 110, labelW: 330, chipX: 470, labelAbove: false, clipR: 450,
      nora: { cx: 1660, bottom: 1075, h: 560 }, head: { x: 110, y: 110, w: 1300, size: 92 } },
    tall: { lanes: [600, 800, 1000, 1200, 1400], labelX: 80, labelW: 900, chipX: 80, labelAbove: true, clipR: 0,
      nora: { cx: 780, bottom: 1890, h: 440 }, head: { x: 80, y: 170, w: 920, size: 96 } },
    square: { lanes: [345, 485, 625, 765, 905], labelX: 60, labelW: 960, chipX: 60, labelAbove: true, clipR: 255,
      nora: { cx: 935, bottom: 1075, h: 300 }, head: { x: 60, y: 70, w: 960, size: 66 } },
    feed: { lanes: [420, 590, 760, 930, 1100], labelX: 60, labelW: 960, chipX: 60, labelAbove: true, clipR: 295,
      nora: { cx: 915, bottom: 1335, h: 360 }, head: { x: 60, y: 80, w: 960, size: 70 } },
  });
  const flash = 1 - expoOut(prog(t, 8.0, 8.35));
  // leaving: push into the open note
  const out = prog(t, 10.7, 11.0);
  const whip = Math.pow(out, 2.4);
  const center = h / 2;
  return (
    <Abs style={{ background: S.bg, overflow: "hidden" }}>
      <Abs
        style={{
          background: `radial-gradient(ellipse ${w}px ${h * 0.8}px at 30% 0%, ${S.surface} 0%, rgba(252,251,251,0) 70%)`,
        }}
      />
      <Abs
        style={{
          transform: `translateX(${-w * 0.9 * whip}px)`,
          filter: out > 0 ? `blur(${whip * 24}px)` : undefined,
          opacity: 1 - prog(t, 10.95, 11.05),
        }}
      >
        {L.lanes.map((ly, i) => {
          const at = CUE.lanes[i];
          const sp = springAt(t, at, 15, 150, 0.9);
          const y = lerp(center, ly * u, sp);
          const reach = expoOut(prog(t, at - 0.05, at + 0.45));
          const tier = c.tiers[i];
          const dt = Math.max(0, t - at);
          const shift = dt * SPEED[i] * u;
          const blur = i === 0 ? 2.2 : 0;
          const chipSize = (kind === "wide" ? 24 : kind === "tall" ? 28 : 20) * u;
          const lanePad = L.labelAbove ? 0 : L.chipX * u;
          const reps = i === 0 ? 5 : i === 1 ? 3 : 2;
          const chips = Array.from({ length: reps }, () => tier.chips).flat();
          const start = i >= 2 ? w * (i === 2 ? 0.18 : 0.1) : w * 0.12;
          return (
            <React.Fragment key={i}>
              {/* the lane: one line, ink when the lights are on */}
              <div
                style={{
                  position: "absolute",
                  left: 0,
                  width: w,
                  top: y - 1.25 * u,
                  height: 2.5 * u,
                  background: S.ink,
                  opacity: 0.85,
                  transformOrigin: "50% 50%",
                  transform: `scaleX(${reach})`,
                }}
              />
              {/* chips riding the lane at its own speed, clipped to the lane's
                  own span: never under a label, never behind her */}
              <div
                style={{
                  position: "absolute",
                  left: Math.max(0, lanePad - 24 * u),
                  right: L.clipR * u,
                  top: y - chipSize * 1.5,
                  height: chipSize * 3,
                  overflow: "hidden",
                  WebkitMaskImage: `linear-gradient(to right, transparent 0, #000 ${60 * u}px, #000 calc(100% - ${60 * u}px), transparent 100%)`,
                }}
              >
              <div
                style={{
                  position: "absolute",
                  left: lanePad + start - shift - Math.max(0, lanePad - 24 * u),
                  top: chipSize * 0.45,
                  display: "flex",
                  gap: 22 * u,
                  opacity: easeOut(prog(t, at + 0.05, at + 0.3)),
                  filter: blur ? `blur(${blur}px)` : undefined,
                  whiteSpace: "nowrap",
                }}
              >
                {chips.map((chip, k) => {
                  const own = i === 2; // an open note is hers: blush marks her
                  const person = i === 4;
                  return (
                    <div
                      key={k}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 10 * u,
                        height: chipSize * 2.1,
                        padding: `0 ${chipSize * 0.8}px`,
                        borderRadius: 999,
                        background: own ? S.blushWash : S.surface,
                        border: `${1.5 * u}px solid ${own ? S.blush : S.hairline}`,
                        fontFamily: uiFont(lang),
                        fontSize: chipSize,
                        fontWeight: 500,
                        color: S.ink,
                        boxShadow: "0 8px 24px rgb(43 34 32 / 0.08)",
                      }}
                    >
                      {(own || person) && (
                        <div
                          style={{
                            width: chipSize * 0.42,
                            height: chipSize * 0.42,
                            borderRadius: 99,
                            background: own ? S.blush : personColor("Ken", false),
                          }}
                        />
                      )}
                      {chip}
                      {own && k === 0 && <Stamp t={t} lang={lang} size={chipSize} />}
                    </div>
                  );
                })}
              </div>
              </div>
              {/* the label: how fast, and what */}
              <div
                style={{
                  position: "absolute",
                  left: L.labelX * u,
                  top: L.labelAbove ? y - (kind === "tall" ? 124 : 102) * u : y - 64 * u,
                  width: L.labelW * u,
                  opacity: easeOut(prog(t, at + 0.1, at + 0.35)),
                  transform: `translateY(${(1 - easeOut(prog(t, at + 0.1, at + 0.4))) * 16 * u}px)`,
                  paddingRight: 16 * u,
                  zIndex: 2,
                }}
              >
                <Mono size={(kind === "wide" ? 21 : 22) * u} color={S.inkMuted}>
                  {tier.scale}
                </Mono>
                <div
                  style={{
                    fontFamily: uiFont(lang),
                    fontWeight: 600,
                    fontSize: (kind === "wide" ? 30 : kind === "tall" ? 36 : 25) * u,
                    color: S.ink,
                    lineHeight: 1.15,
                    marginTop: 2 * u,
                  }}
                >
                  {tier.title}
                </div>
              </div>
            </React.Fragment>
          );
        })}
        <Cutout
          src="art/notebook.webp"
          cx={L.nora.cx * u}
          bottom={L.nora.bottom * u}
          height={L.nora.h * u}
          aspect={aspect("notebook")}
          shadow={0.22}
          opacity={easeOut(prog(t, 8.5, 8.9))}
          style={{ transform: `translateY(${(1 - springAt(t, 8.5, 14, 120, 1)) * 60 * u}px)` }}
        />
      </Abs>
      <Headline
        lang={lang}
        id="vo3"
        x={L.head.x * u}
        y={L.head.y * u}
        width={L.head.w * u}
        size={L.head.size * u * (zh ? 0.95 : 1)}
        color={S.ink}
        outAt={10.75}
      />
      {/* lights on */}
      {flash > 0 && <Abs style={{ background: "#FFFBF8", opacity: flash }} />}
    </Abs>
  );
};

const Stamp: React.FC<{ t: number; lang: Lang; size: number }> = ({ t, lang, size }) => {
  const p = springAt(t, CUE.noteStamp, 10, 260, 0.6);
  if (t < CUE.noteStamp) return null;
  return (
    <div
      style={{
        marginLeft: size * 0.4,
        padding: `${size * 0.12}px ${size * 0.5}px`,
        border: `${size * 0.09}px solid ${STUDIO.denim}`,
        borderRadius: size * 0.35,
        color: STUDIO.denim,
        fontFamily: lang === "yue" ? FONT.hk : FONT.ui,
        fontWeight: 650,
        fontSize: size * 1.05,
        transform: `rotate(${lerp(-14, -5, p)}deg) scale(${lerp(1.9, 1, p)})`,
        opacity: Math.min(1, p * 1.4),
      }}
    >
      {lang === "en" ? "still open" : "未搞掂"}
    </div>
  );
};
