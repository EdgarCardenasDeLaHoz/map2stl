"""building_instances_fast: prompt choice, GPU-side mask filters and the end-to-end label map,
on CPU with a fake MobileSAM predictor (no checkpoint needed)."""

from __future__ import annotations

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from city2stl.skyline import building_instances as bi  # noqa: E402
from city2stl.skyline import building_instances_fast as bif  # noqa: E402

# ------------------------------------------------------------------ fake predictor

class _Transform:
    def apply_coords(self, pts, size):
        return np.asarray(pts, np.float32)            # image frame == model frame


class _PromptEncoder:
    def __call__(self, points, boxes, masks):
        return points[0], None

    def get_dense_pe(self):
        return None


class _Decoder:
    """Per prompt: mask 0 = the box of the building under the point, mask 1 = the whole image,
    mask 2 = a 3 x 3 speck; scores 0.95 / 0.99 / 0.97. Masks at full (padded) resolution."""

    def __init__(self, owner):
        self.o = owner

    def __call__(self, image_embeddings, image_pe, sparse_prompt_embeddings,
                 dense_prompt_embeddings, multimask_output):
        o = self.o
        S = o.model.image_encoder.img_size
        pts = sparse_prompt_embeddings[:, 0].numpy().astype(int)
        out = -np.ones((len(pts), 3, S, S), np.float32)
        h, w = o.input_size
        for i, (x, y) in enumerate(pts):
            gx = x + o.x0
            for (r0, r1, c0, c1) in o.boxes:
                if r0 <= y < r1 and c0 <= gx < c1:
                    out[i, 0, r0:r1, max(c0 - o.x0, 0):max(min(c1 - o.x0, w), 0)] = 1
            out[i, 1, :h, :w] = 1
            out[i, 2, y:y + 3, x:x + 3] = 1
        sc = torch.tensor([[0.95, 0.99, 0.97]] * len(pts))
        o.decoded += len(pts)
        return torch.from_numpy(out), sc


class _Model:
    mask_threshold = 0.0

    def __init__(self, owner):
        self.prompt_encoder = _PromptEncoder()
        self.mask_decoder = _Decoder(owner)
        self.image_encoder = type("E", (), {"img_size": 0})()

    def to(self, device):
        return self


class FakePredictor:
    def __init__(self, rgb, boxes):
        self.rgb, self.boxes = rgb, boxes
        self.model = _Model(self)
        self.transform = _Transform()
        self.device = torch.device("cpu")
        self.features = None
        self.decoded = self.encoded = 0

    def set_image(self, img):
        # locate the window in the pano by its pixels (columns are unique in the fake image)
        self.x0 = int(img[0, 0, 0]) + 256 * int(img[0, 0, 1])
        h, w = img.shape[:2]
        self.input_size = (h, w)
        self.model.image_encoder.img_size = max(h, w)
        self.encoded += 1

    def reset_image(self):
        pass

    def predict_torch(self, point_coords, point_labels, multimask_output=True):
        low, sc = self.model.mask_decoder(None, None, point_coords, None, True)
        h, w = self.input_size
        return low[..., :h, :w] > 0, sc, low


@pytest.fixture
def scene(monkeypatch):
    H, W = 120, 400
    rgb = np.zeros((H, W, 3), np.uint8)
    rgb[:, :, 0] = np.arange(W) % 256
    rgb[:, :, 1] = np.arange(W) // 256
    boxes = [(40, 120, 20, 60), (70, 120, 100, 180), (20, 120, 230, 250), (60, 120, 290, 330)]
    building = np.zeros((H, W), bool)
    for r0, r1, c0, c1 in boxes:
        building[r0:r1, c0:c1] = True
    pred = FakePredictor(rgb, boxes)
    from city2stl.skyline._core import segmentation as sg

    monkeypatch.setattr(sg, "_ensure_mobilesam", lambda: True)
    monkeypatch.setattr(sg, "_mobilesam_predictor", pred, raising=False)
    return rgb, building, boxes, pred


