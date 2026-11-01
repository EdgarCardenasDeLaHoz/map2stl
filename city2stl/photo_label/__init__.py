"""Photo labelling service: the logic behind `tools/photo_label/server.py` and the app's photo
routes (`app/server/core/photo_solve.py`).

- `regions`: per-region `Config` and the `Regions` registry (`tools/photo_label/regions.json`).
- `photos`: photo listing, bad-photo flags, label files.
- `solve`: camera solve of a labelled photo (`camera_solver.solve_pose` / `solve_terrain`) and
  the buildings a camera sees (`photo_view.project_buildings`).
- `candidates`: start poses for an unsolved photo (pano seeds, title/GPS hints, `auto_pose`).
- `auto_pose`: skyline-vs-DEM pose search.

Moved out of the tool in F-UNIFY Phase 3; the tool server keeps thin `api_*` wrappers.
"""
