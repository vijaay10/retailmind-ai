"""Geography for the map surfaces.

**The warehouse holds no coordinates.** It holds city names and a region label,
which is the right thing for it to hold — a retailer's analytics should not
depend on a geocoding service being up. So the console keeps a small reference
table here and is explicit about its two limits:

* A city the table does not know is **listed, never dropped**. A map that
  silently omits three stores is a map that under-reports a region, and the
  reader has no way to tell.
* Region colouring paints every state in a census region with the *region's*
  value. That is not state-level measurement, and the map says so. Painting a
  regional figure across states and letting it read as per-state data is the
  most common lie a retail choropleth tells.

The region→state mapping is the US Census Bureau's, which is also how the
warehouse's own region labels are defined — so the two agree by construction
rather than by coincidence.
"""

#: US Census regions, as the warehouse defines them, expanded to the states
#: they contain. Used for choropleth fills only.
REGION_STATES: dict[str, tuple[str, ...]] = {
    "Northeast": ("CT", "ME", "MA", "NH", "NJ", "NY", "PA", "RI", "VT"),
    "Midwest": ("IL", "IN", "IA", "KS", "MI", "MN", "MO", "NE", "ND", "OH", "SD", "WI"),
    "Southeast": ("AL", "AR", "FL", "GA", "KY", "LA", "MS", "NC", "SC", "TN", "VA", "WV"),
    "Southwest": ("AZ", "NM", "OK", "TX", "NV"),
    "West": ("AK", "CA", "CO", "HI", "ID", "MT", "OR", "UT", "WA", "WY"),
}

#: Coordinates for the estate's cities. Deliberately a fixed table rather than
#: a lookup service: a dashboard that cannot draw because a third-party
#: geocoder is rate-limiting is a dashboard nobody trusts. Anything missing is
#: reported by :func:`locate`.
CITY_COORDINATES: dict[str, tuple[float, float]] = {
    "Albuquerque": (35.0844, -106.6504),
    "Atlanta": (33.7490, -84.3880),
    "Austin": (30.2672, -97.7431),
    "Birmingham": (33.5186, -86.8104),
    "Boise": (43.6150, -116.2023),
    "Boston": (42.3601, -71.0589),
    "Buffalo": (42.8864, -78.8784),
    "Charlotte": (35.2271, -80.8431),
    "Chicago": (41.8781, -87.6298),
    "Columbus": (39.9612, -82.9988),
    "Denver": (39.7392, -104.9903),
    "Detroit": (42.3314, -83.0458),
    "El Paso": (31.7619, -106.4850),
    "Hartford": (41.7658, -72.6734),
    "Indianapolis": (39.7684, -86.1581),
    "Kansas City": (39.0997, -94.5786),
    "Las Vegas": (36.1699, -115.1398),
    "Mesa": (33.4152, -111.8315),
    "Miami": (25.7617, -80.1918),
    "Milwaukee": (43.0389, -87.9065),
    "Minneapolis": (44.9778, -93.2650),
    "Nashville": (36.1627, -86.7816),
    "New York": (40.7128, -74.0060),
    "Newark": (40.7357, -74.1724),
    "Orlando": (28.5383, -81.3792),
    "Philadelphia": (39.9526, -75.1652),
    "Phoenix": (33.4484, -112.0740),
    "Pittsburgh": (40.4406, -79.9959),
    "Portland": (45.5152, -122.6784),
    "Providence": (41.8240, -71.4128),
    "Raleigh": (35.7796, -78.6382),
    "Reno": (39.5296, -119.8138),
    "Sacramento": (38.5816, -121.4944),
    "Salt Lake City": (40.7608, -111.8910),
    "San Antonio": (29.4241, -98.4936),
    "San Francisco": (37.7749, -122.4194),
    "Seattle": (47.6062, -122.3321),
    "St Louis": (38.6270, -90.1994),
    "Tampa": (27.9506, -82.4572),
    "Tucson": (32.2226, -110.9747),
}


def locate(cities: list[str]) -> tuple[dict[str, tuple[float, float]], list[str]]:
    """Resolve city names to coordinates, returning what did not resolve.

    The second element is the point of this function. Callers are expected to
    render it — an unplottable store is a gap in the picture, and the reader
    needs to know its size before drawing a conclusion from the map.
    """
    found: dict[str, tuple[float, float]] = {}
    missing: list[str] = []
    for city in cities:
        key = str(city).strip()
        if key in CITY_COORDINATES:
            found[key] = CITY_COORDINATES[key]
        elif key:
            missing.append(key)
    return found, missing


def states_for(region: str) -> tuple[str, ...]:
    return REGION_STATES.get(str(region), ())


def known_regions() -> tuple[str, ...]:
    return tuple(REGION_STATES)
