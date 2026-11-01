"""One configuration object for a skyline run (F-UNIFY Phase 2).

``SkylineConfig`` holds every switch the pipeline reads. It is built in one order:

1. the dataclass defaults;
2. the site file ``sites/<region>.json`` (the ``use_*`` switches, ``render_pdf``, ...);
3. the environment: each ``SKYLINE_*`` variable the code used to read on its own still
   overrides, parsed exactly as before (:data:`ENV`);
4. request overrides (``SkylineConfig.build(..., overrides={...})``, e.g. from the app).

A run activates its config with :func:`use` (a ``ContextVar``, so two jobs in one server do
not see each other's config); the pipeline reads it with :func:`current`. Outside a run,
``current()`` is the defaults plus the environment, read at call time, so a test that sets an
env var and calls a function directly behaves as it did when that function read the env var.

Secrets are not part of it: the Google key and URL signing secret (``streetview_io``) and the
Flickr key (``region_config``) stay in the environment, so a config can be logged and written
to a report sidecar without leaking them.

Worker threads do not inherit the active config: submit pool work through
``geo2stl.google_usage.bind`` (it carries the whole context).
"""

from __future__ import annotations

import contextvars
import dataclasses
import math
import os
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ON = ("1", "true", "yes", "on")
_OFF = ("0", "false", "no", "off")

#: Default MobileSAM checkpoint (``MOBILESAM_CHECKPOINT_PATH`` overrides).
DEFAULT_MOBILESAM_CHECKPOINT = str(Path.home() / ".cache" / "mobile_sam" / "vit_t.pth")
_SEGFORMER_SIZES = {"b0", "b1", "b2", "b3", "b4", "b5"}


