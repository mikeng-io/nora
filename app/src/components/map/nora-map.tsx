"use client";

import "maplibre-gl/dist/maplibre-gl.css";
import {
  Map as MapLibre,
  Marker,
  setWorkerUrl,
  type GeoJSONSource,
  type LngLatBoundsLike,
  type MapMouseEvent,
} from "maplibre-gl";
import type { FeatureCollection } from "geojson";
import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { useLocale } from "next-intl";
import { useTheme } from "@/lib/theme";
import { hueOf, initials, personColorFor } from "@/lib/person";
import { useReducedMotion } from "@/hooks/use-media";
import { HK_BOUNDS, noraMapStyle } from "./map-style";

// One map component for every map in the portal: the live map, a person's
// places and routes, a trip. Declarative in, imperative underneath: the
// props say what is on the map, and this keeps one MapLibre instance in
// step with them. Every moving camera change is capped at 320ms (DESIGN.md
// §6), and becomes a jump under reduced motion.

export interface MapPerson {
  id: string;
  name: string;
  lat: number;
  lng: number;
  live: boolean;
  faded: boolean;
  /** Read aloud for the pin: "Ken, driving, 2 minutes ago". */
  label: string;
}

export interface MapLine {
  id: string;
  personId: string;
  coords: Array<[number, number]>;
  /** A secondary line (a corridor, an unselected trip) is thinner and dim. */
  quiet?: boolean;
}

export interface MapArea {
  id: string;
  personId: string;
  lat: number;
  lng: number;
  radiusM: number;
  label: string | null;
  emphasis?: boolean;
}

export interface MapPlace {
  name: string;
  lat: number;
  lng: number;
}

export interface MapPadding {
  top: number;
  bottom: number;
  left: number;
  right: number;
}

export interface NoraMapHandle {
  zoomIn: () => void;
  zoomOut: () => void;
  /** Frame everyone, or a set of points. */
  fit: (points?: Array<[number, number]>) => void;
  focus: (lng: number, lat: number, zoom?: number) => void;
}

interface Props {
  people?: MapPerson[];
  selectedId?: string | null;
  onSelect?: (id: string | null) => void;
  lines?: MapLine[];
  areas?: MapArea[];
  places?: MapPlace[];
  padding?: MapPadding;
  /** Change this to re-frame the map around `fitTo` (or everyone). */
  frameKey?: string;
  fitTo?: Array<[number, number]>;
  /** Keep the selected person in view as they move. */
  follow?: boolean;
  className?: string;
  ariaLabel: string;
  interactive?: boolean;
}

const DUR = 320;

// Served from public/ (scripts/vendor-maplibre.mjs says why).
setWorkerUrl("/vendor/maplibre/maplibre-gl-worker.mjs");

function circlePolygon(lng: number, lat: number, radiusM: number, steps = 48): Array<[number, number]> {
  const out: Array<[number, number]> = [];
  const dLat = radiusM / 111_320;
  const dLng = radiusM / (111_320 * Math.cos((lat * Math.PI) / 180));
  for (let i = 0; i <= steps; i++) {
    const a = (i / steps) * Math.PI * 2;
    out.push([lng + dLng * Math.cos(a), lat + dLat * Math.sin(a)]);
  }
  return out;
}

function boundsOf(points: Array<[number, number]>): LngLatBoundsLike | null {
  if (points.length === 0) return null;
  let [minX, minY] = points[0];
  let [maxX, maxY] = points[0];
  for (const [x, y] of points) {
    minX = Math.min(minX, x);
    minY = Math.min(minY, y);
    maxX = Math.max(maxX, x);
    maxY = Math.max(maxY, y);
  }
  return [
    [minX, minY],
    [maxX, maxY],
  ];
}

