# ---------------------------------------------------------------------------
# Polygon extraction for the notebook-era city mesh path.
#
# This file once held six more functions from the research notebooks. All were
# unreachable -- no importer anywhere in the tree, and every one of them raised
# on import-time-removed APIs (``ox.footprints_from_polygon``, ``np.float``,
# ``descartes.PolygonPatch``). They were removed 2026-09-01; the reasoning is
# recorded in docs/issues.md section 0. Their modern replacements:
#
#   building_heights()    -> city2stl.heights._fill_heights
#   building_to_gdf()     -> app.server.core.osm.fetch_osm_data
#   the matplotlib patch helpers -- no equivalent, and none wanted; the
#     3MF pipeline does not draw.
#
# What remains is ``get_polygons``, which is live: city2stl/create.py imports
# it. Note that create.py is itself superseded by the mesh path (see
# city2stl/mesh.py) and survives only for notebooks/.
# ---------------------------------------------------------------------------

import numpy as np
from shapely.geometry import MultiPolygon, Polygon


def get_polygons(gdf):

    polygons = []
    for _n,row in enumerate(gdf.itertuples(index=False)):
        geometry = row.geometry

        z1 = row.z1
        z0 = row.z0
        try:
            if isinstance(geometry, Polygon):
                pts = np.array(geometry.exterior.xy)
                poly = {"points":pts, "z0":z0, "z1":z1}
                polygons.append(poly)
            elif isinstance(geometry, MultiPolygon):
                #if geometry is multipolygon, go through each subpolygon
                for subpolygon in geometry.geoms:
                    pts = np.array(subpolygon.exterior.xy)
                    poly = {"points":pts, "z0":z0, "z1":z1}
                    polygons.append(poly)
        except Exception:
            print("!", end="")
            continue

    return polygons
