"""
tools.ml.models — Network architectures for building analysis.

Primary model: RoofNetV2
  - MobileNetV3-Small backbone (pretrained ImageNet, 2.5M params)
  - Dual heads: height regression (dense H*W) + shape classification (6 classes)
  - Building footprint mask support for shape head
  - Designed for later incorporation of DenseNet-style dense connections
    and Retna_V2 multi-scale aggregation tricks

Legacy models (UNet, Retna_V1, Retna_V2, RoofNet v1) remain in
tools/networks.py for backward compatibility.

Inference-only code lives in city2stl/roof_classifier.py and
city2stl/height/predict.py — this module is training-side only.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Lazy torch imports — module can be imported without torch installed
_TORCH_AVAILABLE = False
try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    _TORCH_AVAILABLE = True
except ImportError:
    pass


def _require_torch():
    if not _TORCH_AVAILABLE:
        raise ImportError(
            "PyTorch is required for model definitions. "
            "Install with: pip install torch torchvision"
        )


# ---------------------------------------------------------------------------
# MobileNetV3 feature extractor
# ---------------------------------------------------------------------------

def _build_mobilenet_backbone(
    pretrained: bool = True,
    freeze_bn: bool = False,
    in_channels: int = 3,
) -> "nn.Module":
    """Build a MobileNetV3-Small backbone, returning the feature extractor.

    The classifier head is removed.  Output is a feature tensor from the
    last convolutional layer (576 channels at 1/32 input resolution).

    Parameters
    ----------
    pretrained : bool
        Load ImageNet-pretrained weights.
    freeze_bn : bool
        Freeze BatchNorm layers (useful for small batch sizes during
        fine-tuning).
    in_channels : int
        Number of input channels.  Default 3 (RGB).  When >3, the first conv
        is rebuilt: existing weights for the first 3 channels are kept, and
        the extra channels are initialised to the mean of the original RGB
        weights so the pretrained features still apply.
    """
    _require_torch()
    from torchvision.models import mobilenet_v3_small, MobileNet_V3_Small_Weights

    weights = MobileNet_V3_Small_Weights.DEFAULT if pretrained else None
    backbone = mobilenet_v3_small(weights=weights)
    features = backbone.features  # nn.Sequential

    if in_channels != 3:
        # First conv is features[0][0] (Conv2dNormActivation -> Conv2d)
        first = features[0][0]
        old_w = first.weight.data  # (out, 3, k, k)
        new_first = nn.Conv2d(
            in_channels, first.out_channels,
            kernel_size=first.kernel_size,
            stride=first.stride,
            padding=first.padding,
            bias=first.bias is not None,
        )
        with torch.no_grad():
            new_w = new_first.weight.data
            new_w[:, :3] = old_w
            if in_channels > 3:
                # Init extra channels with mean of RGB weights, attenuated
                mean_w = old_w.mean(dim=1, keepdim=True)
                new_w[:, 3:] = mean_w.repeat(1, in_channels - 3, 1, 1) * 0.1
            if first.bias is not None:
                new_first.bias.data.copy_(first.bias.data)
        features[0][0] = new_first

    if freeze_bn:
        for m in features.modules():
            if isinstance(m, (nn.BatchNorm2d, nn.SyncBatchNorm)):
                m.eval()
                for p in m.parameters():
                    p.requires_grad = False

    return features


def _mobilenet_out_channels() -> int:
    """Number of output channels from MobileNetV3-Small features."""
    return 576


# ---------------------------------------------------------------------------
# Feature Pyramid Neck (lightweight multi-scale fusion)
# ---------------------------------------------------------------------------

class _FPN(nn.Module):
    """Simple Feature Pyramid Network for multi-scale features.

    Takes the MobileNetV3 feature map (single scale) and produces a
    multi-resolution feature stack, upsampled back to a common size.
    This provides the dense spatial info needed by the height head and
    gives the shape head richer multi-scale context.

    A future version can incorporate Retna_V2-style iterative aggregation
    and DenseNet-style skip connections here.
    """

    def __init__(self, in_channels: int = 576, mid_channels: int = 128):
        super().__init__()
        self.lateral = nn.Conv2d(in_channels, mid_channels, 1)
        self.smooth = nn.Sequential(
            nn.Conv2d(mid_channels, mid_channels, 3, padding=1),
            nn.BatchNorm2d(mid_channels),
            nn.ReLU(inplace=True),
        )
        self.upsample_conv = nn.Sequential(
            nn.Conv2d(mid_channels, mid_channels, 3, padding=1),
            nn.BatchNorm2d(mid_channels),
            nn.ReLU(inplace=True),
        )
        self.out_channels = mid_channels

    def forward(self, x: "torch.Tensor", target_size: tuple[int, int]) -> "torch.Tensor":
        x = self.lateral(x)
        x = self.smooth(x)
        x = F.interpolate(x, size=target_size, mode="bilinear", align_corners=False)
        x = self.upsample_conv(x)
        return x


# ---------------------------------------------------------------------------
# RoofNetV2
# ---------------------------------------------------------------------------

class RoofNetV2(nn.Module):
    """Multi-task building analysis network (v2).

    Architecture:
      MobileNetV3-Small backbone (pretrained) -> FPN neck -> two heads:

      * **height_head** — dense H*W regression (pseudo-nDSM).
        Output: B x 1 x H x W (raw, apply clamp post-hoc).

      * **shape_head** — building footprint-masked global pool -> classifier.
        Output: B x n_classes logits.

    The backbone is pretrained on ImageNet and fine-tuned end-to-end.
    For small datasets, freeze the backbone initially and train heads only,
    then unfreeze for full fine-tuning.

    Parameters
    ----------
    n_classes : int
        Number of roof shape classes.  Default 6.
    fpn_channels : int
        Feature pyramid intermediate channels.  Default 128.
    pretrained : bool
        Use ImageNet-pretrained MobileNetV3 weights.
    freeze_backbone : bool
        Freeze backbone weights (train heads only).
    """

    def __init__(
        self,
        n_classes: int = 6,
        fpn_channels: int = 128,
        pretrained: bool = True,
        freeze_backbone: bool = False,
    ) -> None:
        _require_torch()
        super().__init__()

        self.n_classes = n_classes
        self.backbone = _build_mobilenet_backbone(pretrained=pretrained)
        backbone_ch = _mobilenet_out_channels()

        if freeze_backbone:
            for p in self.backbone.parameters():
                p.requires_grad = False

        self.fpn = _FPN(backbone_ch, fpn_channels)

        # Height head: dense per-pixel regression + dropout for regularization
        self.height_head = nn.Sequential(
            nn.Conv2d(fpn_channels, fpn_channels // 2, 3, padding=1),
            nn.ReLU(inplace=True),
            nn.Dropout2d(0.2),
            nn.Conv2d(fpn_channels // 2, 1, 1),
        )

        # Shape head: masked global pool -> classifier
        self.shape_pool = nn.AdaptiveAvgPool2d(1)
        self.shape_head = nn.Sequential(
            nn.Flatten(),
            nn.Dropout(0.2),
            nn.Linear(fpn_channels, n_classes),
        )

    def forward(
        self,
        x: "torch.Tensor",
        mask: "torch.Tensor | None" = None,
    ) -> "tuple[torch.Tensor, torch.Tensor]":
        """Run both heads.

        Parameters
        ----------
        x : Tensor, B x 3 x H x W (normalised with ImageNet stats)
        mask : Tensor (optional), B x 1 x H x W or B x H x W
            Building footprint mask. Applied before shape pooling.

        Returns
        -------
        height_map : Tensor B x 1 x H x W (raw; clamp to [0, MAX_HEIGHT_M])
        shape_logits : Tensor B x n_classes
        """
        input_size = x.shape[2:]

        feat = self.backbone(x)  # B x 576 x H/32 x W/32
        feat = self.fpn(feat, target_size=input_size)  # B x fpn_ch x H x W

        # Height head
        height_map = self.height_head(feat)

        # Shape head with optional mask
        shape_feat = feat
        if mask is not None:
            m = mask.float()
            if m.dim() == 3:
                m = m.unsqueeze(1)
            if m.shape[2:] != feat.shape[2:]:
                m = F.interpolate(m, size=feat.shape[2:], mode="nearest")
            shape_feat = shape_feat * m

        shape_feat = self.shape_pool(shape_feat)
        shape_logits = self.shape_head(shape_feat)

        return height_map, shape_logits

    def predict_shape(self, x: "torch.Tensor", mask: "torch.Tensor | None" = None) -> "torch.Tensor":
        """Shape classification only (no height computation)."""
        _, logits = self.forward(x, mask)
        return logits

    def predict_height(self, x: "torch.Tensor") -> "torch.Tensor":
        """Height regression only."""
        h, _ = self.forward(x)
        return h

    def unfreeze_backbone(self) -> None:
        """Unfreeze backbone for full fine-tuning."""
        for p in self.backbone.parameters():
            p.requires_grad = True

    def freeze_backbone(self) -> None:
        """Freeze backbone (train heads only)."""
        for p in self.backbone.parameters():
            p.requires_grad = False

    def trainable_params(self) -> int:
        return sum(p.numel() for p in self.parameters() if p.requires_grad)

    def total_params(self) -> int:
        return sum(p.numel() for p in self.parameters())


# ---------------------------------------------------------------------------
# RoofNetV3 -- iterative refinement with explicit segmentation mask head
# ---------------------------------------------------------------------------

class RoofNetV3(nn.Module):
    """Iterative refinement building analysis network (v3).

    Differs from V2 in three ways:

    1. **Higher input resolution support** -- the architecture is fully
       convolutional and works at any size (typically 512x512 here).

    2. **Explicit segmentation head** -- a sigmoid mask predicts "is this a
       building pixel?" supervised by Dice + BCE.  The height head still
       regresses per-pixel metres, supervised by weighted L1 + Sobel + Dice on
       overlap.  Final inference output is mask_prob * height_pred so the
       segmentation gates the regression.

    3. **Iterative refinement** -- input is 5 channels (RGB + prev_mask +
       prev_height_normalised).  Forward runs N iterations: first pass takes
       a zero prior, subsequent passes feed the previous (mask, height) as
       extra channels.  Each iteration sharpens the prediction.

    Heads
    -----
    - mask_head   : 1xHxW sigmoid (apply torch.sigmoid post-hoc, or use raw
                    logits with BCEWithLogitsLoss during training).
    - height_head : 1xHxW raw (clamp to [0, MAX_HEIGHT_M] post-hoc).
    - shape_head  : Bxn_classes logits for ROOF-2 compatibility.

    Parameters
    ----------
    n_classes : int
        Number of roof shape classes.  Default 6.
    fpn_channels : int
        Feature pyramid intermediate channels.  Default 128.
    pretrained : bool
        Use ImageNet-pretrained MobileNetV3 weights.
    freeze_backbone : bool
        Freeze backbone weights (train heads only).
    height_norm : float
        Normalising scale applied to height channel of the prior input.
        Default 50.0 m -- prior heights are divided by this before being
        fed back in, so the network sees values in roughly [0, 1].
    """

    HEIGHT_NORM: float = 50.0

    def __init__(
        self,
        n_classes: int = 6,
        fpn_channels: int = 128,
        pretrained: bool = True,
        freeze_backbone: bool = False,
        height_norm: float = HEIGHT_NORM,
    ) -> None:
        _require_torch()
        super().__init__()

        self.n_classes = n_classes
        self.height_norm = height_norm
        self.in_channels = 5  # RGB + prev_mask + prev_height_norm

        self.backbone = _build_mobilenet_backbone(
            pretrained=pretrained, in_channels=self.in_channels,
        )
        backbone_ch = _mobilenet_out_channels()

        if freeze_backbone:
            for p in self.backbone.parameters():
                p.requires_grad = False

        self.fpn = _FPN(backbone_ch, fpn_channels)

        # Mask head: dense per-pixel sigmoid logits (BCE with logits)
        self.mask_head = nn.Sequential(
            nn.Conv2d(fpn_channels, fpn_channels // 2, 3, padding=1),
            nn.ReLU(inplace=True),
            nn.Conv2d(fpn_channels // 2, 1, 1),
        )

        # Height head: dense per-pixel regression (raw, clamp post-hoc)
        self.height_head = nn.Sequential(
            nn.Conv2d(fpn_channels, fpn_channels // 2, 3, padding=1),
            nn.ReLU(inplace=True),
            nn.Conv2d(fpn_channels // 2, 1, 1),
        )

        # Shape head: masked global pool -> classifier
        self.shape_pool = nn.AdaptiveAvgPool2d(1)
        self.shape_head = nn.Sequential(
            nn.Flatten(),
            nn.Dropout(0.2),
            nn.Linear(fpn_channels, n_classes),
        )

    def _forward_once(
        self,
        rgb: "torch.Tensor",
        prev_mask: "torch.Tensor",
        prev_height: "torch.Tensor",
        footprint_mask: "torch.Tensor | None",
    ):
        """Single forward pass with a given prior.

        rgb         : B x 3 x H x W (ImageNet-normalised)
        prev_mask   : B x 1 x H x W in [0, 1]
        prev_height : B x 1 x H x W in metres (raw scale)
        Returns (mask_logits, height_map, shape_logits, feat).
        """
        prev_h_norm = prev_height / self.height_norm
        x = torch.cat([rgb, prev_mask, prev_h_norm], dim=1)
        input_size = x.shape[2:]

        feat = self.backbone(x)
        feat = self.fpn(feat, target_size=input_size)

        mask_logits = self.mask_head(feat)
        height_map = self.height_head(feat)

        shape_feat = feat
        if footprint_mask is not None:
            m = footprint_mask.float()
            if m.dim() == 3:
                m = m.unsqueeze(1)
            if m.shape[2:] != feat.shape[2:]:
                m = F.interpolate(m, size=feat.shape[2:], mode="nearest")
            shape_feat = shape_feat * m

        shape_feat = self.shape_pool(shape_feat)
        shape_logits = self.shape_head(shape_feat)
        return mask_logits, height_map, shape_logits

    def forward(
        self,
        rgb: "torch.Tensor",
        n_iters: int = 1,
        return_all: bool = False,
        footprint_mask: "torch.Tensor | None" = None,
    ):
        """Run iterative refinement.

        rgb     : B x 3 x H x W ImageNet-normalised
        n_iters : how many refinement passes (>=1).  First pass uses zero prior.
        return_all : if True return list of per-iteration outputs (used for
                     deep supervision / loss across all iterations during
                     training).  Otherwise return only the final tuple.

        Returns
        -------
        If return_all=False:
            (mask_logits, height_map, shape_logits)  -- final iteration.
        If return_all=True:
            list of (mask_logits, height_map, shape_logits) for each iteration.
        """
        B, _, H, W = rgb.shape
        device = rgb.device
        prev_mask = torch.zeros(B, 1, H, W, device=device, dtype=rgb.dtype)
        prev_height = torch.zeros(B, 1, H, W, device=device, dtype=rgb.dtype)

        outputs = []
        for it in range(max(1, n_iters)):
            mask_logits, height_map, shape_logits = self._forward_once(
                rgb, prev_mask, prev_height, footprint_mask,
            )
            outputs.append((mask_logits, height_map, shape_logits))

            # Prepare priors for next iteration (detach so loss flows only
            # through the current iteration -- avoids the unrolled-RNN-style
            # gradient explosion on small datasets).
            prev_mask = torch.sigmoid(mask_logits).detach()
            prev_height = torch.clamp(height_map, min=0.0).detach()

        if return_all:
            return outputs
        return outputs[-1]

    def predict(
        self,
        rgb: "torch.Tensor",
        n_iters: int = 2,
        gate_by_mask: bool = True,
        mask_threshold: float = 0.3,
    ):
        """Inference helper.  Returns (mask_prob, height) tensors.

        gate_by_mask : if True, multiply the height map by the mask
                       probability (or the binary mask above threshold) so
                       background pixels are zeroed out.
        """
        with torch.no_grad():
            mask_logits, height_map, _ = self.forward(rgb, n_iters=n_iters)
            mask_prob = torch.sigmoid(mask_logits)
            height = torch.clamp(height_map, min=0.0)
            if gate_by_mask:
                gate = mask_prob if mask_threshold is None else (mask_prob > mask_threshold).float()
                height = height * gate
        return mask_prob, height


# ---------------------------------------------------------------------------
# RoofNetV3_1 -- skip connections + Retna-style multi-scale fusion +
# scratchpad feedback channels + full-backprop iterative refinement
# ---------------------------------------------------------------------------

class _MobileNetSkipBackbone(nn.Module):
    """MobileNetV3-Small wrapper that exposes intermediate feature maps.

    Returns a dict of named features at strides (2, 8, 16, 32) -- the
    canonical skip points where MobileNetV3 has just downsampled.
    """

    SKIP_STAGES: tuple[int, ...] = (0, 2, 6, 12)
    SKIP_NAMES: tuple[str, ...] = ("s2", "s8", "s16", "s32")
    SKIP_CHANNELS: tuple[int, ...] = (16, 24, 40, 576)
    SKIP_STRIDES: tuple[int, ...] = (2, 8, 16, 32)

    def __init__(self, pretrained: bool = True, in_channels: int = 3):
        _require_torch()
        super().__init__()
        self.features = _build_mobilenet_backbone(
            pretrained=pretrained, in_channels=in_channels,
        )

    def forward(self, x: "torch.Tensor") -> "dict[str, torch.Tensor]":
        skips: dict[str, torch.Tensor] = {}
        for i, layer in enumerate(self.features):
            x = layer(x)
            if i in self.SKIP_STAGES:
                idx = self.SKIP_STAGES.index(i)
                skips[self.SKIP_NAMES[idx]] = x
        return skips


class _RetnaFusionHead(nn.Module):
    """Retna_V1-style multi-scale aggregation with input re-injection.

    Takes:
      - the FPN output (B x fpn_ch x H x W) at full resolution,
      - the four backbone skip features at strides {2, 8, 16, 32},
      - the original input (re-injected with the actual RGB channels only).

    Each skip is upsampled to (H, W) via bilinear and concatenated with the
    FPN output and the re-injected input.  A 1x1 conv at the end produces the
    requested number of output channels.  Because the raw input is in the
    final concat, edges remain available to the per-pixel classifier and
    blur from upsampling cannot wash them away.
    """

    def __init__(self, fpn_ch: int, skip_channels: tuple[int, ...],
                 in_rgb_ch: int, out_ch: int, mid_ch: int = 64):
        super().__init__()
        # Reduce each skip to mid_ch with a 1x1 conv before fusing
        self.lateral = nn.ModuleList([
            nn.Sequential(
                nn.Conv2d(c, mid_ch, 1),
                nn.BatchNorm2d(mid_ch),
                nn.ReLU(inplace=True),
            )
            for c in skip_channels
        ])
        total_concat = fpn_ch + mid_ch * len(skip_channels) + in_rgb_ch
        self.fuse = nn.Sequential(
            nn.Conv2d(total_concat, mid_ch * 2, 3, padding=1),
            nn.BatchNorm2d(mid_ch * 2),
            nn.ReLU(inplace=True),
        )
        self.head = nn.Conv2d(mid_ch * 2, out_ch, 1)

    def forward(
        self,
        fpn_feat: "torch.Tensor",
        skips: "dict[str, torch.Tensor]",
        rgb_input: "torch.Tensor",
    ) -> "torch.Tensor":
        H, W = fpn_feat.shape[2:]
        outs = [fpn_feat, rgb_input if rgb_input.shape[2:] == (H, W)
                else F.interpolate(rgb_input, size=(H, W), mode="bilinear", align_corners=False)]
        for lat, name in zip(self.lateral, ("s2", "s8", "s16", "s32")):
            s = lat(skips[name])
            if s.shape[2:] != (H, W):
                s = F.interpolate(s, size=(H, W), mode="bilinear", align_corners=False)
            outs.append(s)
        x = torch.cat(outs, dim=1)
        x = self.fuse(x)
        return self.head(x)


class RoofNetV3_1(nn.Module):
    """v3.1: skip connections + Retna fusion + scratchpad + full backprop.

    Improvements over v3:

    1. **Skip connections from MobileNetV3 stages 0/2/6/12** (strides 2/8/16/32)
       feed a Retna_V1-style multi-scale fusion head -- the raw RGB and all
       four backbone scales are concatenated before each output head's 1x1
       conv, so spatial detail is never bilinearly washed out.

    2. **Scratchpad feedback channels (K=4)** -- the model emits K extra
       feature maps each iteration that are passed as input to the next
       iteration alongside the mask/height priors.  No direct supervision;
       the model learns whatever intermediate state helps it refine.

    3. **Uncertainty channel** -- 4 * mask_prob * (1 - mask_prob), peaks
       where the previous mask was uncertain (mask=0.5).  Tells the
       refinement step where to focus.

    4. **Full-backprop iteration** -- gradients flow through all
       refinement iterations (no detach), so the loss signal teaches the
       early iterations to produce useful priors.

    Per-iteration input channels (10):
        3 RGB + 1 prev_mask + 1 prev_height_norm + 1 prev_uncertainty + 4 scratch
    """

    HEIGHT_NORM: float = 50.0
    SCRATCH_CHANNELS: int = 4

    def __init__(
        self,
        n_classes: int = 6,
        fpn_channels: int = 128,
        fusion_mid: int = 64,
        pretrained: bool = True,
        freeze_backbone: bool = False,
        height_norm: float = HEIGHT_NORM,
        scratch_channels: int = SCRATCH_CHANNELS,
    ) -> None:
        _require_torch()
        super().__init__()

        self.n_classes = n_classes
        self.height_norm = height_norm
        self.scratch_channels = scratch_channels
        self.in_channels = 3 + 1 + 1 + 1 + scratch_channels  # RGB + mask + h + unc + scratch

        self.backbone = _MobileNetSkipBackbone(
            pretrained=pretrained, in_channels=self.in_channels,
        )
        if freeze_backbone:
            for p in self.backbone.parameters():
                p.requires_grad = False

        # FPN takes the deepest feature (s32) and upsamples to input H, W
        self.fpn = _FPN(self.backbone.SKIP_CHANNELS[-1], fpn_channels)

        # Retna-style fusion heads, one per output type
        skip_chs = self.backbone.SKIP_CHANNELS
        # Input re-injection uses the RGB part only (3 channels), not the
        # full 10-channel input (priors would create a degenerate shortcut).
        self.mask_head = _RetnaFusionHead(fpn_channels, skip_chs, 3, 1, fusion_mid)
        self.height_head = _RetnaFusionHead(fpn_channels, skip_chs, 3, 1, fusion_mid)
        self.scratch_head = _RetnaFusionHead(
            fpn_channels, skip_chs, 3, scratch_channels, fusion_mid,
        )

        # Shape head: masked global pool over fused features
        self.shape_pool = nn.AdaptiveAvgPool2d(1)
        self.shape_head = nn.Sequential(
            nn.Flatten(),
            nn.Dropout(0.2),
            nn.Linear(fpn_channels, n_classes),
        )

    def _forward_once(
        self,
        rgb: "torch.Tensor",
        prev_mask: "torch.Tensor",
        prev_height: "torch.Tensor",
        prev_scratch: "torch.Tensor",
        footprint_mask: "torch.Tensor | None",
    ):
        """Single forward pass with full prior input."""
        prev_h_norm = prev_height / self.height_norm
        # Uncertainty peaks at mask=0.5 (was the model unsure?)
        prev_unc = 4.0 * prev_mask * (1.0 - prev_mask)

        x = torch.cat([rgb, prev_mask, prev_h_norm, prev_unc, prev_scratch], dim=1)

        skips = self.backbone(x)
        deep = skips[self.backbone.SKIP_NAMES[-1]]
        fpn_feat = self.fpn(deep, target_size=rgb.shape[2:])

        mask_logits = self.mask_head(fpn_feat, skips, rgb)
        height_map = self.height_head(fpn_feat, skips, rgb)
        scratch = self.scratch_head(fpn_feat, skips, rgb)

        # Shape head: pool the fpn features (gives a global building-style summary)
        shape_feat = fpn_feat
        if footprint_mask is not None:
            m = footprint_mask.float()
            if m.dim() == 3:
                m = m.unsqueeze(1)
            if m.shape[2:] != fpn_feat.shape[2:]:
                m = F.interpolate(m, size=fpn_feat.shape[2:], mode="nearest")
            shape_feat = shape_feat * m
        shape_feat = self.shape_pool(shape_feat)
        shape_logits = self.shape_head(shape_feat)

        return mask_logits, height_map, scratch, shape_logits

    def forward(
        self,
        rgb: "torch.Tensor",
        n_iters: int = 2,
        return_all: bool = False,
        footprint_mask: "torch.Tensor | None" = None,
    ):
        """Run iterative refinement with FULL backprop (no detach)."""
        B, _, H, W = rgb.shape
        device, dtype = rgb.device, rgb.dtype
        prev_mask = torch.zeros(B, 1, H, W, device=device, dtype=dtype)
        prev_height = torch.zeros(B, 1, H, W, device=device, dtype=dtype)
        prev_scratch = torch.zeros(
            B, self.scratch_channels, H, W, device=device, dtype=dtype,
        )

        outputs = []
        for it in range(max(1, n_iters)):
            mask_logits, height_map, scratch, shape_logits = self._forward_once(
                rgb, prev_mask, prev_height, prev_scratch, footprint_mask,
            )
            outputs.append((mask_logits, height_map, shape_logits))

            # Build the prior for the next iteration -- WITHOUT detach() so
            # gradients flow back through earlier iterations.
            prev_mask = torch.sigmoid(mask_logits)
            prev_height = torch.clamp(height_map, min=0.0)
            # Squash scratch to a bounded range so it doesn't explode across
            # many iterations.  tanh keeps values in [-1, 1].
            prev_scratch = torch.tanh(scratch)

        if return_all:
            return outputs
        return outputs[-1]

    def predict(
        self,
        rgb: "torch.Tensor",
        n_iters: int = 3,
        gate_by_mask: bool = True,
        mask_threshold: float = 0.3,
    ):
        with torch.no_grad():
            mask_logits, height_map, _ = self.forward(rgb, n_iters=n_iters)
            mask_prob = torch.sigmoid(mask_logits)
            height = torch.clamp(height_map, min=0.0)
            if gate_by_mask:
                gate = mask_prob if mask_threshold is None else (mask_prob > mask_threshold).float()
                height = height * gate
        return mask_prob, height


# ---------------------------------------------------------------------------
# RoofNetV3_S -- small/fast variant for low-data regimes
# ---------------------------------------------------------------------------

class RoofNetV3_S(nn.Module):
    """Compact iterative refinement model for height + mask prediction.

    Same iterative structure as RoofNetV3 (RGB + prev_mask + prev_height_norm
    feed back in on each pass) but with a much narrower FPN (32ch vs 128ch)
    and stronger dropout.  This gives ~200K trainable params vs 1.4M, which
    is much more appropriate when you have <500 training tiles.

    The backbone stays at MobileNetV3-Small (1.5M params, pretrained) but we
    freeze it for the first few epochs by default so the slim heads converge
    before the full gradient is applied.

    Architecture:
      MobileNetV3-Small (frozen initially) → FPN(32ch) → mask_head + height_head
      Iterative: prev_mask + prev_height_norm concatenated with RGB each pass.
    """

    HEIGHT_NORM: float = 50.0

    def __init__(
        self,
        n_classes: int = 6,
        fpn_channels: int = 32,
        pretrained: bool = True,
        freeze_backbone: bool = True,
        height_norm: float = HEIGHT_NORM,
        dropout: float = 0.35,
    ) -> None:
        _require_torch()
        super().__init__()

        self.n_classes = n_classes
        self.height_norm = height_norm
        self.in_channels = 5  # RGB + prev_mask + prev_height_norm

        self.backbone = _build_mobilenet_backbone(
            pretrained=pretrained, in_channels=self.in_channels,
        )
        backbone_ch = _mobilenet_out_channels()

        if freeze_backbone:
            for p in self.backbone.parameters():
                p.requires_grad = False

        self.fpn = _FPN(backbone_ch, fpn_channels)

        # Lightweight heads: single 3x3 conv + dropout + 1x1 output
        self.mask_head = nn.Sequential(
            nn.Conv2d(fpn_channels, fpn_channels, 3, padding=1),
            nn.BatchNorm2d(fpn_channels),
            nn.ReLU(inplace=True),
            nn.Dropout2d(dropout),
            nn.Conv2d(fpn_channels, 1, 1),
        )
        self.height_head = nn.Sequential(
            nn.Conv2d(fpn_channels, fpn_channels, 3, padding=1),
            nn.BatchNorm2d(fpn_channels),
            nn.ReLU(inplace=True),
            nn.Dropout2d(dropout),
            nn.Conv2d(fpn_channels, 1, 1),
        )

        # Shape head (global pool → linear, for ROOF-2 compat)
        self.shape_pool = nn.AdaptiveAvgPool2d(1)
        self.shape_head = nn.Sequential(
            nn.Flatten(),
            nn.Dropout(dropout),
            nn.Linear(fpn_channels, n_classes),
        )

    def _forward_once(self, rgb, prev_mask, prev_height, footprint_mask):
        prev_h_norm = prev_height / self.height_norm
        x = torch.cat([rgb, prev_mask, prev_h_norm], dim=1)
        input_size = x.shape[2:]

        feat = self.backbone(x)
        feat = self.fpn(feat, target_size=input_size)

        mask_logits = self.mask_head(feat)
        height_map = self.height_head(feat)

        shape_feat = feat
        if footprint_mask is not None:
            m = footprint_mask.float()
            if m.dim() == 3:
                m = m.unsqueeze(1)
            if m.shape[2:] != feat.shape[2:]:
                m = F.interpolate(m, size=feat.shape[2:], mode="nearest")
            shape_feat = shape_feat * m
        shape_feat = self.shape_pool(shape_feat)
        shape_logits = self.shape_head(shape_feat)
        return mask_logits, height_map, shape_logits

    def forward(
        self,
        rgb: "torch.Tensor",
        n_iters: int = 2,
        return_all: bool = False,
        footprint_mask: "torch.Tensor | None" = None,
    ):
        B, _, H, W = rgb.shape
        device, dtype = rgb.device, rgb.dtype
        prev_mask = torch.zeros(B, 1, H, W, device=device, dtype=dtype)
        prev_height = torch.zeros(B, 1, H, W, device=device, dtype=dtype)

        outputs = []
        for _ in range(max(1, n_iters)):
            mask_logits, height_map, shape_logits = self._forward_once(
                rgb, prev_mask, prev_height, footprint_mask,
            )
            outputs.append((mask_logits, height_map, shape_logits))
            prev_mask = torch.sigmoid(mask_logits).detach()
            prev_height = torch.clamp(height_map, min=0.0).detach()

        if return_all:
            return outputs
        return outputs[-1]

    def predict(
        self,
        rgb: "torch.Tensor",
        n_iters: int = 2,
        gate_by_mask: bool = True,
        mask_threshold: float = 0.3,
    ):
        with torch.no_grad():
            mask_logits, height_map, _ = self.forward(rgb, n_iters=n_iters)
            mask_prob = torch.sigmoid(mask_logits)
            height = torch.clamp(height_map, min=0.0)
            if gate_by_mask:
                gate = mask_prob if mask_threshold is None else (mask_prob > mask_threshold).float()
                height = height * gate
        return mask_prob, height

    def unfreeze_backbone(self):
        for p in self.backbone.parameters():
            p.requires_grad = True

    def freeze_backbone(self):
        for p in self.backbone.parameters():
            p.requires_grad = False


# ---------------------------------------------------------------------------
# HeightUNet  — simple U-Net baseline for height regression
# ---------------------------------------------------------------------------

class _UNetDecBlock(nn.Module):
    """Upsample + concat skip + 2× conv."""

    def __init__(self, in_ch: int, skip_ch: int, out_ch: int):
        super().__init__()
        self.up = nn.Sequential(
            nn.Conv2d(in_ch, out_ch, 3, padding=1),
            nn.BatchNorm2d(out_ch),
            nn.ReLU(inplace=True),
        )
        self.conv = nn.Sequential(
            nn.Conv2d(out_ch + skip_ch, out_ch, 3, padding=1),
            nn.BatchNorm2d(out_ch),
            nn.ReLU(inplace=True),
            nn.Conv2d(out_ch, out_ch, 3, padding=1),
            nn.BatchNorm2d(out_ch),
            nn.ReLU(inplace=True),
        )

    def forward(self, x, skip):
        x = self.up(x)
        x = F.interpolate(x, size=skip.shape[2:], mode="bilinear", align_corners=False)
        x = torch.cat([x, skip], dim=1)
        return self.conv(x)


class HeightUNet(nn.Module):
    """U-Net for height regression with a shared decoder and two output heads.

    Architecture:
      MobileNetV3-Small encoder (pretrained) with skip connections at s2/s8/s16/s32
      → shared U-Net decoder (dec_ch wide)
      → mask_head : (B,1,H,W) building/non-building logits  (BCE + Dice)
      → height_head: (B,1,H,W) raw height in metres         (weighted L1 + Sobel)

    The mask head provides a strong, easy segmentation signal — "is this a
    building pixel?" — that the network can learn from even when height labels
    are noisy.  At inference the height output is gated by the predicted mask
    (pixels where mask_prob < threshold → height 0).

    With <500 tiles keep dec_ch small (24–32) to avoid overfitting.
    Backbone is frozen by default for the first few epochs.
    """

    SKIP_CH: tuple[int, ...] = (16, 24, 40, 576)  # MobileNetV3-Small at s2/s8/s16/s32

    def __init__(
        self,
        pretrained: bool = True,
        freeze_backbone: bool = True,
        dropout: float = 0.25,
        dec_ch: int = 24,
    ) -> None:
        _require_torch()
        super().__init__()

        self.backbone = _MobileNetSkipBackbone(pretrained=pretrained)
        if freeze_backbone:
            for p in self.backbone.parameters():
                p.requires_grad = False

        ch = dec_ch
        sk = self.SKIP_CH
        # Shared decoder: s32(576) → s16(40) → s8(24) → s2(16) → full res
        self.dec32_16 = _UNetDecBlock(sk[3], sk[2], ch * 4)
        self.dec16_8  = _UNetDecBlock(ch * 4, sk[1], ch * 2)
        self.dec8_2   = _UNetDecBlock(ch * 2, sk[0], ch)
        self.final_up = nn.Sequential(
            nn.Conv2d(ch, ch, 3, padding=1),
            nn.BatchNorm2d(ch),
            nn.ReLU(inplace=True),
            nn.Dropout2d(dropout),
        )
        # Separate 1×1 heads — mask logits (raw) and height (activated by ReLU)
        self.mask_head   = nn.Conv2d(ch, 1, 1)
        self.height_head = nn.Conv2d(ch, 1, 1)

    def _decode(self, rgb: "torch.Tensor") -> "torch.Tensor":
        skips = self.backbone(rgb)
        x = self.dec32_16(skips["s32"], skips["s16"])
        x = self.dec16_8(x, skips["s8"])
        x = self.dec8_2(x, skips["s2"])
        x = F.interpolate(x, size=rgb.shape[2:], mode="bilinear", align_corners=False)
        return self.final_up(x)

    def forward(self, rgb: "torch.Tensor") -> "tuple[torch.Tensor, torch.Tensor]":
        """Return (mask_logits, height_map) — both (B, 1, H, W).

        mask_logits : raw (pre-sigmoid) building probability
        height_map  : non-negative height in metres (ReLU applied)
        """
        feat = self._decode(rgb)
        mask_logits = self.mask_head(feat)
        height_map  = F.relu(self.height_head(feat))
        return mask_logits, height_map

    def predict(
        self,
        rgb: "torch.Tensor",
        mask_threshold: float = 0.3,
    ) -> "tuple[torch.Tensor, torch.Tensor]":
        """Inference: return (mask_prob, gated_height)."""
        with torch.no_grad():
            mask_logits, height_map = self.forward(rgb)
            mask_prob = torch.sigmoid(mask_logits)
            height = height_map * (mask_prob > mask_threshold).float()
        return mask_prob, height

    def unfreeze_backbone(self):
        for p in self.backbone.parameters():
            p.requires_grad = True

    def freeze_backbone(self):
        for p in self.backbone.parameters():
            p.requires_grad = False


# ---------------------------------------------------------------------------
# Model factory
# ---------------------------------------------------------------------------

def build_model(
    task: str = "both",
    arch: str = "v2",
    n_classes: int = 6,
    pretrained: bool = True,
    freeze_backbone: bool = False,
    checkpoint: "str | None" = None,
    device: str = "cpu",
):
    """Build a RoofNetV2 or RoofNetV3 model, optionally loading a checkpoint.

    Parameters
    ----------
    task : str
        "shape", "height", or "both".
    arch : str
        "v2" (default, MobileNetV3 + dual heads) or
        "v3" (MobileNetV3 + iterative refinement + mask/height/shape heads).
    n_classes : int
        Number of shape classes.
    pretrained : bool
        Use ImageNet backbone weights.
    freeze_backbone : bool
        Freeze backbone initially.
    checkpoint : str | None
        Path to a saved checkpoint to resume from.
    device : str
        Target device.

    Returns
    -------
    RoofNetV2 or RoofNetV3 model on the specified device.
    """
    _require_torch()

    if arch in ("unet", "height_unet"):
        model = HeightUNet(
            pretrained=pretrained,
            freeze_backbone=freeze_backbone if freeze_backbone else True,
            dec_ch=24,
        )
    elif arch in ("v3.1", "v31"):
        model = RoofNetV3_1(
            n_classes=n_classes,
            pretrained=pretrained,
            freeze_backbone=freeze_backbone,
        )
    elif arch in ("v3s", "v3_s", "v3-s"):
        model = RoofNetV3_S(
            n_classes=n_classes,
            pretrained=pretrained,
            freeze_backbone=freeze_backbone if freeze_backbone else True,
        )
    elif arch == "v3":
        model = RoofNetV3(
            n_classes=n_classes,
            pretrained=pretrained,
            freeze_backbone=freeze_backbone,
        )
    else:
        model = RoofNetV2(
            n_classes=n_classes,
            pretrained=pretrained,
            freeze_backbone=freeze_backbone,
        )

    if checkpoint is not None:
        from pathlib import Path
        ckpt_path = Path(checkpoint)
        if ckpt_path.exists():
            state = torch.load(ckpt_path, map_location=device, weights_only=False)
            sd = state["model_state_dict"] if "model_state_dict" in state else state
            # Migrate old checkpoints: height_head.2.* -> height_head.3.*
            # (Dropout2d was inserted at index 2 in RoofNetV2/V3 height_head,
            #  shifting the final 1x1 conv from index 2 to index 3.)
            migrated = {}
            for k, v in sd.items():
                if k.startswith("height_head.2."):
                    new_key = "height_head.3." + k[len("height_head.2."):]
                    migrated[new_key] = v
                else:
                    migrated[k] = v
            sd = migrated
            missing, unexpected = model.load_state_dict(sd, strict=False)
            if missing or unexpected:
                logger.debug(
                    "load_state_dict: missing=%d unexpected=%d",
                    len(missing), len(unexpected),
                )
            if "model_state_dict" in state:
                logger.info("Loaded checkpoint from %s (epoch %d)", checkpoint, state.get("epoch", -1))
            else:
                logger.info("Loaded raw state_dict from %s", checkpoint)

    return model.to(torch.device(device))


# ---------------------------------------------------------------------------
# Legacy / Baseline architectures  (migrated from tools/example/networks.py)
# ---------------------------------------------------------------------------
# These are the original prototype architectures used to establish baselines
# before the production RoofNetV2/V3 family.  Retna_V1 is the 1k-parameter
# baseline used by train_retna.py, grow_prune.py, and inspect_retna.py.
# ---------------------------------------------------------------------------

def double_conv(in_channels: int, out_channels: int) -> "nn.Module":
    """Two 3×3 conv+LeakyReLU layers (UNet building block)."""
    _require_torch()
    return nn.Sequential(
        nn.Conv2d(in_channels, in_channels + out_channels, 3, padding=1),
        nn.LeakyReLU(inplace=True),
        nn.Conv2d(in_channels + out_channels, out_channels, 3, padding=1),
        nn.LeakyReLU(inplace=True),
    )


def res_conv(in_channels: int, out_channels: int) -> "nn.Module":
    """Strided 3×3 conv pair that halves spatial resolution (Retna_V1 block)."""
    _require_torch()
    return nn.Sequential(
        nn.Conv2d(in_channels, in_channels + out_channels, 3, padding=1, stride=1),
        nn.LeakyReLU(inplace=True),
        nn.Conv2d(in_channels + out_channels, out_channels, 3, padding=1, stride=2),
        nn.LeakyReLU(inplace=True),
    )


class UNet(nn.Module):
    """Lightweight U-Net (8-channel feature maps) for binary segmentation."""

    def __init__(self, in_channels: int, out_classes: int) -> None:
        _require_torch()
        super().__init__()
        num_chan = [8, 8, 8, 8]
        self.dconv_down1 = double_conv(in_channels, num_chan[0])
        self.dconv_down2 = double_conv(num_chan[0], num_chan[1])
        self.dconv_down3 = double_conv(num_chan[1], num_chan[2])
        self.dconv_down4 = double_conv(num_chan[2], num_chan[3])
        self.maxpool = nn.MaxPool2d(2)
        self.upsample = nn.Upsample(scale_factor=2, mode="bilinear", align_corners=True)
        self.dconv_up3 = double_conv(num_chan[2] + num_chan[3], num_chan[2])
        self.dconv_up2 = double_conv(num_chan[1] + num_chan[2], num_chan[1])
        self.dconv_up1 = double_conv(num_chan[0] + num_chan[1], num_chan[0])
        self.conv_last = nn.Conv2d(num_chan[0], out_classes, 1)

    def forward(self, x: "torch.Tensor") -> "torch.Tensor":
        conv1 = self.dconv_down1(x)
        x = self.maxpool(conv1)
        conv2 = self.dconv_down2(x)
        x = self.maxpool(conv2)
        conv3 = self.dconv_down3(x)
        x = self.maxpool(conv3)
        x = self.dconv_down4(x)
        x = self.upsample(x)
        x = torch.cat([x, conv3], dim=1)
        x = self.dconv_up3(x)
        x = self.upsample(x)
        x = torch.cat([x, conv2], dim=1)
        x = self.dconv_up2(x)
        x = self.upsample(x)
        x = torch.cat([x, conv1], dim=1)
        x = self.dconv_up1(x)
        return self.conv_last(x)


class Retna_V1(nn.Module):
    """Minimal ~1k-parameter iterative multi-scale network for height regression.

    Used as the baseline model for grow/prune experiments.  Each block
    downsamples spatially by 2×; all scale outputs are upsampled back to the
    input resolution and concatenated before the final 1×1 conv.
    The original input is re-injected at every scale.
    """

    def __init__(
        self,
        in_channels: int,
        out_classes: int,
        hidden_channels: "list[int] | None" = None,
    ) -> None:
        _require_torch()
        super().__init__()
        if hidden_channels is None:
            hidden_channels = [8, 8, 8, 8]
        hidden_channels = list(hidden_channels)
        hidden_channels.insert(0, in_channels)
        blocks = []
        for i in range(len(hidden_channels) - 1):
            in_ch = hidden_channels[i] if i == 0 else hidden_channels[i] + in_channels
            blocks.append(res_conv(in_ch, hidden_channels[i + 1]))
        self.blocks = nn.ModuleList(blocks)
        self.conv_last = nn.Conv2d(sum(hidden_channels), out_classes, 1)

    def forward(self, x_in: "torch.Tensor") -> "torch.Tensor":
        x_next = x_in
        out_list = [x_in]
        for block in self.blocks:
            x_next = block(x_next)
            x_out = F.interpolate(x_next, size=x_in.shape[2:], mode="bilinear", align_corners=False)
            x_in_down = F.interpolate(x_in, size=x_next.shape[2:], mode="bilinear", align_corners=False)
            x_next = torch.cat([x_in_down, x_next], dim=1)
            out_list.append(x_out)
        x = torch.cat(out_list, dim=1)
        out = self.conv_last(x)
        if self.training:
            out = F.leaky_relu(out)
            out = 1 - F.leaky_relu(1 - out)
        else:
            out = torch.clamp(out, 0, 1)
        return out


def _valid_groups(channels: int, preferred: int = 8) -> int:
    """Return the largest divisor of *channels* that is ≤ *preferred*."""
    for g in range(min(preferred, channels), 0, -1):
        if channels % g == 0:
            return g
    return 1


def add_coord_channels(x: "torch.Tensor") -> "torch.Tensor":
    """Append (y, x) coordinate grids normalised to [-1, 1] as extra channels."""
    _require_torch()
    B, _, H, W = x.shape
    ys = torch.linspace(-1.0, 1.0, H, device=x.device).view(1, 1, H, 1).expand(B, 1, H, W)
    xs = torch.linspace(-1.0, 1.0, W, device=x.device).view(1, 1, 1, W).expand(B, 1, H, W)
    return torch.cat([x, ys, xs], dim=1)


class ResBlock(nn.Module):
    """Strided residual block with GroupNorm (Retna_V2 building block)."""

    def __init__(self, in_ch: int, out_ch: int, groups: int = 8) -> None:
        _require_torch()
        super().__init__()
        mid = in_ch + out_ch
        g_mid = _valid_groups(mid, groups)
        g_out = _valid_groups(out_ch, groups)
        self.body = nn.Sequential(
            nn.Conv2d(in_ch, mid, 3, padding=1),
            nn.GroupNorm(g_mid, mid),
            nn.LeakyReLU(inplace=True),
            nn.Conv2d(mid, out_ch, 3, padding=1, stride=2),
            nn.GroupNorm(g_out, out_ch),
        )
        self.shortcut = nn.Sequential(
            nn.Conv2d(in_ch, out_ch, 1, stride=2),
            nn.GroupNorm(g_out, out_ch),
        )

    def forward(self, x: "torch.Tensor") -> "torch.Tensor":
        return F.leaky_relu(self.body(x) + self.shortcut(x), inplace=True)


class Retna_V2(nn.Module):
    """Improved multi-scale iterative aggregation network with GroupNorm + CoordConv."""

    def __init__(
        self,
        in_channels: int,
        out_classes: int,
        hidden_channels: "list[int] | None" = None,
        coord_conv: bool = True,
    ) -> None:
        _require_torch()
        super().__init__()
        if hidden_channels is None:
            hidden_channels = [32, 64, 128, 256]
        self.coord_conv = coord_conv
        effective_in = in_channels + 2 if coord_conv else in_channels
        hidden = list(hidden_channels)
        hidden.insert(0, effective_in)
        self._effective_in = effective_in
        self._hidden = hidden
        blocks = []
        for i in range(len(hidden) - 1):
            in_ch = hidden[i] if i == 0 else hidden[i] + effective_in
            blocks.append(ResBlock(in_ch, hidden[i + 1]))
        self.blocks = nn.ModuleList(blocks)
        self._sum_ch = sum(hidden)
        self.conv_last = nn.Conv2d(self._sum_ch, out_classes, 1)

    def forward(self, x: "torch.Tensor") -> "torch.Tensor":
        if self.coord_conv:
            x = add_coord_channels(x)
        x_in = x
        x_next = x_in
        out_list = [x_in]
        for block in self.blocks:
            x_next = block(x_next)
            x_out = F.interpolate(x_next, size=x_in.shape[2:], mode="bilinear", align_corners=False)
            x_in_down = F.interpolate(x_in, size=x_next.shape[2:], mode="bilinear", align_corners=False)
            x_next = torch.cat([x_in_down, x_next], dim=1)
            out_list.append(x_out)
        return self.conv_last(torch.cat(out_list, dim=1))


class RoofNet(nn.Module):
    """Multi-task roof analysis network (Retna_V2 backbone, height + shape heads).

    Legacy version superseded by RoofNetV2/V3.
    """

    def __init__(
        self,
        in_channels: int = 3,
        n_classes: int = 6,
        hidden_channels: "list[int] | None" = None,
        coord_conv: bool = True,
    ) -> None:
        _require_torch()
        super().__init__()
        if hidden_channels is None:
            hidden_channels = [32, 64, 128, 256]
        self.coord_conv = coord_conv
        effective_in = in_channels + 2 if coord_conv else in_channels
        hidden = list(hidden_channels)
        hidden.insert(0, effective_in)
        self._effective_in = effective_in
        self._backbone_ch = sum(hidden)
        blocks = []
        for i in range(len(hidden) - 1):
            in_ch = hidden[i] if i == 0 else hidden[i] + effective_in
            blocks.append(ResBlock(in_ch, hidden[i + 1]))
        self.blocks = nn.ModuleList(blocks)
        self.height_head = nn.Conv2d(self._backbone_ch, 1, 1)
        self.shape_head = nn.Sequential(
            nn.AdaptiveAvgPool2d(1),
            nn.Flatten(),
            nn.Linear(self._backbone_ch, n_classes),
        )

    def _backbone_features(self, x: "torch.Tensor") -> "torch.Tensor":
        if self.coord_conv:
            x = add_coord_channels(x)
        x_in = x
        x_next = x_in
        out_list = [x_in]
        for block in self.blocks:
            x_next = block(x_next)
            x_out = F.interpolate(x_next, size=x_in.shape[2:], mode="bilinear", align_corners=False)
            x_in_down = F.interpolate(x_in, size=x_next.shape[2:], mode="bilinear", align_corners=False)
            x_next = torch.cat([x_in_down, x_next], dim=1)
            out_list.append(x_out)
        return torch.cat(out_list, dim=1)

    def forward(
        self,
        x: "torch.Tensor",
        mask: "torch.Tensor | None" = None,
    ) -> "tuple[torch.Tensor, torch.Tensor]":
        feat = self._backbone_features(x)
        height_map = self.height_head(feat)
        if mask is not None:
            m = mask.float()
            if m.dim() == 3:
                m = m.unsqueeze(1)
            feat = feat * m
        shape_logits = self.shape_head(feat)
        return height_map, shape_logits
