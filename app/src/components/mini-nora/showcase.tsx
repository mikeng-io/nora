"use client";

import { useEffect, useRef, useState } from "react";
import Image from "next/image";
import { useTranslations } from "next-intl";
import { PageFrame } from "@/components/shell/app-shell";
import { Button } from "@/components/ui/button";
import { art } from "@/components/nora/art";
import type { createStage, Motion, Expression, Hand, Finger } from "./stage";

type Stage = Awaited<ReturnType<typeof createStage>>;
export function MiniNoraShowcase() {
  const t = useTranslations("miniNora");
  const host = useRef<HTMLDivElement>(null), stage = useRef<Stage | null>(null);
  const [active, setActive] = useState(false), [ready, setReady] = useState(false), [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [motion, setMotion] = useState<Motion>("Stand"), [playing, setPlaying] = useState(false);
  const [head, setHead] = useState(0), [arm, setArm] = useState(0);
  const [expression, setExpression] = useState<Expression>("Neutral");
  const fingerNames = ["Thumb", "Index", "Middle", "Ring", "Pinky"] as const;
  const [hands, setHands] = useState({ Left: [0, 0, 0, 0, 0], Right: [0, 0, 0, 0, 0] });
  function curl(side: Hand, finger: Finger, value: number) {
    setHands(current => ({ ...current, [side]: current[side].map((v, i) => fingerNames[i] === finger ? value : v) }));
    stage.current?.setFinger(side, finger, value);
  }
  function handPose(side: Hand, pose: "Open" | "Fist" | "Point") {
    const values = fingerNames.map(finger => pose === "Open" || (pose === "Point" && finger === "Index") ? 0 : 1);
    setHands(current => ({ ...current, [side]: values }));
    fingerNames.forEach((finger, index) => stage.current?.setFinger(side, finger, values[index]));
  }
  useEffect(() => {
    if (!active || !host.current) return;
    let cancelled = false;
    let instance: Stage | undefined;
    import("./stage").then(({ createStage }) => cancelled ? undefined : createStage(host.current!, () => setFailed(true))).then(async (value) => {
      if (!value) return;
      instance = value;
      if (cancelled) { value.dispose(); return; }
      stage.current = value;
      await value.ready;
      if (!cancelled) setReady(true);
    }).catch(() => { instance?.dispose(); if (!cancelled) { stage.current = null; setFailed(true); } });
    return () => { cancelled = true; instance?.dispose(); stage.current = null; };
  }, [active, attempt]);
  function load() {
    setReady(false); setFailed(false); setActive(true); setAttempt(value => value + 1);
    setMotion("Stand"); setPlaying(false); setHead(0); setArm(0); setExpression("Neutral");
    setHands({ Left: [0, 0, 0, 0, 0], Right: [0, 0, 0, 0, 0] });
  }
  function choose(value: Motion) {
    setMotion(value); stage.current?.setMotion(value);
    setPlaying(false); stage.current?.setPlaying(false);
  }
  return <PageFrame width="wide">
    <h1 className="font-display text-3xl text-ink">{t("title")}</h1>
    <p className="mt-2 max-w-2xl text-ink-soft">{t("intro")}</p>
    <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_280px]">
      <div className="relative h-[min(65vh,640px)] min-h-80 overflow-hidden rounded-panel border border-hairline bg-hoodie">
        {(!ready || failed) && <Image src={art.canonical} alt={t("portrait")} fill sizes="(min-width:1024px) 800px, 100vw" className="object-contain" />}
        <div ref={host} role="img" aria-label={t("stage")} className={`absolute inset-0 ${!ready || failed ? "invisible" : ""}`} />
      </div>
      <div className="space-y-5">
        <p role="status" className="text-sm text-ink-soft">{failed ? t("failed") : !active ? t("download") : !ready ? t("loading") : t("help")}</p>
        {(!active || failed) && <Button variant="primary" onClick={load}>{t(failed ? "retry" : "load")}</Button>}
        <fieldset key={attempt} disabled={!ready || failed} className="space-y-5 disabled:opacity-45">
          <legend className="sr-only">{t("controls")}</legend>
          <label className="block text-sm">{t("motion")}<select className="mt-2 block h-11 w-full rounded-control border border-seam bg-surface px-3" value={motion} onChange={e => choose(e.target.value as Motion)}>{(["Stand", "Walk", "Run"] as const).map(m => <option key={m} value={m}>{t(m)}</option>)}</select></label>
          <Button variant="outline" disabled={motion === "Stand"} onClick={() => { setPlaying(!playing); stage.current?.setPlaying(!playing); }}>{playing ? t("pause") : t("play")}</Button>
          <label className="block text-sm">{t("head")}<input className="mt-3 block w-full accent-denim" type="range" min="-0.35" max="0.35" step="0.01" value={head} disabled={motion !== "Stand"} onChange={e => { setHead(+e.target.value); stage.current?.setHead(+e.target.value); }} /></label>
          <label className="block text-sm">{t("arm")}<input className="mt-3 block w-full accent-denim" type="range" min="0" max="1" step="0.01" value={arm} disabled={motion !== "Stand"} onChange={e => { setArm(+e.target.value); stage.current?.setArm(+e.target.value); }} /></label>
          <label className="block text-sm">{t("expression")}<select aria-label={t("expression")} className="mt-2 block h-11 w-full rounded-control border border-seam bg-surface px-3" value={expression} onChange={e => { const value = e.target.value as Expression; setExpression(value); stage.current?.setExpression(value); }}>{(["Neutral", "Smile", "Happy", "Puzzled"] as const).map(value => <option key={value} value={value}>{t(value)}</option>)}</select></label>
          {(["Left", "Right"] as const).map(side => <details key={side} className="border-t border-hairline pt-3">
            <summary className="min-h-11 cursor-pointer text-sm text-ink">{t(`${side}Hand`)}</summary>
            <fieldset className="space-y-3">
              <legend className="sr-only">{t(`${side}Hand`)}</legend>
              <div className="flex flex-wrap gap-2">{(["Open", "Fist", "Point"] as const).map(pose => <Button key={pose} variant="outline" onClick={() => handPose(side, pose)}>{t(pose)}</Button>)}</div>
              {fingerNames.map((finger, index) => <label key={finger} className="block text-sm">{t(finger)}<input className="mt-3 block w-full accent-denim" type="range" min="0" max="1" step="0.01" value={hands[side][index]} onChange={e => curl(side, finger, +e.target.value)} /></label>)}
            </fieldset>
          </details>)}
          <label className="block text-sm">{t("light")}<input className="mt-3 block w-full accent-denim" type="range" min="0.5" max="5" step="0.1" defaultValue="3" onChange={e => stage.current?.setLight(+e.target.value)} /></label>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={() => stage.current?.orbit(-0.3)}>{t("left")}</Button>
            <Button variant="outline" onClick={() => stage.current?.orbit(0.3)}>{t("right")}</Button>
            <Button variant="outline" onClick={() => stage.current?.zoom(0.85)}>{t("closer")}</Button>
            <Button variant="outline" onClick={() => stage.current?.zoom(1.15)}>{t("farther")}</Button>
          </div>
          <Button variant="outline" onClick={() => stage.current?.reset()}>{t("reset")}</Button>
        </fieldset>
        <p className="border-t border-hairline pt-4 text-sm text-ink-muted">{t("limits")}</p>
      </div>
    </div>
  </PageFrame>;
}
