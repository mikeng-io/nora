import type { StyleSpecification, ExpressionSpecification } from "maplibre-gl";

// Nora's basemap, drawn from OpenFreeMap's vector tiles (OpenMapTiles
// schema; free, no key) in her own palette: a warm-grey map in the studio
// lighting and a dark warm-grey one late at night, never the provider's
// default blue-black (DESIGN.md §7, Map). Streets are quiet so that the
// people on it, in their own hues, are the loudest thing on the page.
// Values continue the design tokens rather than repeat them: the canvas is
// studio, the streets are surface, the casings are hairline and seam.

interface Palette {
  land: string;
  landuse: string;
  park: string;
  water: string;
  waterLabel: string;
  building: string;
  buildingLine: string;
  road: string;
  roadMajor: string;
  roadCasing: string;
  motorway: string;
  rail: string;
  boundary: string;
  label: string;
  labelSoft: string;
  halo: string;
}

const STUDIO: Palette = {
  land: "#f1eeee",
  landuse: "#ebe7e6",
  park: "#e4e6de",
  water: "#d3d9dc",
  waterLabel: "#7b8a90",
  building: "#e7e2e1",
  buildingLine: "#dcd5d5",
  road: "#fcfbfb",
  roadMajor: "#fcfbfb",
  roadCasing: "#dcd5d5",
  motorway: "#f7f1ee",
  rail: "#c9c0bf",
  boundary: "#bdb3b3",
  label: "#5e5351",
  labelSoft: "#8a7f7e",
  halo: "#f4f2f2",
};

const LATE_NIGHT: Palette = {
  land: "#181415",
  landuse: "#1c1819",
  park: "#1b1d1a",
  water: "#11171a",
  waterLabel: "#5d6f76",
  building: "#221d1e",
  buildingLine: "#2b2526",
  road: "#2a2425",
  roadMajor: "#322b2c",
  roadCasing: "#1e191a",
  motorway: "#3a3233",
  rail: "#3d3536",
  boundary: "#4a4243",
  label: "#b9afaf",
  labelSoft: "#8a7f7f",
  halo: "#141112",
};

const FONT = ["Noto Sans Regular"];
const FONT_BOLD = ["Noto Sans Bold"];

function name(locale: string): ExpressionSpecification {
  return locale.startsWith("zh")
    ? ["coalesce", ["get", "name:zh-Hant"], ["get", "name:zh"], ["get", "name"]]
    : ["coalesce", ["get", "name:en"], ["get", "name_en"], ["get", "name:latin"], ["get", "name"]];
}

const lineGeom: ExpressionSpecification = ["match", ["geometry-type"], ["LineString", "MultiLineString"], true, false];
const polyGeom: ExpressionSpecification = ["match", ["geometry-type"], ["Polygon", "MultiPolygon"], true, false];

