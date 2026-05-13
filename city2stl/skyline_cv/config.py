"""Configuration types for the skyline CV research workflow."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ViewpointSpec:
    """Known Street View capture point used for skyline registration."""

    name: str
    query: str
    heading: float
    pitch: float = 0.0
    fov: float = 75.0
    image_width: int = 1280
    image_height: int = 720


@dataclass(frozen=True)
class SiteSpec:
    """Baseline site definition for one city or region."""

    name: str
    north: float
    south: float
    east: float
    west: float
    viewpoints: tuple[ViewpointSpec, ...]


@dataclass(frozen=True)
class RunSpec:
    """Resolved paths for one pipeline run."""

    site: SiteSpec
    root_dir: Path
    streetview_headings: tuple[float, ...] = (-30.0, -15.0, 0.0, 15.0, 30.0)
    camera_height_m: float = 1.7
    heading_search_deg: float = 12.0
    heading_step_deg: float = 1.0
    min_building_area_m2: float = 12.0
    overpass_timeout_s: int = 180
    image_timeout_s: int = 40
    user_agent: str = "strm2stl-skyline-cv/1.0"

    @property
    def output_dir(self) -> Path:
        return self.root_dir / "runs" / self.site.name.lower()

    @property
    def osm_dir(self) -> Path:
        return self.output_dir / "01_osm"

    @property
    def streetview_dir(self) -> Path:
        return self.output_dir / "02_streetview"

    @property
    def registration_dir(self) -> Path:
        return self.output_dir / "03_registration"

    @property
    def estimates_dir(self) -> Path:
        return self.output_dir / "04_estimates"

    @property
    def report_dir(self) -> Path:
        return self.output_dir / "05_report"


def _site_from_json(city: str) -> SiteSpec:
    """Load a SiteSpec from ``sites/<city>.json``.  Falls back to hard-coded
    data if the file is absent or cannot be parsed."""
    import json as _json

    path = Path(__file__).parent / "sites" / f"{city.lower()}.json"
    if path.exists():
        try:
            data = _json.loads(path.read_text(encoding="utf-8"))
            vps = tuple(
                ViewpointSpec(
                    name=v["name"],
                    query=v["query"],
                    heading=float(v["heading"]),
                    pitch=float(v.get("pitch", 0.0)),
                    fov=float(v.get("fov", 75.0)),
                    image_width=int(v.get("image_width", 1280)),
                    image_height=int(v.get("image_height", 720)),
                )
                for v in data.get("viewpoints", [])
            )
            return SiteSpec(
                name=data["name"],
                north=float(data["north"]),
                south=float(data["south"]),
                east=float(data["east"]),
                west=float(data["west"]),
                viewpoints=vps,
            )
        except Exception:
            pass
    raise FileNotFoundError(f"No site definition found for {city!r}")


def miami_site() -> SiteSpec:
    """Miami downtown / Brickell skyline region.

    Canonical ground-truth building heights are stored in
    ``sites/miami.json`` under ``known_heights_m`` for pipeline validation.
    """
    return _site_from_json("miami")


def cartagena_site() -> SiteSpec:
    """Baseline Cartagena region and a few known skyline viewpoints."""

    return SiteSpec(
        name="cartagena",
        north=10.48,
        south=10.34,
        east=-75.44,
        west=-75.59,
        viewpoints=(
            ViewpointSpec(
                name="baluarte_santo_domingo",
                query="Baluarte de Santo Domingo, Cartagena, Colombia",
                heading=65.0,
                pitch=0.0,
                fov=75.0,
            ),
            ViewpointSpec(
                name="muelle_de_los_pegasos",
                query="Muelle de los Pegasos, Cartagena, Colombia",
                heading=25.0,
                pitch=0.0,
                fov=75.0,
            ),
            ViewpointSpec(
                name="castillo_san_felipe",
                query="Castillo de San Felipe de Barajas, Cartagena, Colombia",
                heading=300.0,
                pitch=0.0,
                fov=75.0,
            ),
        ),
    )
