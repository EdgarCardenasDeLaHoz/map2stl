import os
import torch
import torch.nn as nn
import numpy as np
import torch.nn.functional as F
import cv2


class UNet(nn.Module):

    def __init__(self, in_channels, out_classes):
        super().__init__()

        num_chan = [8, 8, 8, 8]
        self.dconv_down1 = double_conv(in_channels, num_chan[0])
        self.dconv_down2 = double_conv(num_chan[0], num_chan[1])
        self.dconv_down3 = double_conv(num_chan[1], num_chan[2])
        self.dconv_down4 = double_conv(num_chan[2], num_chan[3])

        self.maxpool = nn.MaxPool2d(2)
        self.upsample = nn.Upsample(
            scale_factor=2, mode='bilinear', align_corners=True)

        self.dconv_up3 = double_conv(num_chan[2] + num_chan[3], num_chan[2])
        self.dconv_up2 = double_conv(num_chan[1] + num_chan[2], num_chan[1])
        self.dconv_up1 = double_conv(num_chan[0] + num_chan[1], num_chan[0])
        self.conv_last = nn.Conv2d(num_chan[0], out_classes, 1)

    def forward(self, x):

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
        out = self.conv_last(x)

        return out

    def train_model(self, data_loader, optimizer=None, num_epochs=10):

        train_model(self, data_loader, optimizer, num_epochs)


class Retna_V1(nn.Module):

    def __init__(self, in_channels, out_classes, hidden_channels=None):
        super().__init__()

        if hidden_channels is None:
            hidden_channels = [8, 8, 8, 8]

        hidden_channels = list(hidden_channels)  # copy to avoid mutating caller's list
        hidden_channels.insert(0, in_channels)

        Mod_list = []
        for i in range(len(hidden_channels)-1):
            if i == 0:
                hid_chan1 = hidden_channels[i]
            else:
                hid_chan1 = hidden_channels[i]+in_channels
            hid_chan2 = hidden_channels[i+1]
            block = res_conv(hid_chan1, hid_chan2)
            Mod_list.append(block)

        self.blocks = nn.ModuleList(Mod_list)
        self.maxpool = nn.AvgPool2d(2)
        self.conv_last = nn.Conv2d(sum(hidden_channels), out_classes, 1)

    def forward(self, x_in):

        x_next = x_in

        out_list = [x_in]
        for n, block in enumerate(self.blocks):

            x_next = block(x_next)
            x_out = F.interpolate(
                x_next, size=x_in.shape[2:], mode='bilinear', align_corners=False)
            x_in2 = F.interpolate(
                x_in, size=x_next.shape[2:], mode='bilinear', align_corners=False)
            x_next = torch.cat([x_in2, x_next], dim=1)

            out_list.append(x_out)

        x = torch.cat(out_list, dim=1)
        out = self.conv_last(x)

        if self.training:
            out = F.leaky_relu(out)
            out = 1-F.leaky_relu(1-out)
        else:
            out = torch.clamp(out, 0, 1)

        return out

    def train_model(self, data_loader, optimizer=None, num_epochs=10):
        train_model(self, data_loader, optimizer, num_epochs)

    def eval_file(self, filename):
        return eval_file(self, filename)

    def segment(self, image, scale=1):
        return segment(self, image, scale=scale)

    def save(self, name, path=".\\models\\"):
        if not os.path.isdir(path):
            os.mkdir(path)
        fn = path + name + ".pt"
        torch.save(self, fn)

    def saveto(self, name):
        torch.save(self, name)


#####################################################
##              BASE Functions                     ##
#####################################################

def eval_file(model, filename):

    X1, X2, Y1, Y2 = 148, 1852, 398, 2022
    in_im = np.array(cv2.imread(filename))
    out_im = np.zeros((in_im.shape[0], in_im.shape[1], 1))
    t_im = in_im[X1:X2, Y1:Y2, 0:1]/256

    t_im = np.transpose(t_im, [2, 0, 1])
    t_im = torch.tensor(t_im).float()[None, :]

    model.eval()
    with torch.no_grad():
        output = model(t_im)

    output = output.detach().numpy()[0, 0]
    out_im[X1:X2, Y1:Y2, 0] = output

    return in_im, out_im


def segment(model, image, scale=1):

    if image.ndim == 4 and len(image) == 1:
        image = image[0]

    if image.ndim == 2:
        image = image[..., None]

    if image.shape[0] > image.shape[-1]:
        image = np.transpose(image, [2, 0, 1])

    if isinstance(image, torch.Tensor):
        image = image.cpu().detach().numpy()

    if image.dtype == np.uint8:
        image = image / 255.0
    if image.max() > 20:
        image = image / 255.0

    sz = np.array(image.shape[1:3])
    image = resize_to_base(image, scale)

    image = torch.tensor(image).float()[None, :]
    if next(model.parameters()).is_cuda:
        image = image.to("cuda")

    model.eval()
    with torch.no_grad():
        output = model(image)

    output = output.detach().cpu().numpy()[0]

    output = resize_to_base(output, 1, base=1, size_out=sz)

    return output


