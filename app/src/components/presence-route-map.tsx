"use client";

import { useEffect, useMemo, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
// `leaflet.heat` is loaded dynamically inside DwellHeatLayer's effect, not
// imported here — it's a plain script that assumes a browser global and
// crashes Next's server-side prerender the moment it's in ANY module graph
// the server touches, even behind this file's own ssr:false dynamic import.
// `@types/leaflet.heat` (a type-only devDependency) still augments `L`'s
// types globally, so `L.heatLayer` below type-checks without the runtime
// import present at module scope.
import { antPath, type AntPath } from "leaflet-ant-path";
import { MapContainer, Marker, Polyline, TileLayer, useMap } from "react-leaflet";
import type { PresenceHistoryPoint } from "@/lib/presence-schema";

const DEFAULT_CENTER: [number, number] = [22.4106, 114.1245];

const START_ICON = L.divIcon({
  className: "",
  html: '<span style="display:block;width:12px;height:12px;border-radius:9999px;background:var(--success);border:2px solid white;box-shadow:0 1px 4px rgba(0,0,0,.45)"></span>',
  iconSize: [12, 12],
  iconAnchor: [6, 6],
});

const END_ICON = L.divIcon({
  className: "",
  html: '<span style="display:block;width:14px;height:14px;border-radius:9999px;background:var(--accent);border:2px solid white;box-shadow:0 1px 4px rgba(0,0,0,.45)"></span>',
  iconSize: [14, 14],
  iconAnchor: [7, 7],
});

function FitToPath({ points }: { points: [number, number][] }) {
  const map = useMap();

  // The caller rebuilds the `routes` prop inline on every render, so
  // `points` is a fresh array even when nothing moved — an effect keyed on
  // it re-fits (undoing manual pan/zoom) on every re-render, e.g. toggling
  // the heat layer. Same stable-key fix as presence-map's FitToMarkers:
  // the string changes only when a real coordinate does, and the effect
  // re-derives the bounds from it rather than from `points`.
  const fitKey = points.map(([lat, lng]) => `${lat.toFixed(4)},${lng.toFixed(4)}`).join("|");
  useEffect(() => {
    if (!fitKey) return;
    const fitted = fitKey.split("|").map((pair) => pair.split(",").map(Number) as [number, number]);
    if (fitted.length === 1) {
      map.setView(fitted[0], 14);
      return;
    }
    map.fitBounds(L.latLngBounds(fitted), { padding: [40, 40] });
  }, [map, fitKey]);

  return null;
}

// react-leaflet has no declarative wrapper for a third-party layer — same
// imperative add/remove-on-the-map pattern as FitToPath above, not a new
// convention. The selected trip's path animates (marching-ants direction of
// travel); every other trip stays a plain dim Polyline (rendered by the
// caller) — animating a dozen at once would be noise, not signal.
function AnimatedSelectedPath({ positions, color }: { positions: [number, number][]; color: string }) {
  const map = useMap();
  const layerRef = useRef<AntPath | null>(null);

  useEffect(() => {
    if (positions.length < 2) return undefined;
    const layer = antPath(positions, {
      color, weight: 5, opacity: 0.95, delay: 800, dashArray: [10, 20],
      pulseColor: "#ffffff",
      // The route keeps its dashed direction-of-travel encoding, just
      // static — the same trade globals.css's reduced-motion guard makes
      // for .marker-pulse (strip the motion, keep the styling).
      paused: window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    });
    layer.addTo(map);
    layerRef.current = layer;
    return () => {
      layer.remove();
      layerRef.current = null;
    };
  }, [map, positions, color]);

  return null;
}

// Density of every fetched fix (not just the selected trip) — the texture
// of where a subject actually spends time, including spots that never
// crossed Tier 2's clustering confidence threshold to become a labelled
// place. Uses points already fetched for the trip paths — no extra request.
function DwellHeatLayer({ points, enabled }: { points: [number, number][]; enabled: boolean }) {
  const map = useMap();

  useEffect(() => {
    if (!enabled || points.length === 0) return undefined;
    let layer: L.HeatLayer | undefined;
    let cancelled = false;
    import("leaflet.heat").then(() => {
      if (cancelled) return;
      layer = L.heatLayer(
        points.map(([lat, lng]) => L.latLng(lat, lng)),
        { radius: 18, blur: 22, maxZoom: 16, minOpacity: 0.25 },
      );
      layer.addTo(map);
    });
    return () => {
      cancelled = true;
      layer?.remove();
    };
  }, [map, points, enabled]);

  return null;
}

type RouteLine = {
  id: number;
  corridorId: number | null;
  points: PresenceHistoryPoint[];
};

export function PresenceRouteMap({
  routes, selectedTripId, showHeat = false,
}: {
  routes: RouteLine[];
  selectedTripId: number | null;
  showHeat?: boolean;
}) {
  const positions = useMemo<[number, number][]>(
    () => routes.flatMap((route) => route.points.map((point) => [point.lat, point.lng] as [number, number])),
    [routes],
  );
  const selectedRoute = routes.find((route) => route.id === selectedTripId);
  const selectedPositions = useMemo<[number, number][]>(
    () => selectedRoute?.points.map((point) => [point.lat, point.lng] as [number, number]) ?? [],
    [selectedRoute],
  );
  // A selected trip with no recorded fix path (older trips can fall outside
  // the shared history fetch's window) has nothing to frame — fall back to
  // the full set rather than silently keeping the previous trip's framing.
  const fitTargets = selectedPositions.length > 0 ? selectedPositions : positions;

  return (
    <MapContainer
      center={positions[0] ?? DEFAULT_CENTER}
      zoom={13}
      className="isolate h-full w-full"
      scrollWheelZoom
    >
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      <FitToPath points={fitTargets} />
      <DwellHeatLayer points={positions} enabled={showHeat} />
      {routes.map((route) => {
        const routePositions = route.points.map((point) => [point.lat, point.lng] as [number, number]);
        if (routePositions.length < 2) return null;
        if (route.id === selectedTripId) return null; // rendered animated, below
        return (
          <Polyline
            key={route.id}
            positions={routePositions}
            pathOptions={{
              color: route.corridorId === null ? "var(--accent-dim)" : "var(--accent)",
              weight: 2,
              opacity: 0.38,
            }}
          />
        );
      })}
      {selectedPositions.length > 0 && (
        <AnimatedSelectedPath
          positions={selectedPositions}
          color={selectedRoute?.corridorId === null ? "var(--accent-dim)" : "var(--accent)"}
        />
      )}
      {selectedRoute && selectedRoute.points.length > 0 && (
        <Marker position={[selectedRoute.points[0].lat, selectedRoute.points[0].lng]} icon={START_ICON} />
      )}
      {selectedRoute && selectedRoute.points.length > 1 && (
        <Marker
          position={[
            selectedRoute.points[selectedRoute.points.length - 1].lat,
            selectedRoute.points[selectedRoute.points.length - 1].lng,
          ]}
          icon={END_ICON}
        />
      )}
    </MapContainer>
  );
}
