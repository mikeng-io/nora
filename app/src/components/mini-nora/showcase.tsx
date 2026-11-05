"use client";

import { useEffect, useRef, useState } from "react";
import Image from "next/image";
import { useTranslations } from "next-intl";
import { PageFrame } from "@/components/shell/app-shell";
import { Button } from "@/components/ui/button";
import { art } from "@/components/nora/art";
import type { createStage, Motion } from "./stage";

type Stage = Awaited<ReturnType<typeof createStage>>;
export function MiniNoraShowcase() {
  const t = useTranslations("miniNora");
  const host = useRef<HTMLDivElement>(null), stage = useRef<Stage | null>(null);
  const [active, setActive] = useState(false), [ready, setReady] = useState(false), [failed, setFailed] = useState(false);
  const [motion, setMotion] = useState<Motion>("Stand"), [playing, setPlaying] = useState(false);
  const [head, setHead] = useState(0), [arm, setArm] = useState(0);
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
  }, [active]);
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
        {!active && <Button variant="primary" onClick={() => setActive(true)}>{t("load")}</Button>}
        <fieldset disabled={!ready || failed} className="space-y-5 disabled:opacity-45">
          <legend className="sr-only">{t("controls")}</legend>
          <label className="block text-sm">{t("motion")}<select className="mt-2 block h-11 w-full rounded-control border border-seam bg-surface px-3" value={motion} onChange={e => choose(e.target.value as Motion)}>{(["Stand", "Walk", "Run"] as const).map(m => <option key={m} value={m}>{t(m)}</option>)}</select></label>
          <Button variant="outline" disabled={motion === "Stand"} onClick={() => { setPlaying(!playing); stage.current?.setPlaying(!playing); }}>{playing ? t("pause") : t("play")}</Button>
          <label className="block text-sm">{t("head")}<input className="mt-3 block w-full accent-denim" type="range" min="-0.35" max="0.35" step="0.01" value={head} disabled={motion !== "Stand"} onChange={e => { setHead(+e.target.value); stage.current?.setHead(+e.target.value); }} /></label>
          <label className="block text-sm">{t("arm")}<input className="mt-3 block w-full accent-denim" type="range" min="0" max="1" step="0.01" value={arm} disabled={motion !== "Stand"} onChange={e => { setArm(+e.target.value); stage.current?.setArm(+e.target.value); }} /></label>
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
