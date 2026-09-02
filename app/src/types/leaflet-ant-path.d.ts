// No @types package exists for leaflet-ant-path (checked npm — 404). Typed
// against the README's documented surface (constructor + factory), not the
// library's full internals.
declare module "leaflet-ant-path" {
  import type { LatLngExpression, Polyline, PolylineOptions } from "leaflet";

  export interface AntPathOptions extends PolylineOptions {
    delay?: number;
    dashArray?: [number, number];
    weight?: number;
    color?: string;
    pulseColor?: string;
    paused?: boolean;
    reverse?: boolean;
    hardwareAcceleration?: boolean;
  }

  export class AntPath extends Polyline {
    constructor(latLngs: LatLngExpression[], options?: AntPathOptions);
  }

  export function antPath(latLngs: LatLngExpression[], options?: AntPathOptions): AntPath;
}
