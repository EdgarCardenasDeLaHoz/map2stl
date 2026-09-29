# Surveyed elevation, city by city

_Last updated: 2026-09-28_

Where to get a lidar surface over each plate's own window, so a plate can be checked against
ground somebody else measured. This page is only about sourcing; what the comparison found is in
[decisions/survey-lidar.md](../decisions/survey-lidar.md) and
[decisions/registration-validation.md](../decisions/registration-validation.md). The scratch readers are gone; since 2026-09-27 (F-LANDMARK §4) the
scriptable sources are library providers behind one `ndsm_for_bbox` interface in
`city2stl/height/providers/` (`city2stl/height/providers/survey.py` registry; see
[height-providers.md](height-providers.md)): IGN LiDAR HD (Paris), REDIAM
(Granada), CNIG MDSn edificación via IDEE's WCS (all of Spain, 2.5 m), ČÚZK (Prague), USGS 3DEP.

Findings from wiring them (2026-09-27):

- Spain has a documented national WCS after all: `https://wcs-mds.idee.es/mds`, coverages `mds05`
  (surface 5 m), `mdsn_e025` / `mdsn_v025` (normalised, buildings / vegetation, 2.5 m, first PNOA
  coverage 2008-15, int16 whole metres, stored in EPSG:3042). Only the 0.5 m products of the later
  coverages still need the undocumented download form below; they are not scripted.
- REDIAM's MDHN COG is `https://portalrediam.cica.es/repositorio/01_CARACTERIZACION_TERRITORIO/07_BASES_REF_ELEV/07_ALTURA_NORMALIZADA/01_PROYECTOS_REGIONALES/2020-21_AND_PNOA_LiDAR_V226_hn/InfGeografica/InfRaster/ETRS89_h30_Horto/COG/Mos_1m/Mos_MDHN_1m_COG.tif`
  (EPSG:25830, nodata tag 255 but voids hold -9999). Over Granada Cathedral about a quarter of the
  window is void, mostly the cathedral's own roofs, so an nDSM override there falls back to nearest
  fill or is refused below 30 % coverage; CNIG's 2.5 m building model covers it fully.
- ČÚZK DMP 1G includes vegetation (it is a surface model), so DMP − DMR over a footprint is the roof
  plus any overhanging tree.
- The Cartagena below is Cartagena de Indias (Colombia). Cartagena in Spain is covered by CNIG's
  national WCS.

Everything listed as open below needs no account, no key, and no browser. Where a source is marked
click-only it is reachable but not scriptable, and an alternative is named.

## What we build from a source

Three rasters over the plate's `osm_bbox_nsew`, sampled four times across a plate cell:

- **surface** — the top of whatever is standing, buildings and trees included
- **bare earth** — the ground beneath it
- **height above ground** — their difference, which is what a plate's residual layer is trying to be

Three countries publish the third one directly and save the subtraction: France (`MNH`), Andalucía
(`MDHN`), and Spain nationally (`MDSNE`, buildings only).

## United States — one route for all of it

The Entwine octree on open storage (`city2stl/height/providers/lidar_3dep_ept.py`).
- Use `s3-us-west-2.amazonaws.com/usgs-lidar-public`; `rockyweb.usgs.gov`, which The National
  Map names, does not answer.
- The trap that costs most of the coverage: an octree level is a *sample* of the survey, not a
  coarse copy of it, so every level from the root down must be read or most cells stay empty.
- Why: [decisions/survey-lidar.md](../decisions/survey-lidar.md).

| Plate | Survey project |
| --- | --- |
| Boston Miniature | `MA_CentralEastern_1_2021` |
| Denver Miniature | `CO_DRCOG_2_2020` |
| Miami | `FL_TopobathyFLKeysNOAA_Hydroflattened_2019` |
| Philadelphia, Philadelphia Miniature | `USGS_LPC_DE_DelawareValley_HD_2015_LAS_2017` |
| Old San Juan | `USGS_LPC_PR_PuertoRico_2015_LAS_2018` |

The bucket is `https://s3-us-west-2.amazonaws.com/usgs-lidar-public/<PROJECT>/`. The only spatial
index of it is the survey-footprint file at
`https://raw.githubusercontent.com/hobuinc/usgs-lidar/master/boundaries/resources.geojson`.

`rockyweb.usgs.gov`, which The National Map's product API names for every point-cloud download,
does not answer from this machine at all.

## Europe and Colombia

| City | Source | Product | Access |
| --- | --- | --- | --- |
| Paris | IGN LiDAR HD | 0.5 m surface, bare earth, and height above ground | WMS returning a float GeoTIFF over any window — `https://data.geopf.fr/wms-r` |
| Prague | ČÚZK DMP 1G / DMR 5G | irregular lidar, served as raster | ArcGIS image service, `https://ags.cuzk.gov.cz/arcgis2/rest/services/{dmp1g,dmr5g}/ImageServer/exportImage` |
| Salzburg | BEV Austria ALS | 1 m surface and bare earth | cloud-optimized GeoTIFF, 50 km tiles; the city straddles two of them |
| Granada | REDIAM Andalucía | 1 m surface, bare earth, and height above ground | cloud-optimized GeoTIFF, one 284 GB regional mosaic read through a window |
| Barcelona | ICGC | 0.25 m lidar surface and bare earth (2022) | **the WMS renders a picture, not values** — use the point cloud at `https://datacloud.icgc.cat/datacloud/lidar-territorial/laz_unzip/` |
| Bilbao | geoEuskadi 2017 | 2.2 pts/m², classified, 500 m tiles | plain directory index under `https://www.geo.euskadi.eus/lidar/DatosDescarga/` |
| Valencia | CNIG PNOA (national), not the regional service | 5 pts/m² point cloud, or the 0.5 m surface raster | the regional service is click-only and publishes no surface model over Valencia at all |
| Lisbon | DGT 2024 | 0.5 m surface and bare earth, 10 pts/m² | free account required; the search is anonymous but the download is not |
| Cartagena | — | **nothing open with buildings in it** | see below |

Spain nationally, for any of the four Spanish cities, goes through one undocumented pair of
requests at `centrodedescargas.cnig.es`: list the tiles over a polygon with
`archivosTotalesSerieVisor`, then `POST descargaDir` with the tile's id. A plain GET is refused.

### Cartagena has no answer

The one metre lidar over the Centro Histórico is real, is openly licensed, and cannot be had: it
is bare earth only, and its delivery runs through a login. No surface model or point cloud from
that flight was ever published. The best open surface over the walled city is a 12 m model from
2013, which cannot resolve a rampart. Cartagena's wall width therefore stays unmeasured, and any
claim about it rests on the tags in OpenStreetMap.

## Two traps worth naming

- **A map service may answer with a picture of the elevation rather than the elevation.** Barcelona's
  returns three bands of bytes; Paris's returns one band of floats. Check the dtype, not the status
  code.
- **Surface and bare earth are often flown years apart.** Austria's are dated per tile and can differ
  by five years, so differencing them across a redeveloped block invents buildings that were never
  simultaneously there.
