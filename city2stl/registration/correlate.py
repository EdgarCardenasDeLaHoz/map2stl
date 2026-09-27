"""Raster correlation primitives shared by the plate placement and its checks.

Promoted 2026-09-27 from the align tool, where they were only importable with
``tools/align_tool`` on ``sys.path``:

* ``find_peaks`` and ``edges`` from ``locate.py``;
* ``ncc_surface`` and ``height_channel`` from ``refine_guess.py``.

The tool modules now import them from here, so there is one copy.  Pure NumPy.
"""

from __future__ import annotations

import numpy as np

__all__ = ["edges", "find_peaks", "height_channel", "ncc_surface"]


def find_peaks(surface: np.ndarray, separation: int, count: int = 6) -> list[dict]:
    """The strongest peaks, each dominating a neighbourhood of `separation` px.

    A plain argmax returns one arbitrary point of a ridge, which is what a
    straight shoreline produces.  Suppressing a radius around each accepted peak
    turns that ridge into a spaced-out shortlist for a second cue to arbitrate,
    and makes the gap between first and second place mean something.
    """
    work = surface.copy()
    mean, std = float(surface.mean()), float(surface.std()) or 1.0
    out = []
    for _ in range(count):
        idx = int(np.argmax(work))
        y, x = (int(v) for v in np.unravel_index(idx, work.shape))
        out.append({"y": y, "x": x, "z": (float(surface[y, x]) - mean) / std})
        y0, y1 = max(0, y - separation), min(work.shape[0], y + separation + 1)
        x0, x1 = max(0, x - separation), min(work.shape[1], x + separation + 1)
        work[y0:y1, x0:x1] = -np.inf
    return out


def edges(mask: np.ndarray) -> np.ndarray:
    """The outline of a mask: where it changes value between adjacent cells."""
    out = np.zeros_like(mask, dtype=np.float32)
    out[:-1, :] += np.abs(np.diff(mask, axis=0))
    out[1:, :] += np.abs(np.diff(mask, axis=0))
    out[:, :-1] += np.abs(np.diff(mask, axis=1))
    out[:, 1:] += np.abs(np.diff(mask, axis=1))
    return np.minimum(out, 1.0)


def _corr(a_fft, b, shape):
    return np.fft.irfft2(a_fft * np.conj(np.fft.rfft2(b)), s=shape)


def ncc_surface(fixed, template, support, fixed_ffts=None):
    """Pearson correlation of `template` against `fixed`, taken inside `support` only.

    The plain FFT correlation the water solver uses zero-means over the whole frame, which
    hands a larger template a higher score for nothing.  Restricting the statistics to the
    plate's own footprint makes scores from different spans comparable, which is what the
    span sweep needs.  The surface is rolled so that zero shift sits at the centre, the same
    convention as `locate.correlation_surface`.
    """
    shape = fixed.shape
    if fixed_ffts is None:
        fixed_ffts = (np.fft.rfft2(fixed), np.fft.rfft2(fixed * fixed))
    f_fft, f2_fft = fixed_ffts

    n = float(support.sum())
    sum_ft = _corr(f_fft, template * support, shape)
    sum_f = _corr(f_fft, support, shape)
    sum_f2 = _corr(f2_fft, support, shape)
    sum_t = float((template * support).sum())
    sum_t2 = float(((template * support) ** 2).sum())

    num = sum_ft - sum_f * sum_t / n
    var_f = np.maximum(sum_f2 - sum_f * sum_f / n, 0.0)
    var_t = max(sum_t2 - sum_t * sum_t / n, 0.0)
    den = np.sqrt(var_f * var_t)
    out = np.where(den > 1e-9, num / np.maximum(den, 1e-9), 0.0)
    h, w = shape
    return np.roll(out, (h // 2, w // 2), axis=(0, 1))


def height_channel(raw) -> np.ndarray:
    """A height raster as the fit wants it: no NaN, nothing negative, unit maximum.

    NaN is the "no building" sentinel on the OSM side and does not occur on the plate side;
    both become zero, which is what "ground" means to the correlation.  The unit scaling is
    cosmetic -- the correlation is scale-invariant -- but it keeps the two sides' numbers
    comparable when they are printed.
    """
    a = np.nan_to_num(np.asarray(raw, dtype=np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    a = np.maximum(a, 0.0)
    peak = float(a.max())
    return a / peak if peak > 0 else a
