"""Road widths by OSM ``highway`` tag (used by city2stl.fetch to buffer road lines)."""

# ---------------------------------------------------------------------------
# Canonical highway widths: approximate TOTAL carriageway width in metres.
# This is the authoritative source (city2stl.fetch imports get_road_width_m).
# ---------------------------------------------------------------------------

_HIGHWAY_WIDTHS: dict = {
    'motorway': 12,       'motorway_link': 6,
    'trunk': 10,          'trunk_link': 5,
    'primary': 8,         'primary_link': 4,
    'secondary': 7,       'secondary_link': 3.5,
    'tertiary': 6,        'tertiary_link': 3,
    'residential': 4,     'living_street': 3,
    'service': 2,         'track': 2,
    'footway': 1.5,       'path': 1.5,       'cycleway': 1.5,
    'steps': 1,           'pedestrian': 3,
    'unclassified': 4,
}


def get_road_width_m(highway) -> float:
    """Return the approximate total road width in metres for the given highway tag."""
    if isinstance(highway, list):
        highway = highway[0] if highway else 'unclassified'
    return float(_HIGHWAY_WIDTHS.get(str(highway), 3.0))
