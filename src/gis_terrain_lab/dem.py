"""Sample a local elevation raster and encode unsigned 16-bit heights."""

import math

import numpy as np
import rasterio
from rasterio.warp import Resampling, reproject

from .grid import Grid


def sample_dem(path, target=None):
    with rasterio.open(path, driver="GTiff") as src:
        if not src.crs or src.count != 1 or np.dtype(src.dtypes[0]).kind not in "iuf":
            raise ValueError("The DEM must have one real-valued band and a horizontal CRS.")
        unit = (src.units[0] or "").strip().lower()
        if unit not in {"", "m", "metre", "metres", "meter", "meters"}:
            raise ValueError(f"Convert vertical units {unit!r} to metres before packing.")
        if target is None:
            t = src.transform
            if t.b != 0 or t.d != 0 or t.a <= 0 or t.e >= 0:
                raise ValueError("Supply a target grid for rotated, skewed or inverted rasters.")
            target_grid = Grid(src.crs, tuple(src.bounds), src.width, src.height)
        else:
            target_grid = target

        band = src.read(1, masked=True, out_dtype="float64").filled(np.nan)
        band *= src.scales[0]
        band += src.offsets[0]
        if target is not None:
            band[~np.isfinite(band)] = np.nan
            sampled = np.empty((target.height, target.width), dtype="float64")
            reproject(
                band,
                sampled,
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=target.transform,
                dst_crs=target.crs,
                src_nodata=np.nan,
                dst_nodata=np.nan,
                resampling=Resampling.bilinear,
            )
            band = sampled
    invalid = int(np.count_nonzero(~np.isfinite(band)))
    if invalid:
        raise ValueError(f"Target grid contains {invalid} missing elevation samples.")
    return target_grid, band


def elevation_range(low, high):
    if not (math.isfinite(low) and math.isfinite(high - low)) or high < low:
        raise ValueError("Elevation endpoints must form a finite, ordered range.")


def encode(values, low, high):
    """Quantize in metres. A constant terrain uses a zero-valued heightfield."""
    elevation_range(low, high)
    if not np.isfinite(values).all() or values.min() < low or values.max() > high:
        raise ValueError("Every elevation must be finite and inside the selected range.")
    if low == high:
        return np.zeros(values.shape, dtype="<u2")
    fraction = (values.astype("float64") - low) / (high - low)
    return np.rint(fraction * 65535).astype("<u2")


def decode(raw, low, high):
    elevation_range(low, high)
    if low == high and np.any(raw):
        raise ValueError("A constant elevation range requires zero-valued samples.")
    return low + (raw.astype("float64") / 65535) * (high - low)