export const NoraMap = forwardRef<NoraMapHandle, Props>(function NoraMap(
  {
    people = [],
    selectedId = null,
    onSelect,
    lines = [],
    areas = [],
    places = [],
    padding = { top: 0, bottom: 0, left: 0, right: 0 },
    frameKey,
    fitTo,
    follow = false,
    className,
    ariaLabel,
    interactive = true,
  },
  ref,
) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibre | null>(null);
  const markers = useRef(new Map<string, { marker: Marker; el: HTMLButtonElement }>());
  const placeMarkers = useRef<Marker[]>([]);
  const [ready, setReady] = useState(false);
  const { resolved: theme } = useTheme();
  const locale = useLocale();
  const reduce = useReducedMotion();
  const userMoved = useRef(false);
  const appliedStyle = useRef("");
  const styleReady = useRef(false);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  const peopleRef = useRef(people);
  peopleRef.current = people;
  const overlays = useRef({ lines, areas, theme });
  overlays.current = { lines, areas, theme };

  const move = (fn: (m: MapLibre, duration: number) => void) => {
    const m = mapRef.current;
    if (m) fn(m, reduce ? 0 : DUR);
  };

  const fitPoints = (points: Array<[number, number]>) =>
    move((m, duration) => {
      const b = boundsOf(points);
      if (!b) return m.fitBounds(HK_BOUNDS, { duration, padding: 24 });
      m.fitBounds(b, { duration, maxZoom: 15, padding: 56 });
    });

  useImperativeHandle(ref, () => ({
    zoomIn: () => move((m, duration) => m.zoomIn({ duration })),
    zoomOut: () => move((m, duration) => m.zoomOut({ duration })),
    fit: (points) => {
      userMoved.current = false;
      fitPoints(points ?? peopleRef.current.map((p) => [p.lng, p.lat]));
    },
    focus: (lng, lat, zoom) =>
      move((m, duration) => m.easeTo({ center: [lng, lat], zoom: zoom ?? Math.max(m.getZoom(), 14), duration })),
  }));

  // ------------------------------------------------------------ create
  useEffect(() => {
    if (!container.current) return;
    const m = new MapLibre({
      container: container.current,
      style: noraMapStyle(theme, locale),
      bounds: HK_BOUNDS,
      fitBoundsOptions: { padding: 24 },
      attributionControl: { compact: true },
      interactive,
      dragRotate: false,
      pitchWithRotate: false,
      touchPitch: false,
      maxZoom: 18,
      minZoom: 3,
      localIdeographFontFamily: "'PingFang HK', 'Noto Sans HK Variable', 'Microsoft JhengHei', sans-serif",
      cooperativeGestures: false,
    });
    appliedStyle.current = `${theme}:${locale}`;
    m.touchZoomRotate.disableRotation();
    m.getCanvas().setAttribute("aria-label", ariaLabel);
    m.on("dragstart", () => (userMoved.current = true));
    m.on("click", (e: MapMouseEvent) => {
      // A click on the map itself (not a pin) clears the selection.
      if ((e.originalEvent.target as HTMLElement).closest(".nora-pin")) return;
      onSelectRef.current?.(null);
    });
    m.on("style.load", () => {
      styleReady.current = true;
      syncOverlays(m, overlays.current);
      setReady(true);
    });
    mapRef.current = m;
    return () => {
      markers.current.forEach(({ marker }) => marker.remove());
      markers.current.clear();
      m.remove();
      mapRef.current = null;
    };
    // The map is created once; theme and locale are applied below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---------------------------------------------------- theme & language
  useEffect(() => {
    const m = mapRef.current;
    const key = `${theme}:${locale}`;
    if (!m || appliedStyle.current === key) return;
    appliedStyle.current = key;
    styleReady.current = false;
    m.setStyle(noraMapStyle(theme, locale), { diff: false });
  }, [theme, locale]);

  // ------------------------------------------------------------ padding
  useEffect(() => {
    mapRef.current?.setPadding(padding);
  }, [padding.top, padding.bottom, padding.left, padding.right]); // eslint-disable-line react-hooks/exhaustive-deps

  // ------------------------------------------------------------- people
  useEffect(() => {
    const m = mapRef.current;
    if (!m) return;
    const seen = new Set<string>();
    for (const p of people) {
      seen.add(p.id);
      let entry = markers.current.get(p.id);
      if (!entry) {
        const el = document.createElement("button");
        el.type = "button";
        el.className = "nora-pin";
        for (const part of ["pulse", "disc", "tag"]) {
          const span = document.createElement("span");
          span.className = part;
          if (part === "pulse") span.dataset.liveMark = "";
          el.appendChild(span);
        }
        el.addEventListener("click", (e) => {
          e.stopPropagation();
          onSelectRef.current?.(el.dataset.id ?? null);
        });
        const marker = new Marker({ element: el, anchor: "center" }).setLngLat([p.lng, p.lat]).addTo(m);
        entry = { marker, el };
        markers.current.set(p.id, entry);
      }
      const { marker, el } = entry;
      marker.setLngLat([p.lng, p.lat]);
      el.dataset.id = p.id;
      el.dataset.live = String(p.live);
      el.dataset.faded = String(p.faded);
      el.dataset.selected = String(p.id === selectedId);
      el.setAttribute("aria-label", p.label);
      el.setAttribute("aria-pressed", String(p.id === selectedId));
      el.style.setProperty("--person", `oklch(var(--person-l) var(--person-c) ${hueOf(p.id)})`);
      (el.querySelector(".disc") as HTMLElement).textContent = initials(p.name);
      (el.querySelector(".tag") as HTMLElement).textContent = p.name;
    }
    for (const [id, { marker }] of markers.current) {
      if (!seen.has(id)) {
        marker.remove();
        markers.current.delete(id);
      }
    }
  }, [people, selectedId]);

  // ------------------------------------------------------- saved places
  useEffect(() => {
    const m = mapRef.current;
    if (!m) return;
    placeMarkers.current.forEach((pm) => pm.remove());
    placeMarkers.current = places.map((p) => {
      const el = document.createElement("div");
      el.className = "nora-place";
      el.textContent = p.name;
      return new Marker({ element: el, anchor: "top", offset: [0, -5] })
        .setLngLat([p.lng, p.lat])
        .addTo(m);
    });
  }, [places]);

  // ----------------------------------------------------- lines & areas
  useEffect(() => {
    const m = mapRef.current;
    // Not isStyleLoaded(): that stays false while tiles are still arriving,
    // and a route that waits for every tile arrives late or never.
    if (m && ready && styleReady.current) syncOverlays(m, { lines, areas, theme });
  }, [lines, areas, theme, ready]);

  // ------------------------------------------------------------ framing
  const lastFrame = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (!ready || frameKey === undefined || frameKey === lastFrame.current) return;
    lastFrame.current = frameKey;
    userMoved.current = false;
    fitPoints(fitTo && fitTo.length ? fitTo : people.map((p) => [p.lng, p.lat]));
  }, [ready, frameKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // ------------------------------------------------ follow the selected
  const selected = people.find((p) => p.id === selectedId);
  useEffect(() => {
    const m = mapRef.current;
    if (!m || !ready || !follow || !selected || userMoved.current) return;
    const px = m.project([selected.lng, selected.lat]);
    const { clientWidth: w, clientHeight: h } = m.getContainer();
    const inside =
      px.x > padding.left + 40 && px.x < w - padding.right - 40 && px.y > padding.top + 40 && px.y < h - padding.bottom - 40;
    if (!inside) move((mm, duration) => mm.easeTo({ center: [selected.lng, selected.lat], duration }));
  }, [selected?.lat, selected?.lng, follow, ready]); // eslint-disable-line react-hooks/exhaustive-deps

  // MapLibre's stylesheet sets `position: relative` on the element it is
  // given, and unlayered CSS beats Tailwind's layered utilities, so the
  // positioning lives on a wrapper and the map fills it.
  return (
    <div className={className}>
      <div ref={container} className="h-full w-full" />
    </div>
  );
});

// -------------------------------------------------------------- overlays

function syncOverlays(
  m: MapLibre,
  { lines, areas, theme }: { lines: MapLine[]; areas: MapArea[]; theme: "light" | "dark" },
) {
  const lineData: FeatureCollection = {
    type: "FeatureCollection",
    features: lines
      .filter((l) => l.coords.length > 1)
      .map((l) => ({
        type: "Feature",
        properties: { color: personColorFor(l.personId, theme), quiet: Boolean(l.quiet) },
        geometry: { type: "LineString", coordinates: l.coords },
      })),
  };
  const ends: FeatureCollection = {
    type: "FeatureCollection",
    features: lines
      .filter((l) => l.coords.length > 1 && !l.quiet)
      .map((l) => ({
        type: "Feature",
        properties: { color: personColorFor(l.personId, theme) },
        geometry: { type: "Point", coordinates: l.coords[0] },
      })),
  };
  const areaData: FeatureCollection = {
    type: "FeatureCollection",
    features: areas.map((a) => ({
      type: "Feature",
      properties: {
        color: personColorFor(a.personId, theme),
        emphasis: Boolean(a.emphasis),
        label: a.label ?? "",
      },
      geometry: { type: "Polygon", coordinates: [circlePolygon(a.lng, a.lat, a.radiusM)] },
    })),
  };
  const areaLabels: FeatureCollection = {
    type: "FeatureCollection",
    features: areas
      .filter((a) => a.label)
      .map((a) => ({
        type: "Feature",
        properties: { label: a.label },
        geometry: { type: "Point", coordinates: [a.lng, a.lat] },
      })),
  };

  const set = (id: string, data: FeatureCollection) => {
    const src = m.getSource(id) as GeoJSONSource | undefined;
    if (src) src.setData(data);
    else m.addSource(id, { type: "geojson", data });
  };
  set("nora-lines", lineData);
  set("nora-line-ends", ends);
  set("nora-areas", areaData);
  set("nora-area-labels", areaLabels);

  const halo = theme === "dark" ? "#141112" : "#f4f2f2";
  const ink = theme === "dark" ? "#b9afaf" : "#5e5351";
  if (!m.getLayer("nora-areas-fill")) {
    m.addLayer({
      id: "nora-areas-fill",
      type: "fill",
      source: "nora-areas",
      paint: {
        "fill-color": ["get", "color"],
        "fill-opacity": ["case", ["get", "emphasis"], 0.22, 0.12],
      },
    });
    m.addLayer({
      id: "nora-areas-line",
      type: "line",
      source: "nora-areas",
      paint: { "line-color": ["get", "color"], "line-width": ["case", ["get", "emphasis"], 2, 1] },
    });
    // The Cable Rule: a route is one 2px line in the person's hue, never
    // doubled, glowed or gradiented.
    m.addLayer({
      id: "nora-lines",
      type: "line",
      source: "nora-lines",
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": ["get", "color"],
        "line-width": ["case", ["get", "quiet"], 1.5, 2],
        "line-opacity": ["case", ["get", "quiet"], 0.45, 1],
      },
    });
    m.addLayer({
      id: "nora-line-ends",
      type: "circle",
      source: "nora-line-ends",
      paint: {
        "circle-radius": 4,
        "circle-color": halo,
        "circle-stroke-color": ["get", "color"],
        "circle-stroke-width": 2,
      },
    });
    m.addLayer({
      id: "nora-area-labels",
      type: "symbol",
      source: "nora-area-labels",
      layout: { "text-field": ["get", "label"], "text-font": ["Noto Sans Bold"], "text-size": 11.5 },
      paint: { "text-color": ink, "text-halo-color": halo, "text-halo-width": 1.5 },
    });
  }
}
