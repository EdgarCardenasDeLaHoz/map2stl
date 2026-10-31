"""The two network definitions behind :mod:`city2stl.height.satellite.geopose` (torch; imported
only when a backend is loaded, never at package import).

- :class:`UnetVFLOW`: the pubgeo ``monocular-geocentric-pose`` baseline (Christie et al., CVPRW
  2021, Apache-2.0): a U-Net on a ResNet-34 with an AGL (height above ground) map, a "magnitude"
  map and an image-level lean direction. Re-implemented here with torchvision's ResNet so the
  checkpoint (``geopose_baseline.pth``, keys ``encoder.* decoder.* xydir_head.* height_head.*
  mag_head.*``) loads without ``segmentation_models_pytorch``; the lean scale is the least-squares
  fit ``mag = scale * height`` of the upstream ``ScaleHead``.
- :class:`DPTDino`: the RS3DAda / SynRS3D height network (Song et al., NeurIPS 2024, MIT; AltiMap
  is a fine-tune of it): DINOv2 ViT-L/14 encoder (timm's ``vit_large_patch14_dinov2``, same
  parameter names as the hub model) and a DPT regression head, 518 px tiles. Only the regression
  (height) head is built; the checkpoint's segmentation head is ignored.

Both take a normalised ``(B, 3, H, W)`` float tensor and return metres. Weights are loaded with
``torch.load(..., weights_only=True)`` by the caller; no code from a checkpoint is executed.
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F  # noqa: N812
import torchvision

IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)
#: SynRS3D normalisation on the 0-255 scale (``infer_height.py``).
DINO_MEAN = (123.675, 116.28, 103.53)
DINO_STD = (58.395, 57.12, 57.375)


# ------------------------------------------------------------------ pubgeo baseline

class _ConvBNReLU(nn.Sequential):
    def __init__(self, cin: int, cout: int):
        super().__init__(nn.Conv2d(cin, cout, 3, padding=1, bias=False), nn.BatchNorm2d(cout),
                         nn.ReLU(inplace=True))


class _DecoderBlock(nn.Module):
    def __init__(self, cin: int, skip: int, cout: int):
        super().__init__()
        self.conv1 = _ConvBNReLU(cin + skip, cout)
        self.conv2 = _ConvBNReLU(cout, cout)

    def forward(self, x, skip=None):
        x = F.interpolate(x, scale_factor=2, mode="nearest")
        if skip is not None:
            x = torch.cat([x, skip], 1)
        return self.conv2(self.conv1(x))


class _Decoder(nn.Module):
    def __init__(self):
        super().__init__()
        # head 512 (layer4); skips 256, 128, 64, 64 (layer3, layer2, layer1, stem), then none
        self.blocks = nn.ModuleList([_DecoderBlock(512, 256, 256), _DecoderBlock(256, 128, 128),
                                     _DecoderBlock(128, 64, 64), _DecoderBlock(64, 64, 32),
                                     _DecoderBlock(32, 0, 16)])


class UnetVFLOW(nn.Module):
    """pubgeo baseline: returns ``(xydir (B,2), height (B,1,H,W), scale (B,))``.

    ``xydir`` is ``(sin a, cos a)`` of the lean angle ``a``; ``scale`` is lean pixels per metre of
    height *in the input's own pixels*."""

    def __init__(self):
        super().__init__()
        self.encoder = torchvision.models.resnet34(weights=None)
        del self.encoder.fc, self.encoder.avgpool
        self.decoder = _Decoder()
        self.xydir_head = nn.Sequential(nn.AdaptiveAvgPool2d(1), nn.Flatten(), nn.Dropout(0.5),
                                        nn.Linear(512, 2))
        self.height_head = nn.Sequential(nn.Conv2d(16, 1, 3, padding=1))
        self.mag_head = nn.Sequential(nn.Conv2d(16, 1, 3, padding=1))

    def forward(self, x):
        e = self.encoder
        s0 = e.relu(e.bn1(e.conv1(x)))                  # /2, 64
        s1 = e.layer1(e.maxpool(s0))                    # /4, 64
        s2 = e.layer2(s1)                               # /8, 128
        s3 = e.layer3(s2)                               # /16, 256
        s4 = e.layer4(s3)                               # /32, 512
        d = s4
        for blk, skip in zip(self.decoder.blocks, (s3, s2, s1, s0, None), strict=True):
            d = blk(d, skip)
        height, mag = self.height_head(d), self.mag_head(d)
        h, m = height.flatten(1), mag.flatten(1)
        scale = (h * m).sum(1) / ((h * h).sum(1) + 0.01)
        return self.xydir_head(s4), height, scale


# ------------------------------------------------------------------ RS3DAda / AltiMap