def _ref_select(m, sc, sub, x0, x1, W, min_score=0.8, min_bf=0.8, max_wf=0.45, min_px=60):
    """building_instances' Python loop over one prompt's three masks."""
    for k in np.argsort(-sc):
        mm = m[k]
        a = int(mm.sum())
        if sc[k] < min_score or a < min_px:
            continue
        if (mm & sub).sum() / a < min_bf:
            continue
        xs = np.flatnonzero(mm.any(0))
        if xs[-1] - xs[0] > max_wf * (x1 - x0):
            continue
        if (xs[0] == 0 and x0 > 0) or (xs[-1] == x1 - x0 - 1 and x1 < W):
            continue
        return bi._crop(float(sc[k]), mm & sub, x0)
    return None


# ------------------------------------------------------------------ prompts

def test_grid_points_match_the_original_grid():
    sub = np.zeros((50, 40), bool)
    sub[10:45, 5:30] = True
    p = bif.grid_points(sub, 12)
    gy, gx = np.mgrid[6:50:12, 6:40:12]
    ok = sub[gy, gx]
    assert np.array_equal(p, np.column_stack([gx[ok], gy[ok]]).astype(np.float32))


def test_coarse_grid_is_a_subset_of_the_fine_grid():
    sub = np.ones((100, 100), bool)
    fine = {tuple(p) for p in bif.grid_points(sub, 12)}
    coarse = {tuple(p) for p in bif.grid_points(sub, 24, phase=18)}
    assert coarse and coarse <= fine


def test_peak_points_one_set_per_tower(scene):
    _rgb, building, boxes, _ = scene
    p = bif.peak_points(building, 12, radius_px=15)
    xs = sorted({int(x) for x in p[:, 0]})
    # one peak column per box (each box is its own flat top)
    assert len(xs) == len(boxes)
    for (_r0, _r1, c0, c1), x in zip(sorted(boxes, key=lambda b: b[2]), xs, strict=True):
        assert c0 <= x < c1
    assert all(building[int(y), int(x)] for x, y in p)


def test_refine_gaps_and_edges():
    sub = np.zeros((60, 60), bool)
    sub[:, :] = True
    a = np.zeros((60, 30), bool)
    a[:] = True
    found = [(0.9, 0, 0, a)]                         # left half covered, right half a gap
    used = np.zeros((0, 2), np.float32)
    g = bif.refine_points(sub, found, 0, 12, "gaps", 12, used)
    assert len(g) and (g[:, 0] >= 30).all()
    b = np.ones((60, 30), bool)
    found2 = found + [(0.9, 0, 30, b)]               # two masks side by side: no gaps
    assert len(bif.refine_points(sub, found2, 0, 12, "gaps", 12, used)) == 0
    e = bif.refine_points(sub, found2, 0, 12, "gaps_edges", 12, used)
    assert len(e) and (np.abs(e[:, 0] - 30) <= 12).all()
    # points already prompted are not prompted again
    assert len(bif.refine_points(sub, found2, 0, 12, "gaps_edges", 12, e)) == 0


# ------------------------------------------------------------------ decoding

def test_decoder_filters_match_the_python_loop(scene):
    rgb, building, boxes, pred = scene
    x0, x1, W = 160, 384, building.shape[1]
    sub = building[:, x0:x1]
    pred.set_image(np.ascontiguousarray(rgb[:, x0:x1]))
    pts = bif.grid_points(sub, 12)
    dec = bif._Decoder(pred, torch.as_tensor(sub), x0, x1, W, 0.8, 0.8, 0.45, 60, 5, False)
    got = dec(pts)
    low, sc = pred.model.mask_decoder(None, None, torch.as_tensor(pts)[:, None, :], None, True)
    h, w = sub.shape
    m = (low[..., :h, :w] > 0).numpy()
    ref = [r for r in (_ref_select(m[i], sc[i].numpy(), sub, x0, x1, W) for i in range(len(pts)))
           if r is not None]
    assert len(got) == len(ref) > 0
    for (s1, y1, xa, m1), (s2, y2, xb, m2) in zip(got, ref, strict=True):
        assert (y1, xa) == (y2, xb) and np.array_equal(m1, m2) and s1 == pytest.approx(s2)


@pytest.mark.parametrize("kw", [{}, {"seeds": "coarse", "refine": "gaps_edges"},
                                {"seeds": "peaks", "refine": "gaps"}])