def res_conv(in_channels, out_channels):
    return nn.Sequential(
        nn.Conv2d(in_channels, in_channels +
                  out_channels, 3, padding=1, stride=1),
        nn.LeakyReLU(inplace=True),
        nn.Conv2d(in_channels+out_channels,
                  out_channels, 3, padding=1, stride=2),
        nn.LeakyReLU(inplace=True)
    )


def double_conv(in_channels, out_channels):
    return nn.Sequential(
        nn.Conv2d(in_channels, in_channels + out_channels, 3, padding=1),
        nn.LeakyReLU(inplace=True),
        nn.Conv2d(in_channels + out_channels, out_channels, 3, padding=1),
        nn.LeakyReLU(inplace=True)
    )


# ─────────────────────────────────────────────────────────────────────────────
# Retna_V2 building blocks
# ─────────────────────────────────────────────────────────────────────────────

def _valid_groups(channels: int, preferred: int = 8) -> int:
    """Return the largest divisor of *channels* that is ≤ *preferred*."""
    for g in range(min(preferred, channels), 0, -1):
        if channels % g == 0:
            return g
    return 1


def add_coord_channels(x: "torch.Tensor") -> "torch.Tensor":
    """Append (y, x) coordinate grids normalised to [-1, 1] as extra channels.

    CoordConv gives the network a free spatial prior so it can learn
    position-dependent responses without needing to infer location from
    contextual features alone.

    Parameters
    ----------
    x : Tensor, shape B × C × H × W

    Returns
    -------
    Tensor, shape B × (C+2) × H × W
    """
    B, _, H, W = x.shape
    ys = torch.linspace(-1.0, 1.0, H, device=x.device).view(1, 1, H, 1).expand(B, 1, H, W)
    xs = torch.linspace(-1.0, 1.0, W, device=x.device).view(1, 1, 1, W).expand(B, 1, H, W)
    return torch.cat([x, ys, xs], dim=1)


class ResBlock(nn.Module):
    """Strided residual block with GroupNorm.

    Downsamples spatially by 2 (stride=2 in the 3×3 conv and a 1×1 shortcut).
    GroupNorm replaces BatchNorm so batch size 1 works at inference time.

    Parameters
    ----------
    in_ch : int    — input channels
    out_ch : int   — output channels (after downsampling)
    groups : int   — preferred group count for GroupNorm (clamped to a valid divisor)
    """

    def __init__(self, in_ch: int, out_ch: int, groups: int = 8) -> None:
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
    """Improved multi-scale iterative aggregation network.

    Fixes Retna_V1 by:
      - replacing the plain Sequential ``res_conv`` with ``ResBlock`` (true residuals)
      - adding GroupNorm in every conv block
      - removing the dead ``self.maxpool = AvgPool2d(2)`` attribute
      - returning raw logits from ``forward`` (no baked-in clamping)
      - widening default hidden channels to [32, 64, 128, 256]
      - optional CoordConv (+2 input channels)

    Architecture
    ------------
    Input ``x_in`` (B × C_eff × H × W) is re-injected at every scale after the
    previous feature map is downsampled, giving the network a persistent full-
    resolution reference at each stage.  All per-stage outputs are upsampled
    back to the input resolution and concatenated before the final 1×1 conv.

    Parameters
    ----------
    in_channels : int
        Number of input channels (3 for RGB).
    out_classes : int
        Number of output channels (1 for height regression, N for segmentation).
    hidden_channels : list[int] | None
        Feature widths at each scale.  Default ``[32, 64, 128, 256]``.
    coord_conv : bool
        If True, append (y, x) coordinate channels to the input (+2 channels).
    """

    def __init__(
        self,
        in_channels: int,
        out_classes: int,
        hidden_channels: "list[int] | None" = None,
        coord_conv: bool = True,
    ) -> None:
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
            out_ch = hidden[i + 1]
            blocks.append(ResBlock(in_ch, out_ch))

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
            x_out = F.interpolate(
                x_next, size=x_in.shape[2:], mode="bilinear", align_corners=False
            )
            x_in_down = F.interpolate(
                x_in, size=x_next.shape[2:], mode="bilinear", align_corners=False
            )
            x_next = torch.cat([x_in_down, x_next], dim=1)
            out_list.append(x_out)

        x_cat = torch.cat(out_list, dim=1)
        return self.conv_last(x_cat)  # raw logits — no clamping

    def segment(self, image, scale: float = 1) -> "np.ndarray":
        return segment(self, image, scale=scale)

    def save(self, name: str, path: str = ".\\models\\") -> None:
        if not os.path.isdir(path):
            os.mkdir(path)
        torch.save(self, path + name + ".pt")

    def saveto(self, name: str) -> None:
        torch.save(self, name)


# Number of roof-shape classes (must match _SHAPE_LABELS in roof_classifier.py)
_N_SHAPE_CLASSES: int = 6  # flat, gabled, hipped, pyramidal, skillion, dome