class _ResUnit(nn.Module):
    def __init__(self, c: int):
        super().__init__()
        self.conv1 = nn.Conv2d(c, c, 3, padding=1)
        self.conv2 = nn.Conv2d(c, c, 3, padding=1)
        self.bn1, self.bn2 = nn.BatchNorm2d(c), nn.BatchNorm2d(c)

    def forward(self, x):
        out = self.bn1(self.conv1(F.relu(x)))
        return self.bn2(self.conv2(F.relu(out))) + x


class _Fusion(nn.Module):
    def __init__(self, c: int):
        super().__init__()
        self.out_conv = nn.Conv2d(c, c, 1)
        self.resConfUnit1, self.resConfUnit2 = _ResUnit(c), _ResUnit(c)

    def forward(self, *xs, size=None):
        out = xs[0]
        if len(xs) == 2:
            out = out + self.resConfUnit1(xs[1])
        out = self.resConfUnit2(out)
        out = F.interpolate(out, **({"size": size} if size is not None else {"scale_factor": 2}),
                            mode="bilinear", align_corners=True)
        return self.out_conv(out)


class _DPTHead(nn.Module):
    def __init__(self, dim: int = 1024, feat: int = 256, outs=(256, 512, 1024, 1024)):
        super().__init__()
        self.projects = nn.ModuleList([nn.Conv2d(dim, o, 1) for o in outs])
        self.resize_layers = nn.ModuleList([
            nn.ConvTranspose2d(outs[0], outs[0], 4, stride=4),
            nn.ConvTranspose2d(outs[1], outs[1], 2, stride=2), nn.Identity(),
            nn.Conv2d(outs[3], outs[3], 3, stride=2, padding=1)])
        self.scratch = nn.Module()
        for i, o in enumerate(outs, 1):
            setattr(self.scratch, f"layer{i}_rn", nn.Conv2d(o, feat, 3, padding=1, bias=False))
            setattr(self.scratch, f"refinenet{i}", _Fusion(feat))
        self.scratch.output_conv1 = nn.Conv2d(feat, feat // 2, 3, padding=1)
        self.scratch.output_conv2 = nn.Sequential(nn.Conv2d(feat // 2, 32, 3, padding=1),
                                                  nn.Conv2d(32, 1, 1), nn.Identity())

    def forward(self, feats, ph, pw):
        outs = []
        for i, x in enumerate(feats):
            x = x.permute(0, 2, 1).reshape(x.shape[0], x.shape[-1], ph, pw)
            outs.append(self.resize_layers[i](self.projects[i](x)))
        s = self.scratch
        l1, l2, l3, l4 = (getattr(s, f"layer{i}_rn")(o) for i, o in enumerate(outs, 1))
        p4 = s.refinenet4(l4, size=l3.shape[2:])
        p3 = s.refinenet3(p4, l3, size=l2.shape[2:])
        p2 = s.refinenet2(p3, l2, size=l1.shape[2:])
        p1 = s.refinenet1(p2, l1)
        out = F.interpolate(s.output_conv1(p1), (ph * 14, pw * 14), mode="bilinear",
                            align_corners=True)
        return s.output_conv2(out)


class DPTDino(nn.Module):
    """RS3DAda height network: ``(B,3,H,W)`` (H, W multiples of 14) -> ``(B,1,H,W)`` metres.
    State-dict keys ``pretrained.*`` (encoder) and ``heads.regression.*`` (head)."""

    def __init__(self):
        super().__init__()
        import timm  # noqa: PLC0415

        self.pretrained = timm.create_model("vit_large_patch14_dinov2", pretrained=False,
                                            img_size=518, num_classes=0, dynamic_img_size=True)
        self.heads = nn.ModuleDict({"regression": _DPTHead()})

    def load_checkpoint(self, sd: dict) -> None:
        """Load a RS3DAda / AltiMap state dict (the segmentation head and DINOv2's unused
        ``mask_token`` are ignored); raises if anything else is missing or unexpected."""
        sd = {k: v for k, v in sd.items() if not k.startswith("heads.segmentation.")
              and k != "pretrained.mask_token"}
        res = self.load_state_dict(sd, strict=False)
        bad = [k for k in list(res.missing_keys) + list(res.unexpected_keys)
               if not k.endswith("num_batches_tracked")]
        if bad:
            raise RuntimeError(f"checkpoint does not match DPTDino: {bad[:6]}")

    def forward(self, x):
        h, w = x.shape[-2:]
        feats = self.pretrained.get_intermediate_layers(x, 4, norm=True)
        out = self.heads["regression"](feats, h // 14, w // 14)
        return F.interpolate(out, size=(h, w), mode="bilinear", align_corners=True)


__all__ = ["UnetVFLOW", "DPTDino", "IMAGENET_MEAN", "IMAGENET_STD", "DINO_MEAN", "DINO_STD"]