@dataclass(frozen=True)
class SkylineConfig:
    """Every switch of a skyline run. Field docs name the env var that overrides it."""

    region: str | None = None

    # ---- run options (request level)
    #: Skip paid Google requests (``google_usage.cache_only``); the app's "allow paid" off.
    cache_only: bool = False

    # ---- site switches (sites/<region>.json; some have an env override)
    use_satellite_footprints: bool = False
    use_cross_view_scoring: bool = False
    use_pano_coastline_recovery: bool = False
    drive_pano_recovery_anchor: bool = False
    use_commons_photos: bool = False
    use_satellite_heights: bool = False
    #: site ``use_cadastre_band``; env ``SKYLINE_CADASTRE_BAND``.
    cadastre_band: bool = False
    #: site ``use_survey_heights``; env ``SKYLINE_SURVEY_HEIGHTS``.
    survey_heights: bool = False
    #: site ``use_ownership``; env ``SKYLINE_OWNERSHIP``.
    ownership: bool = False
    #: site ``use_cadastre_occluders``; env ``SKYLINE_CADASTRE_OCCLUDERS``.
    cadastre_occluders: bool = False
    render_pdf: bool = False
    pano_only_pdf: bool = False
    max_plausible_height_m: float = 300.0

    # ---- feature flags (env only)
    f_sky1: bool = True                 # SKYLINE_CV_F_SKY1
    f_sky5: bool = True                 # SKYLINE_CV_F_SKY5
    f_sky11_1: bool = False             # SKYLINE_CV_F_SKY11_1
    f_sky12: bool = False               # SKYLINE_CV_F_SKY12
    f_sky13: bool = True                # SKYLINE_CV_F_SKY13
    f_sky13_sat_bg: bool = False        # SKYLINE_CV_F_SKY13_SAT_BG
    phase_c: bool = False               # SKYLINE_CV_PHASE_C
    multires: bool = False              # SKYLINE_CV_MULTIRES
    html_report: bool = True            # SKYLINE_CV_HTML_REPORT
    html_depth: bool = True             # SKYLINE_CV_HTML_DEPTH
    pano_layered: bool = True           # SKYLINE_CV_PANO_LAYERED
    sv_tall_frame: bool = False         # SKYLINE_SV_TALL_FRAME
    elevated_hires: bool = False        # SKYLINE_ELEVATED_HIRES
    elevated_hires_headings: int = 0    # SKYLINE_ELEVATED_HIRES_HEADINGS

    # ---- height publishing (env only)
    tag_filter: bool = True             # SKYLINE_TAG_FILTER
    prefer_tags: bool = True            # SKYLINE_PREFER_TAGS
    withhold_untagged: bool = True      # SKYLINE_WITHHOLD_UNTAGGED
    withhold_single: bool = True        # SKYLINE_WITHHOLD_SINGLE
    untagged_fallback: str = "model"    # SKYLINE_UNTAGGED_FALLBACK (model / constant)

    # ---- models (env only)
    segformer_size: str = "b3"          # SKYLINE_CV_SEGFORMER_SIZE
    segformer_input_size: int = 512     # SKYLINE_CV_SEGFORMER_INPUT_SIZE
    segformer_device: str = ""          # SKYLINE_CV_SEGFORMER_DEVICE ("" = auto)
    segformer_batch: int | None = None  # SKYLINE_CV_SEGFORMER_BATCH (None = by device)
    mobilesam_checkpoint: str = DEFAULT_MOBILESAM_CHECKPOINT  # MOBILESAM_CHECKPOINT_PATH

    # ---- caches and folders (env only)
    local_dir: str | None = None        # SKYLINE_LOCAL_DIR (None = runs/ beside the code)
    stage_cache: str = "on"             # SKYLINE_STAGE_CACHE (on / off / refresh)
    stage_cache_dir: str | None = None  # SKYLINE_STAGE_CACHE_DIR
    stage_cache_depth_f16: bool = False  # SKYLINE_STAGE_CACHE_DEPTH_F16
    truth_workers: int | None = None    # MAP2STL_TRUTH_WORKERS (benchmark)

    # ------------------------------------------------------------------ building

    @classmethod
    def build(cls, region: str | None = None, *, site: Mapping | None = None,
              env: Mapping[str, str] | None = None,
              overrides: Mapping[str, Any] | None = None) -> SkylineConfig:
        """Defaults, then ``site`` (default: ``sites/<region>.json``), then ``env`` (default:
        ``os.environ``), then ``overrides`` (field name -> value; an unknown name raises)."""
        values: dict[str, Any] = {"region": region}
        if site is None and region:
            from .region_data import _read_site_config  # noqa: PLC0415
            site = _read_site_config(region)
        values.update(_site_values(site or {}))
        values.update(_env_values(os.environ if env is None else env, values))
        if overrides:
            unknown = set(overrides) - _FIELDS
            if unknown:
                raise ValueError(f"unknown SkylineConfig field(s): {sorted(unknown)}")
            values.update(overrides)
        return cls(**values)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> SkylineConfig:
        """Defaults plus the environment (no site): what :func:`current` is outside a run."""
        return cls.build(None, site={}, env=env)

    def replace(self, **changes) -> SkylineConfig:
        return dataclasses.replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


_FIELDS = {f.name for f in dataclasses.fields(SkylineConfig)}


# ---------------------------------------------------------------------------- site JSON


def max_plausible_height(raw) -> float:
    """``max_plausible_height_m`` from the site file: 300 m when absent, invalid or <= 0."""
    try:
        v = float(raw) if raw is not None else 300.0
    except (TypeError, ValueError):
        return 300.0
    if not math.isfinite(v) or v <= 0.0:
        return 300.0
    return v


