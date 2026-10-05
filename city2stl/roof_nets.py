"""
city2stl.roof_nets - RoofNetV2 architecture, importable without ``tools/``.

Inference in :mod:`city2stl.roof_classifier` loads RoofNetV2 checkpoints, and a
library package must not import from the ``tools`` tree.  The definition lives
here; :mod:`tools.ml.models` re-exports it (with the backbone and FPN blocks)
so training code is unchanged.

Importing this module imports ``torch``; callers should import it lazily.
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F

_TORCH_AVAILABLE = True


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
) -> nn.Module:
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
    from torchvision.models import MobileNet_V3_Small_Weights, mobilenet_v3_small

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

    def forward(self, x: torch.Tensor, target_size: tuple[int, int]) -> torch.Tensor:
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
        x: torch.Tensor,
        mask: torch.Tensor | None = None,
    ) -> tuple[torch.Tensor, torch.Tensor]:
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

    def predict_shape(self, x: torch.Tensor, mask: torch.Tensor | None = None) -> torch.Tensor:
        """Shape classification only (no height computation)."""
        _, logits = self.forward(x, mask)
        return logits

    def predict_height(self, x: torch.Tensor) -> torch.Tensor:
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
