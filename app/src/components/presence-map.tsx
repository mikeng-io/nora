"use client";

import { useEffect, useMemo } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { MapContainer, Marker, Polyline, Popup, TileLayer, useMap } from "react-leaflet";
import { hueOf } from "@/lib/person-color";
import type { PresenceEntry, PresenceHistoryPoint, PresencePlace } from "@/lib/presence-schema";

// Hong Kong — this portal's whole friend group is HK-based (see the HKO
// weather tool, the hkgov geocode option). Only used before any fix has
// ever loaded; FitToMarkers takes over the instant real positions arrive.
const DEFAULT_CENTER: [number, number] = [22.4106, 114.1245];

// A colored dot via `L.divIcon` rather than the default `L.Icon` — the
// default marker image's asset path is a known Leaflet+webpack breakage,
// and a plain dot lets each person's pin match their `hueOf()` accent used
// everywhere else in the portal (memory cards, this map) for free. A
// selected marker gets a ring; a `live` one also pulses (globals.css's
// `.marker-pulse`) — raw HTML/inline styles because Leaflet's icon content
// isn't rendered through React, so Tailwind classes don't apply here.
function dotIcon(hue: number, muted: boolean, selected: boolean): L.DivIcon {
  const size = selected ? 20 : muted ? 12 : 16;
  const color = `hsl(${hue} ${muted ? 15 : 75}% ${muted ? 65 : 50}%)`;
  const ring = selected ? `outline:3px solid hsl(${hue} 75% 50% / 0.35);outline-offset:2px;` : "";
  // The animation class lives on the SAME element as --pulse-color (an
  // inline custom property) rather than on the outer wrapper `className`
  // Leaflet controls — custom properties don't flow upward to a parent,
  // only down to children, so splitting them across two elements would
  // leave the animation reading the fallback color for every marker.
  const pulseClass = !muted ? "marker-pulse" : "";
  const pulseColor = `hsl(${hue} 75% 50% / 0.55)`;
  return L.divIcon({
    className: "",
    html:
      `<span class="${pulseClass}" style="display:block;width:${size}px;height:${size}px;` +
      `border-radius:9999px;background:${color};border:2.5px solid white;` +
      `box-shadow:0 1px 4px rgba(0,0,0,0.5);${ring}--pulse-color:${pulseColor}"></span>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  });
}

// Saved places (Life360's named landmarks — "Home") get a visually
// distinct marker from person dots: a neutral, house-shaped pin, never a
// hue — a place isn't a person, and this map's whole color language
// (hueOf) means "this belongs to someone".
const PLACE_ICON = L.divIcon({
  className: "",
  html:
    '<svg width="17" height="22" viewBox="0 0 22 28" fill="none" xmlns="http://www.w3.org/2000/svg" ' +
    'style="filter:drop-shadow(0 1px 3px rgba(0,0,0,0.45))">' +
    '<path d="M11 0C4.9 0 0 4.9 0 11c0 8.25 11 17 11 17s11-8.75 11-17C22 4.9 17.1 0 11 0z" fill="var(--text-primary)"/>' +
    '<path d="M11 6 5.5 10.5V17h3.5v-4h4v4H16.5v-6.5L11 6z" fill="var(--bg-surface)"/>' +
    "</svg>",
  iconSize: [17, 22],
  iconAnchor: [8.5, 22],
  popupAnchor: [0, -20],
});

function FitToMarkers({ points }: { points: [number, number][] }) {
  const map = useMap();
  useEffect(() => {
    if (points.length === 0) return;
    if (points.length === 1) {
      map.setView(points[0], 14);
      return;
    }
    map.fitBounds(L.latLngBounds(points), { padding: [48, 48] });
  }, [map, points]);
  return null;
}

// Imperative pan/zoom triggered from the sidebar's places list — a click
// there needs to move the SAME map instance, and react-leaflet only reads
// `useMap()` from inside <MapContainer>, so this has to be its own child
// rather than something the page component drives directly.
function FocusOn({ point }: { point: [number, number] | null }) {
  const map = useMap();
  useEffect(() => {
    if (point) map.setView(point, 16);
  }, [map, point]);
  return null;
}

export function PresenceMap({
  entries,
  places,
  selectedPersonId,
  onSelect,
  historyPoints,
  focusPlace,
}: {
  entries: PresenceEntry[];
  places: PresencePlace[];
  selectedPersonId: string | null;
  onSelect: (personId: string) => void;
  historyPoints: PresenceHistoryPoint[];
  focusPlace: [number, number] | null;
}) {
  const withFix = useMemo(() => entries.filter((e) => e.current !== null), [entries]);
  const points = useMemo<[number, number][]>(
    () => withFix.map((e) => [e.current!.lat, e.current!.lng]),
    [withFix],
  );
  const polyline = useMemo<[number, number][]>(
    () => historyPoints.map((p) => [p.lat, p.lng]),
    [historyPoints],
  );
  const routeHue = selectedPersonId ? hueOf(selectedPersonId) : 0;

  return (
    <MapContainer center={points[0] ?? DEFAULT_CENTER} zoom={13} className="isolate h-full w-full" scrollWheelZoom>
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      <FitToMarkers points={points} />
      <FocusOn point={focusPlace} />
      {places.map((place) => (
        <Marker key={place.name} position={[place.lat, place.lng]} icon={PLACE_ICON}>
          <Popup>
            <div className="text-[13px] font-medium">{place.name}</div>
          </Popup>
        </Marker>
      ))}
      {polyline.length > 1 && (
        <Polyline positions={polyline} pathOptions={{ color: `hsl(${routeHue} 70% 50%)`, weight: 3 }} />
      )}
      {withFix.map((entry) => {
        const fix = entry.current!;
        return (
          <Marker
            key={entry.personId}
            position={[fix.lat, fix.lng]}
            icon={dotIcon(
              hueOf(entry.personId),
              fix.wireState !== "live",
              entry.personId === selectedPersonId,
            )}
            eventHandlers={{ click: () => onSelect(entry.personId) }}
          >
            <Popup>
              <div className="text-[13px] font-medium">{entry.displayName}</div>
              {fix.address && <div className="text-[12px] text-text-secondary">{fix.address}</div>}
            </Popup>
          </Marker>
        );
      })}
    </MapContainer>
  );
}