export function noraMapStyle(theme: "light" | "dark", locale: string): StyleSpecification {
  const c = theme === "dark" ? LATE_NIGHT : STUDIO;
  const label = name(locale);
  const w = (stops: Array<[number, number]>): ExpressionSpecification =>
    ["interpolate", ["exponential", 1.4], ["zoom"], ...stops.flat()] as ExpressionSpecification;

  return {
    version: 8,
    name: `nora-${theme}`,
    glyphs: "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf",
    sources: {
      openmaptiles: {
        type: "vector",
        url: "https://tiles.openfreemap.org/planet",
        attribution:
          '<a href="https://openfreemap.org" target="_blank" rel="noreferrer">OpenFreeMap</a> © <a href="https://www.openmaptiles.org/" target="_blank" rel="noreferrer">OpenMapTiles</a> © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a>',
      },
    },
    layers: [
      { id: "background", type: "background", paint: { "background-color": c.land } },
      {
        id: "landuse",
        type: "fill",
        source: "openmaptiles",
        "source-layer": "landuse",
        filter: ["all", polyGeom, ["match", ["get", "class"], ["residential", "commercial", "industrial", "retail"], true, false]],
        paint: { "fill-color": c.landuse, "fill-opacity": 0.7 },
      },
      {
        id: "landcover",
        type: "fill",
        source: "openmaptiles",
        "source-layer": "landcover",
        filter: ["all", polyGeom, ["match", ["get", "class"], ["wood", "grass", "farmland", "wetland"], true, false]],
        paint: { "fill-color": c.park, "fill-opacity": 0.8 },
      },
      {
        id: "park",
        type: "fill",
        source: "openmaptiles",
        "source-layer": "park",
        filter: polyGeom,
        paint: { "fill-color": c.park },
      },
      {
        id: "water",
        type: "fill",
        source: "openmaptiles",
        "source-layer": "water",
        filter: ["all", polyGeom, ["!=", ["get", "brunnel"], "tunnel"]],
        paint: { "fill-color": c.water },
      },
      {
        id: "waterway",
        type: "line",
        source: "openmaptiles",
        "source-layer": "waterway",
        filter: lineGeom,
        paint: { "line-color": c.water, "line-width": w([[10, 0.5], [16, 3]]) },
      },
      {
        id: "building",
        type: "fill",
        source: "openmaptiles",
        "source-layer": "building",
        minzoom: 13,
        paint: {
          "fill-color": c.building,
          "fill-outline-color": c.buildingLine,
          "fill-opacity": ["interpolate", ["linear"], ["zoom"], 13, 0, 14, 1],
        },
      },
      {
        id: "aeroway",
        type: "fill",
        source: "openmaptiles",
        "source-layer": "aeroway",
        filter: polyGeom,
        paint: { "fill-color": c.landuse },
      },
      {
        id: "road-minor",
        type: "line",
        source: "openmaptiles",
        "source-layer": "transportation",
        minzoom: 12,
        filter: ["all", lineGeom, ["match", ["get", "class"], ["minor", "service", "track"], true, false]],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": c.road, "line-width": w([[12, 0.6], [14, 1.6], [18, 10]]) },
      },
      {
        id: "path",
        type: "line",
        source: "openmaptiles",
        "source-layer": "transportation",
        minzoom: 15,
        filter: ["all", lineGeom, ["==", ["get", "class"], "path"]],
        paint: { "line-color": c.roadCasing, "line-width": 1, "line-dasharray": [2, 2] },
      },
      {
        id: "road-major-casing",
        type: "line",
        source: "openmaptiles",
        "source-layer": "transportation",
        minzoom: 10,
        filter: ["all", lineGeom, ["match", ["get", "class"], ["primary", "secondary", "tertiary", "trunk"], true, false]],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": c.roadCasing, "line-width": w([[10, 1.2], [14, 4], [18, 20]]) },
      },
      {
        id: "road-major",
        type: "line",
        source: "openmaptiles",
        "source-layer": "transportation",
        minzoom: 10,
        filter: ["all", lineGeom, ["match", ["get", "class"], ["primary", "secondary", "tertiary", "trunk"], true, false]],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": c.roadMajor, "line-width": w([[10, 0.6], [14, 2.8], [18, 17]]) },
      },
      {
        id: "motorway-casing",
        type: "line",
        source: "openmaptiles",
        "source-layer": "transportation",
        minzoom: 6,
        filter: ["all", lineGeom, ["==", ["get", "class"], "motorway"]],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": c.roadCasing, "line-width": w([[6, 1], [12, 3], [18, 24]]) },
      },
      {
        id: "motorway",
        type: "line",
        source: "openmaptiles",
        "source-layer": "transportation",
        minzoom: 6,
        filter: ["all", lineGeom, ["==", ["get", "class"], "motorway"]],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": c.motorway, "line-width": w([[6, 0.6], [12, 2], [18, 20]]) },
      },
      {
        id: "rail",
        type: "line",
        source: "openmaptiles",
        "source-layer": "transportation",
        minzoom: 11,
        filter: ["all", lineGeom, ["match", ["get", "class"], ["rail", "transit"], true, false], ["!=", ["get", "brunnel"], "tunnel"]],
        paint: { "line-color": c.rail, "line-width": w([[11, 0.6], [16, 1.6]]) },
      },
      {
        id: "boundary",
        type: "line",
        source: "openmaptiles",
        "source-layer": "boundary",
        filter: ["all", ["<=", ["get", "admin_level"], 4], ["!=", ["get", "maritime"], 1]],
        paint: { "line-color": c.boundary, "line-width": 1, "line-dasharray": [3, 2] },
      },
      {
        id: "water-name",
        type: "symbol",
        source: "openmaptiles",
        "source-layer": "water_name",
        filter: ["match", ["geometry-type"], ["Point", "MultiPoint"], true, false],
        layout: { "text-field": label, "text-font": FONT, "text-size": 11, "text-max-width": 8 },
        paint: { "text-color": c.waterLabel, "text-halo-color": c.halo, "text-halo-width": 1 },
      },
      {
        id: "road-name",
        type: "symbol",
        source: "openmaptiles",
        "source-layer": "transportation_name",
        minzoom: 14,
        filter: ["match", ["get", "class"], ["primary", "secondary", "tertiary", "trunk", "minor"], true, false],
        layout: {
          "symbol-placement": "line",
          "text-field": label,
          "text-font": FONT,
          "text-size": ["interpolate", ["linear"], ["zoom"], 14, 10, 17, 12],
          "text-rotation-alignment": "map",
        },
        paint: { "text-color": c.labelSoft, "text-halo-color": c.halo, "text-halo-width": 1.2 },
      },
      {
        id: "place-minor",
        type: "symbol",
        source: "openmaptiles",
        "source-layer": "place",
        minzoom: 12,
        filter: ["match", ["get", "class"], ["suburb", "neighbourhood", "quarter", "hamlet", "village"], true, false],
        layout: {
          "text-field": label,
          "text-font": FONT,
          "text-size": ["interpolate", ["linear"], ["zoom"], 12, 10.5, 16, 13],
          "text-max-width": 7,
        },
        paint: { "text-color": c.labelSoft, "text-halo-color": c.halo, "text-halo-width": 1.4 },
      },
      {
        id: "place-town",
        type: "symbol",
        source: "openmaptiles",
        "source-layer": "place",
        minzoom: 8,
        filter: ["match", ["get", "class"], ["town"], true, false],
        layout: {
          "text-field": label,
          "text-font": FONT,
          "text-size": ["interpolate", ["linear"], ["zoom"], 8, 11, 14, 14],
          "text-max-width": 8,
        },
        paint: { "text-color": c.label, "text-halo-color": c.halo, "text-halo-width": 1.4 },
      },
      {
        id: "place-city",
        type: "symbol",
        source: "openmaptiles",
        "source-layer": "place",
        maxzoom: 13,
        filter: ["match", ["get", "class"], ["city"], true, false],
        layout: {
          "text-field": label,
          "text-font": FONT_BOLD,
          "text-size": ["interpolate", ["linear"], ["zoom"], 4, 11, 10, 15],
          "text-max-width": 8,
        },
        paint: { "text-color": c.label, "text-halo-color": c.halo, "text-halo-width": 1.6 },
      },
    ],
  };
}

/** Hong Kong, the circle's home, framed whole: the view before anyone has a
 * position to fit to. */
export const HK_BOUNDS: [[number, number], [number, number]] = [
  [113.82, 22.15],
  [114.44, 22.56],
];