class RoofNet(nn.Module):
    """Multi-task roof analysis network.

    Shared Retna_V2 backbone with two heads:

    * **height_head** — dense H×W height-regression head (pseudo-nDSM).
      Output: B × 1 × H × W, raw float (apply ReLU or clamp post-hoc).

    * **shape_head** — masked-pool roof-shape classification head.
      Output: B × n_classes logits.
      When a ``mask`` is supplied the backbone feature map is element-wise
      multiplied by the mask before global average pooling, so the classifier
      attends only to pixels inside the building footprint.

    Parameters
    ----------
    in_channels : int
        Input image channels.  Default 3 (RGB).
    n_classes : int
        Number of roof-shape classes.  Default 6.
    hidden_channels : list[int] | None
        Backbone feature widths.  Default ``[32, 64, 128, 256]``.
    coord_conv : bool
        Append coordinate channels to the input.  Default True.
    """

    def __init__(
        self,
        in_channels: int = 3,
        n_classes: int = _N_SHAPE_CLASSES,
        hidden_channels: "list[int] | None" = None,
        coord_conv: bool = True,
    ) -> None:
        super().__init__()

        if hidden_channels is None:
            hidden_channels = [32, 64, 128, 256]

        self.coord_conv = coord_conv
        effective_in = in_channels + 2 if coord_conv else in_channels

        hidden = list(hidden_channels)
        hidden.insert(0, effective_in)
        self._effective_in = effective_in
        self._hidden = hidden
        self._backbone_ch = sum(hidden)

        blocks = []
        for i in range(len(hidden) - 1):
            in_ch = hidden[i] if i == 0 else hidden[i] + effective_in
            out_ch = hidden[i + 1]
            blocks.append(ResBlock(in_ch, out_ch))

        self.blocks = nn.ModuleList(blocks)
        self.height_head = nn.Conv2d(self._backbone_ch, 1, 1)
        self.shape_head = nn.Sequential(
            nn.AdaptiveAvgPool2d(1),
            nn.Flatten(),
            nn.Linear(self._backbone_ch, n_classes),
        )

    def _backbone_features(self, x: "torch.Tensor") -> "torch.Tensor":
        """Run multi-scale aggregation; return concatenated feature map B×C×H×W."""
        if self.coord_conv:
            x = add_coord_channels(x)

        x_in = x
        x_next = x_in
        out_list = [x_in]

        for block in self.blocks:
            x_next = block(x_next)
            x_out = F.interpolate(
                x_next, size=x_in.shape[2:], mode="bilinear", align_corners=False
            )
            x_in_down = F.interpolate(
                x_in, size=x_next.shape[2:], mode="bilinear", align_corners=False
            )
            x_next = torch.cat([x_in_down, x_next], dim=1)
            out_list.append(x_out)

        return torch.cat(out_list, dim=1)

    def forward(
        self,
        x: "torch.Tensor",
        mask: "torch.Tensor | None" = None,
    ) -> "tuple[torch.Tensor, torch.Tensor]":
        """Run both heads.

        Parameters
        ----------
        x : Tensor, B × C × H × W
        mask : Tensor (optional), B × H × W or B × 1 × H × W
            Boolean/float footprint mask.  Applied to features before shape pooling.

        Returns
        -------
        height_map : Tensor B × 1 × H × W  (raw; apply clamp/relu post-hoc)
        shape_logits : Tensor B × n_classes (raw logits; apply softmax post-hoc)
        """
        feat = self._backbone_features(x)

        height_map = self.height_head(feat)

        if mask is not None:
            m = mask.float()
            if m.dim() == 3:
                m = m.unsqueeze(1)
            feat = feat * m

        shape_logits = self.shape_head(feat)
        return height_map, shape_logits

    def segment(self, image, scale: float = 1) -> "np.ndarray":
        return segment(self, image, scale=scale)

    def save(self, name: str, path: str = ".\\models\\") -> None:
        if not os.path.isdir(path):
            os.mkdir(path)
        torch.save(self, path + name + ".pt")

    def saveto(self, name: str) -> None:
        torch.save(self, name)


def lims_normalize(x):

    # chanMin,_ = x.reshape((*x.shape[0:2], -1)).min(dim=2)
    # x = x - chanMin[:,:,None,None]
    # chanMax,_ = x.reshape((*x.shape[0:2], -1)).max(dim=2)
    # chanMax = torch.clamp(chanMax, 1, 1000)
    # x = x / ( chanMax[:,:,None,None] + 0.001)
    return x


def resize_to_base(im_in, reduction, base=8, size_out=None):

    if size_out is None:
        sz_in = np.array(im_in.shape[1:3])[::-1]
        sz_out = sz_in * reduction
        sz_out = (np.round(sz_out/base)*base).astype(int)
    else:
        sz_out = size_out[::-1].astype(int)

    im_out = np.stack([cv2.resize(1.*im, tuple(sz_out)) for im in im_in])
    return im_out


