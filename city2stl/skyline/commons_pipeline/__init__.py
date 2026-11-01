"""The Commons skyline photo pipeline (F-WEB2) as a library; scripts 13-17 are CLI wrappers.

Stages, in order (each a typed function; the cache files are the hand-off between them):

- ``profiles``: search Commons, fetch, screen, outline  -> ``profiles.json`` / ``profiles.npz`` (script 13)
- ``groups``: pairwise outline similarity, same-spot groups, review page   -> ``pairs.json`` /
  ``groups.json`` (scripts 14, 15)
- ``embed``: DINOv2 embeddings for same-spot search   -> ``embed.npz`` (script 16)
- ``pipeline``: route, place, measure, keep, combine and report (script 17), built from
  ``measure`` (one photo's pose and towers), ``placement`` (routes and gates), ``readings``
  (cross-check and per-building rows) and ``finish`` (truth, report page).

``cache`` holds what they share: the region cache folder, the retrying fetch, the thumbnail url.
The finder itself (``find_skyline_photos``, ``screen``, ``skyline_quality``...) stays in
``city2stl.skyline.commons_photos``.
"""
