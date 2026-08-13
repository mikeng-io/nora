---
name: geocode-address
description: Resolve a place's own address to a (lat, lng) coordinate, for feeding render_map.
---

# geocode_address

Call `geocode_address(address=...)`. Returns `(lat, lng)`. Use this when
`context.json` gives you a place's NAME and ADDRESS but `render_map` needs
coordinates — geocode the address first, then pass the result to
`render_map`. Raises `GeocodeError` if Google cannot resolve the address;
catch it and either drop that one marker or fall back to prose naming the
place instead of failing the whole artifact.
