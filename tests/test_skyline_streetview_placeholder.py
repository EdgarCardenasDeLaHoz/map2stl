"""streetview_io._is_no_imagery_placeholder: only a truly flat grey frame is the placeholder.

A rejected frame gets a permanent negative-cache marker, so the old channel-mean rule
(per-channel mean spread < 3, std < 32) lost real dusk and fog views for good: 7 of 30 for
the Chicago wickerSKY drone sphere.
"""
import cv2
import numpy as np

from city2stl.skyline.streetview_io import _is_no_imagery_placeholder as is_ph


def _placeholder():
    img = np.full((640, 640, 3), 228, np.uint8)            # flat light grey background
    cv2.putText(img, "Sorry, we have no imagery here.", (40, 320),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (120, 120, 120), 2)
    cv2.circle(img, (320, 220), 50, (170, 170, 170), -1)
    return img


def _dusk_view(seed=0):
    """Dark, near-monochrome but noisy: a sky gradient over a dim city with colour noise."""
    rng = np.random.default_rng(seed)
    y = np.linspace(90, 20, 640)[:, None, None]
    img = np.repeat(np.repeat(y, 640, axis=1), 3, axis=2)
    img = img + rng.normal(0, 8, (640, 640, 3)) + np.array([3, 0, -3])
    img[400:, ::37] += 60                                   # lit windows
    return np.clip(img, 0, 255).astype(np.uint8)


def _fog_view(seed=1):
    rng = np.random.default_rng(seed)
    img = np.full((640, 640, 3), 180.0) + rng.normal(0, 7, (640, 640, 3))
    img[300:, :, :] -= 40                                   # dim ground band
    return np.clip(img, 0, 255).astype(np.uint8)


def test_flat_frames_are_placeholders():
    assert is_ph(np.full((64, 64, 3), 187, np.uint8))
    assert is_ph(np.full((64, 64, 3), 40, np.uint8))
    assert is_ph(np.zeros((0, 0, 3), np.uint8))
    assert is_ph(None)


def test_text_placeholder_is_caught():
    img = _placeholder()
    assert img.std() > 6                                    # not caught by the flat rule alone
    assert is_ph(img)


def test_dusk_and_fog_views_pass():
    for img in (_dusk_view(), _fog_view()):
        ch = img.reshape(-1, 3).mean(0)
        assert np.std(ch) < 3.0 and img.std() < 32          # what the old rule rejected
        assert not is_ph(img)


def test_grey_photo_without_dominant_level_passes():
    rng = np.random.default_rng(2)
    g = rng.integers(140, 240, (640, 640, 1)).astype(np.uint8)
    assert not is_ph(np.repeat(g, 3, axis=2))
