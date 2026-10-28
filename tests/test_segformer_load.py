"""SegFormer loading must avoid torch.load (.bin) and must report failures, not hide them."""
import logging
from unittest import mock

from city2stl.skyline._core import segmentation as seg


def _reset(monkeypatch):
    monkeypatch.setattr(seg, "_SEGFORMER_LOADED", False)
    monkeypatch.setattr(seg, "_SEGFORMER_OK", False)


def test_safetensors_first(monkeypatch):
    sentinel = object()
    fp = mock.MagicMock(return_value=sentinel)
    with mock.patch("transformers.SegformerForSemanticSegmentation.from_pretrained", fp):
        assert seg._load_segformer_model("nvidia/segformer-b3-finetuned-ade-512-512") is sentinel
    assert fp.call_args.kwargs["use_safetensors"] is True


def test_falls_back_to_cached_safetensors(monkeypatch, tmp_path):
    snap = tmp_path / "models--a--b" / "snapshots" / "abc"
    snap.mkdir(parents=True)
    (snap / "model.safetensors").write_bytes(b"x")
    model = mock.MagicMock()
    with mock.patch("transformers.SegformerForSemanticSegmentation.from_pretrained",
                    side_effect=OSError("no safetensors")), \
         mock.patch("transformers.SegformerForSemanticSegmentation", return_value=model) as cls, \
         mock.patch("transformers.SegformerConfig.from_pretrained", return_value="cfg"), \
         mock.patch("huggingface_hub.constants.HF_HUB_CACHE", str(tmp_path)), \
         mock.patch("safetensors.torch.load_file", return_value={"w": 1}) as lf:
        cls.from_pretrained = mock.MagicMock(side_effect=OSError("no safetensors"))
        assert seg._load_segformer_model("a/b") is model
    lf.assert_called_once()
    model.load_state_dict.assert_called_once_with({"w": 1})


def test_failure_is_logged_and_returns_false(monkeypatch, caplog):
    _reset(monkeypatch)
    monkeypatch.setattr(seg, "_load_segformer_model", mock.Mock(side_effect=RuntimeError("boom")))
    with mock.patch("transformers.SegformerImageProcessor.from_pretrained", return_value=mock.Mock()), \
         caplog.at_level(logging.WARNING, logger=seg.logger.name):
        assert seg._ensure_segformer() is False
    assert "boom" in caplog.text
