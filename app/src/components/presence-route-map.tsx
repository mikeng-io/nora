"use client";

import { useEffect, useMemo } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
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

  useEffect(() => {
    if (points.length === 0) return;
    if (points.length === 1) {
      map.setView(points[0], 14);
      return;
    }
    map.fitBounds(L.latLngBounds(points), { padding: [40, 40] });
  }, [map, points]);

  return null;
}

type RouteLine = {
  id: number;
  corridorId: number | null;
  points: PresenceHistoryPoint[];
};

export function PresenceRouteMap({ routes, selectedTripId }: { routes: RouteLine[]; selectedTripId: number | null }) {
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
      {routes.map((route) => {
        const routePositions = route.points.map((point) => [point.lat, point.lng] as [number, number]);
        if (routePositions.length < 2) return null;
        const selected = route.id === selectedTripId;
        return (
          <Polyline
            key={route.id}
            positions={routePositions}
            pathOptions={{
              color: route.corridorId === null ? "var(--accent-dim)" : "var(--accent)",
              weight: selected ? 5 : 2,
              opacity: selected ? 0.95 : 0.38,
            }}
          />
        );
      })}
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
