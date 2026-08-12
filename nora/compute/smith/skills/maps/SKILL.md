---
name: render-map
description: Render a static map image for one or more (lat, lng) locations, embedded inline.
---

# render_map

Call `render_map(locations=[(lat, lng), ...], title=...)`. Returns a
self-contained `<img>` tag — the map image is fetched once at job time and
embedded as base64, not loaded live from Google's servers when someone opens
the artifact. Good for "where is X" or "here's the route" — not for a
pannable/zoomable interactive map (that's the allowlisted Google Maps JS API
path, a doctrine-level decision, not this tool's default).
