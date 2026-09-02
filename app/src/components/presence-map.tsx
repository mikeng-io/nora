"use client";

import { useEffect, useMemo, useRef } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import BoringAvatar from "boring-avatars";
import { MapContainer, Marker, Polyline, Popup, TileLayer, useMap } from "react-leaflet";
import { hueOf } from "@/lib/person-color";
import {
  STALE_DRIVING_SECONDS,
  type PresenceEntry,
  type PresenceHistoryPoint,
  type PresencePlace,
} from "@/lib/presence-schema";

// Hong Kong — this portal's whole friend group is HK-based (see the HKO
// weather tool, the hkgov geocode option). Only used before any fix has
// ever loaded; FitToMarkers takes over the instant real positions arrive.
const DEFAULT_CENTER: [number, number] = [22.4106, 114.1245];

// Same identicon as components/ui/person-avatar.tsx (People page etc) —
// one person, one look, everywhere. Duplicated here (not imported) because
// Leaflet's divIcon takes a raw HTML STRING, not a rendered React tree;
// `renderToStaticMarkup` is how a React SVG component becomes that string.
const AVATAR_PALETTE = ["#3d5a80", "#5b7ca3", "#98c1d9", "#e0fbfc", "#ee6c4d"];

// React's server renderer assigns internal ids (`_R_0_`, ...) from a
// per-call counter, NOT from the `name` prop — every person's avatar SVG
// gets the SAME id. Leaflet inserts each marker's icon into the SAME page,
// where `url(#id)` resolves by first-match in the whole document, so two
// un-scoped avatars on the map at once would cross-reference each other's
// gradient. Suffixing every id (and its references) with personId fixes
// that; verified live (multiple circle members rendering together).
function avatarSvg(personId: string, size: number): string {
  const raw = renderToStaticMarkup(
    <BoringAvatar size={size} name={personId} variant="beam" colors={AVATAR_PALETTE} square={false} />,
  );
  const ids = new Set([...raw.matchAll(/id="([^"]+)"/g)].map((m) => m[1]));
  let scoped = raw;
  for (const id of ids) {
    const unique = `${id}-${personId}`;
    scoped = scoped.split(`"${id}"`).join(`"${unique}"`).split(`#${id})`).join(`#${unique})`);
  }
  return scoped;
}

// Lucide's Car glyph, inlined — Leaflet's divIcon html is a raw string, not
// a place a <Car> component can render. Same icon the sidebar's PersonRow
// already shows next to a driving member's name (map/page.tsx) — one badge
// meaning, two surfaces.
function carBadge(color: string): string {
  return (
    `<span style="position:absolute;right:-2px;bottom:-2px;width:14px;height:14px;border-radius:9999px;` +
    `background:${color};border:1.5px solid white;display:flex;align-items:center;justify-content:center;">` +
    `<svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2.5" ` +
    `stroke-linecap="round" stroke-linejoin="round">` +
    `<path d="M19 17h2c.6 0 1-.4 1-1v-3c0-.9-.7-1.7-1.5-1.9C18.7 10.6 16 10 16 10s-1.3-1.4-2.2-2.3c-.5-.4-1.1-.7-1.8-.7H5` +
    `c-.6 0-1.1.4-1.4.9l-1.4 2.9A3.7 3.7 0 0 0 2 12v4c0 .6.4 1 1 1h2"/>` +
    `<circle cx="7" cy="17" r="2"/><path d="M9 17h6"/><circle cx="17" cy="17" r="2"/>` +
    "</svg></span>"
  );
}

// The person's real identicon, not a plain colour dot — a selected marker
// gets a hue ring, a `live` one also pulses (globals.css's `.marker-pulse`),
// and a driving member gets a small car badge (the same signal the
// sidebar's PersonRow already surfaces via a Car icon next to their name —
// this makes it visible ON the map itself, not just the list beside it).
// The pulse/ring live on the OUTER span, not the avatar-clipping inner one:
// box-shadow radiates from an element's own border-box regardless of what
// overflow:hidden content sits inside it, so splitting "the ring" from
// "the circular clip" doesn't change how either looks.
function dotIcon(
  personId: string, hue: number, muted: boolean, selected: boolean, driving: boolean, ageSeconds: number,
): L.DivIcon {
  const size = selected ? 36 : muted ? 22 : 28;
  const ring = selected ? `outline:3px solid hsl(${hue} 75% 50% / 0.45);outline-offset:2px;` : "";
  const pulseClass = !muted ? "marker-pulse" : "";
  const pulseColor = `hsl(${hue} 75% 50% / 0.55)`;
  const avatar = avatarSvg(personId, size);
  const stale = driving && ageSeconds > STALE_DRIVING_SECONDS;
  const badge = driving ? carBadge(stale ? "var(--text-muted)" : `hsl(${hue} 70% 45%)`) : "";
  return L.divIcon({
    className: "",
    html:
      `<span class="${pulseClass}" style="position:relative;display:block;width:${size}px;height:${size}px;` +
      `border-radius:9999px;--pulse-color:${pulseColor}">` +
      `<span style="display:block;width:100%;height:100%;border-radius:9999px;overflow:hidden;` +
      `border:2px solid white;box-shadow:0 1px 4px rgba(0,0,0,0.5);${ring}${muted ? "opacity:0.55;" : ""}">` +
      `${avatar}</span>${badge}</span>`,
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
  // `points` is a fresh array on every 20s poll tick even when nobody has
  // moved, so an effect keyed on the array re-fits — and undoes any manual
  // pan/zoom — every tick. This string only changes when a real position
  // does (~11 m rounding); the effect re-derives the bounds from IT, which
  // keeps the dependency list honest (no `points` used but undeclared).
  const fitKey = points.map(([lat, lng]) => `${lat.toFixed(4)},${lng.toFixed(4)}`).join("|");
  useEffect(() => {
    if (!fitKey) return;
    const fitted = fitKey.split("|").map((pair) => pair.split(",").map(Number) as [number, number]);
    if (fitted.length === 1) {
      map.setView(fitted[0], 14);
      return;
    }
    map.fitBounds(L.latLngBounds(fitted), { padding: [48, 48] });
  }, [map, fitKey]);
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

// Selecting a person (sidebar row or marker click) zooms to them once, then
// keeps recentering as their `point` updates on each 20s poll tick — a
// moving (driving) member doesn't drift out of frame. Only the FIRST focus
// after a selection changes zoom (`setView`); every update after that just
// pans (`panTo`), so the operator's own manual zoom-out isn't fought on
// every tick. Ref, not state — this must never itself trigger a re-render.
function FollowSelected({ personId, point }: { personId: string | null; point: [number, number] | null }) {
  const map = useMap();
  // Tracks WHICH person the last zoom-in was for, not just whether someone
  // was selected — switching straight from A to B (a marker click right
  // after another, with no deselect in between) must re-zoom on B, not
  // silently keep panning at whatever zoom A left behind.
  const followedId = useRef<string | null>(null);

  useEffect(() => {
    if (!personId || !point) {
      followedId.current = null;
      return;
    }
    if (followedId.current !== personId) {
      map.setView(point, 16);
      followedId.current = personId;
    } else {
      map.panTo(point);
    }
  }, [map, personId, point]);

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
  const selectedPoint = useMemo<[number, number] | null>(() => {
    const entry = withFix.find((e) => e.personId === selectedPersonId);
    return entry ? [entry.current!.lat, entry.current!.lng] : null;
  }, [withFix, selectedPersonId]);

  return (
    <MapContainer center={points[0] ?? DEFAULT_CENTER} zoom={13} className="isolate h-full w-full" scrollWheelZoom>
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      {/* While a person is selected/followed, FollowSelected owns the
          camera — re-fitting to the WHOLE group on every poll tick would
          fight it, zooming back out the moment new positions arrive. */}
      {!selectedPersonId && <FitToMarkers points={points} />}
      <FollowSelected personId={selectedPersonId} point={selectedPoint} />
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
              entry.personId,
              hueOf(entry.personId),
              fix.wireState !== "live",
              entry.personId === selectedPersonId,
              fix.isDriving === true,
              fix.ageSeconds,
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