#: site key -> field, for the plain boolean switches (``bool(site.get(key, False))``).
SITE_FLAGS = {
    "use_satellite_footprints": "use_satellite_footprints",
    "use_cross_view_scoring": "use_cross_view_scoring",
    "use_pano_coastline_recovery": "use_pano_coastline_recovery",
    "drive_pano_recovery_anchor": "drive_pano_recovery_anchor",
    "use_commons_photos": "use_commons_photos",
    "use_satellite_heights": "use_satellite_heights",
    "use_cadastre_band": "cadastre_band",
    "use_survey_heights": "survey_heights",
    "render_pdf": "render_pdf",
    "pano_only_pdf": "pano_only_pdf",
}
#: site key -> field, applied only when the key is present (else the default stays).
SITE_OPTIONAL_FLAGS = {"use_ownership": "ownership", "use_cadastre_occluders": "cadastre_occluders"}


def _site_values(site: Mapping) -> dict[str, Any]:
    out: dict[str, Any] = {field: bool(site.get(key, False)) for key, field in SITE_FLAGS.items()}
    out.update({field: bool(site[key]) for key, field in SITE_OPTIONAL_FLAGS.items()
                if key in site})
    out["max_plausible_height_m"] = max_plausible_height(site.get("max_plausible_height_m"))
    return out


# ---------------------------------------------------------------------------- environment
# Each parser takes the raw value of a variable that IS set and the value so far (default or
# site); it reproduces how the old ``os.environ`` read parsed it.


def _on(raw: str, _cur):
    return raw.strip().lower() in _ON


def _on_case(raw: str, _cur):           # SKYLINE_CV_MULTIRES compared without lower()
    return raw.strip() in _ON


def _not_off(raw: str, _cur):           # "" counts as on
    return raw.strip().lower() not in _OFF


def _not_off_or_empty(raw: str, _cur):  # SKYLINE_CADASTRE_BAND: "" counts as off
    return raw.strip().lower() not in (*_OFF, "")


def _flag_or_keep(raw: str, cur):       # SKYLINE_OWNERSHIP / _CADASTRE_OCCLUDERS: "" keeps
    return cur if raw == "" else raw.strip().lower() not in _OFF


def _survey(raw: str, cur):             # unrecognised value keeps the site flag
    v = raw.strip().lower()
    return True if v in _ON else False if v in _OFF else cur


def _int_or_zero(raw: str, _cur):
    return int(raw or 0)


def _segformer_size(raw: str, _cur):
    size = raw.strip().lower()
    return size if size in _SEGFORMER_SIZES else "b3"


def _segformer_input(raw: str, _cur):
    try:
        n = int(raw.strip())
    except ValueError:
        return 512
    return max(128, n)


def _optional_batch(raw: str, _cur):
    raw = raw.strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return None


def _optional_int(raw: str, _cur):
    try:
        return int(raw)
    except ValueError:
        return None


def _path_or_keep(raw: str, cur):
    return raw.strip() or cur


def _stage_mode(raw: str, _cur):
    v = raw.strip().lower()
    if v in ("0", "off", "false", "no", "disabled", "none"):
        return "off"
    if v in ("refresh", "rebuild", "overwrite"):
        return "refresh"
    return "on"


