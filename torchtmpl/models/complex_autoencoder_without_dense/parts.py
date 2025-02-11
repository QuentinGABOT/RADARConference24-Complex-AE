""" Parts of the AutoEncoder model """

import torch
import torch.nn as nn
import torch.nn.functional as F
import torchcvnn.nn.modules as c_nn
from math import prod


class DoubleConv(nn.Module):
    """(convolution => [BN] => ReLU) * 2"""

    def __init__(
        self, in_channels, out_channels, activation, stride=1, mid_channels=None
    ):
        super().__init__()
        if not mid_channels:
            mid_channels = out_channels
        self.double_conv = nn.Sequential(
            nn.Conv2d(
                in_channels,
                mid_channels,
                kernel_size=3,
                stride=stride,
                padding=1,
                bias=False,
                padding_mode="replicate",
                dtype=torch.complex64,
            ),
            c_nn.BatchNorm2d(mid_channels),
            activation,
            nn.Conv2d(
                mid_channels,
                out_channels,
                kernel_size=3,
                stride=1,
                padding=1,
                bias=False,
                padding_mode="replicate",
                dtype=torch.complex64,
            ),
            c_nn.BatchNorm2d(out_channels),
            activation,
        )

    def forward(self, x):
        return self.double_conv(x)


class Down(nn.Module):
    """Downscaling with maxpool then double conv"""

    def __init__(self, in_channels, out_channels, activation):
        super().__init__()
        self.maxpool_conv = nn.Sequential(
            DoubleConv(
                in_channels,
                out_channels,
                activation,
                stride=2,
            ),
        )

    def forward(self, x):
        return self.maxpool_conv(x)


class Up(nn.Module):
    """Upscaling then double conv"""

    def __init__(self, in_channels, out_channels, activation):
        super().__init__()
        self.up = c_nn.ConvTranspose2d(
            in_channels, out_channels, kernel_size=2, stride=2
        )
        self.conv = DoubleConv(out_channels, out_channels, activation)

    def forward(self, x):
        x = self.up(x)
        return self.conv(x)


class OutConv(nn.Module):
    def __init__(self, in_channels, out_channels):
        super(OutConv, self).__init__()
        self.conv = nn.Sequential(
            nn.Conv2d(in_channels, out_channels, kernel_size=1, dtype=torch.complex64),
        )

    def forward(self, x):
        return self.conv(x)