def test_end_to_end_matches_original(scene, kw):
    rgb, building, boxes, pred = scene
    ref = bi.building_instances(rgb, building, device="cpu", window_px=224)
    n_ref = pred.decoded
    pred.decoded = 0
    st = {}
    lab = bif.building_instances(rgb, building, device="cpu", window_px=224, stats=st, **kw)
    assert lab.max() == ref.max() == len(boxes)
    # same partition of the building pixels
    for i in range(1, ref.max() + 1):
        vals = np.unique(lab[ref == i])
        assert len(vals) == 1 and vals[0] > 0
    assert st["decoder_prompts"] == pred.decoded
    if kw:
        assert st["decoder_prompts"] < n_ref


def test_points_seed_with_no_points_and_no_refine_gives_empty_map(scene):
    rgb, building, _boxes, _pred = scene
    lab = bif.building_instances(rgb, building, device="cpu", window_px=224, seeds="points",
                                 points=np.zeros((0, 2)))
    assert lab.max() == 0


def test_full_cover_reaches_the_last_columns(scene):
    rgb, building, _boxes, pred = scene
    building[60:, 350:390] = True                    # beyond the last snapped window (112-336)
    pred.boxes = pred.boxes + [(60, 120, 350, 390)]
    old = bi.building_instances(rgb, building, device="cpu", window_px=224)
    new = bif.building_instances(rgb, building, device="cpu", window_px=224)
    assert old[60:, 350:390].max() == 0
    assert len(np.unique(new[60:, 350:390])) == 1 and new[60:, 350:390].min() > 0
    same = bif.building_instances(rgb, building, device="cpu", window_px=224, full_cover=False)
    assert np.array_equal(same > 0, old > 0)


# ------------------------------------------------------------------ GPU memory sizing

class _FakeCuda:
    """torch.cuda stand-in: a 4 GiB card with ``used`` bytes taken, a decoder probe that peaks
    at ``per_prompt`` bytes a prompt, and ``reserved`` bytes held by this process."""

    def __init__(self, used, per_prompt, reserved):
        self.used, self.per, self.reserved, self.peak = used, per_prompt, reserved, 0

    def mem_get_info(self):
        return 4 * 2**30 - self.used, 4 * 2**30

    def memory_allocated(self):
        return 0

    def max_memory_allocated(self):
        return self.peak

    def memory_reserved(self):
        return self.reserved

    def max_memory_reserved(self):
        return self.reserved

    def reset_peak_memory_stats(self):
        self.peak = 0

    def synchronize(self):
        self.peak = max(self.peak, 8 * self.per)        # the probe's 8 prompts

    def empty_cache(self):
        pass


@pytest.fixture
def fake_cuda(monkeypatch):
    def make(used, per_prompt, reserved=0):
        fc = _FakeCuda(used, per_prompt, reserved)
        for name in ("mem_get_info", "memory_allocated", "max_memory_allocated",
                     "memory_reserved", "max_memory_reserved", "reset_peak_memory_stats",
                     "synchronize", "empty_cache"):
            monkeypatch.setattr(torch.cuda, name, getattr(fc, name))
        monkeypatch.setattr(bif, "_DEC_PER_PROMPT", {})
        return fc
    return make


def test_batches_are_sized_from_free_gpu_memory(scene, fake_cuda):
    rgb, building, boxes, pred = scene
    fake_cuda(used=1 * 2**30, per_prompt=32 * 2**20)
    st = {}
    lab = bif.building_instances(rgb, building, device="cuda", window_px=224, stats=st)
    # 3 GiB cap - 1 GiB used = 2 GiB; 35 % of it for the decoder at 32 MiB a prompt
    assert st["batch"] == int(0.35 * 2**31 // (32 * 2**20)) == 22
    up = bif._upsample_bytes(224, 120, 224)
    assert st["upsample_chunk"] == min(22, int(0.35 * 2**31 // up))
    assert lab.max() == len(boxes)


def test_cpu_keeps_fixed_batches(scene):
    rgb, building, _boxes, _pred = scene
    st = {}
    bif.building_instances(rgb, building, device="cpu", window_px=224, stats=st)
    assert (st["batch"], st["upsample_chunk"]) == (64, 16)


def test_reserved_memory_over_the_limit_aborts(scene, fake_cuda):
    rgb, building, _boxes, _pred = scene
    fake_cuda(used=1 * 2**30, per_prompt=32 * 2**20, reserved=int(3.6 * 2**30))
    with pytest.raises(bif.GpuBudgetExceeded):
        bif.building_instances(rgb, building, device="cuda", window_px=224)