#: env var -> (field, parser). The whole inventory of env reads the package makes (secrets
#: excepted, see the module doc).
ENV: dict[str, tuple[str, Callable]] = {
    "SKYLINE_CADASTRE_BAND": ("cadastre_band", _not_off_or_empty),
    "SKYLINE_SURVEY_HEIGHTS": ("survey_heights", _survey),
    "SKYLINE_OWNERSHIP": ("ownership", _flag_or_keep),
    "SKYLINE_CADASTRE_OCCLUDERS": ("cadastre_occluders", _flag_or_keep),
    "SKYLINE_CV_F_SKY1": ("f_sky1", _on),
    "SKYLINE_CV_F_SKY5": ("f_sky5", _on),
    "SKYLINE_CV_F_SKY11_1": ("f_sky11_1", _on),
    "SKYLINE_CV_F_SKY12": ("f_sky12", _on),
    "SKYLINE_CV_F_SKY13": ("f_sky13", _on),
    "SKYLINE_CV_F_SKY13_SAT_BG": ("f_sky13_sat_bg", _on),
    "SKYLINE_CV_PHASE_C": ("phase_c", _on),
    "SKYLINE_CV_MULTIRES": ("multires", _on_case),
    "SKYLINE_CV_HTML_REPORT": ("html_report", _on),
    "SKYLINE_CV_HTML_DEPTH": ("html_depth", _on),
    "SKYLINE_CV_PANO_LAYERED": ("pano_layered", _on),
    "SKYLINE_SV_TALL_FRAME": ("sv_tall_frame", _on),
    "SKYLINE_ELEVATED_HIRES": ("elevated_hires", _on),
    "SKYLINE_ELEVATED_HIRES_HEADINGS": ("elevated_hires_headings", _int_or_zero),
    "SKYLINE_TAG_FILTER": ("tag_filter", _not_off),
    "SKYLINE_PREFER_TAGS": ("prefer_tags", lambda raw, _cur: raw != "0"),
    "SKYLINE_WITHHOLD_UNTAGGED": ("withhold_untagged", _not_off),
    "SKYLINE_WITHHOLD_SINGLE": ("withhold_single", _not_off),
    "SKYLINE_UNTAGGED_FALLBACK": ("untagged_fallback", lambda raw, _cur: raw.strip().lower()),
    "SKYLINE_CV_SEGFORMER_SIZE": ("segformer_size", _segformer_size),
    "SKYLINE_CV_SEGFORMER_INPUT_SIZE": ("segformer_input_size", _segformer_input),
    "SKYLINE_CV_SEGFORMER_DEVICE": ("segformer_device", lambda raw, _cur: raw.strip().lower()),
    "SKYLINE_CV_SEGFORMER_BATCH": ("segformer_batch", _optional_batch),
    "MOBILESAM_CHECKPOINT_PATH": ("mobilesam_checkpoint", lambda raw, _cur: raw),
    "SKYLINE_LOCAL_DIR": ("local_dir", _path_or_keep),
    "SKYLINE_STAGE_CACHE": ("stage_cache", _stage_mode),
    "SKYLINE_STAGE_CACHE_DIR": ("stage_cache_dir", _path_or_keep),
    "SKYLINE_STAGE_CACHE_DEPTH_F16": ("stage_cache_depth_f16", _on),
    "MAP2STL_TRUTH_WORKERS": ("truth_workers", _optional_int),
}


def _env_values(env: Mapping[str, str], so_far: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    defaults = _DEFAULTS
    for name, (field, parse) in ENV.items():
        raw = env.get(name)
        if raw is not None:
            cur = so_far.get(field, defaults[field])
            out[field] = parse(raw, cur)
    return out


_DEFAULTS = {f.name: f.default for f in dataclasses.fields(SkylineConfig)}


# ---------------------------------------------------------------------------- active config

_ACTIVE: contextvars.ContextVar[SkylineConfig | None] = contextvars.ContextVar(
    "skyline_config", default=None)
_ENV_CACHE: dict[tuple, SkylineConfig] = {}


@contextmanager
def use(config: SkylineConfig):
    """Make ``config`` the one :func:`current` returns in this context (this job)."""
    token = _ACTIVE.set(config)
    try:
        yield config
    finally:
        _ACTIVE.reset(token)


def active() -> SkylineConfig | None:
    """The config a run activated with :func:`use`, or None outside a run."""
    return _ACTIVE.get()


def current() -> SkylineConfig:
    """The active run's config; outside a run, the defaults plus the current environment."""
    cfg = _ACTIVE.get()
    if cfg is not None:
        return cfg
    key = tuple(os.environ.get(name) for name in ENV)
    cfg = _ENV_CACHE.get(key)
    if cfg is None:
        if len(_ENV_CACHE) > 64:
            _ENV_CACHE.clear()
        cfg = _ENV_CACHE[key] = SkylineConfig.from_env()
    return cfg
