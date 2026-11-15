"""Write and verify the version 1 heightfield exchange directory."""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS

from .dem import decode, elevation_range, encode, sample_dem
from .grid import Grid, is_number
from .roads import road_mask

HEIGHT = "elevation.r16"
ROADS = "roads.r8"
MANIFEST = "terrain.json"
FORMAT = "gis-terrain-lab/1"


def _digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def pack(dem, destination, *, target=None, roads=None, levels=None):
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"Output already exists: {destination}")
    grid, values = sample_dem(dem, target)
    low, high = levels if levels is not None else (float(values.min()), float(values.max()))
    encoded = encode(values, low, high)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as scratch:
        stage = Path(scratch) / "tile"
        stage.mkdir()
        encoded.tofile(stage / HEIGHT)
        hashes = {HEIGHT: _digest(stage / HEIGHT)}
        if roads is not None:
            road_mask(roads, grid).tofile(stage / ROADS)
            hashes[ROADS] = _digest(stage / ROADS)
        manifest = {
            "format": FORMAT,
            "crs": grid.crs.to_string(),
            "bounds": list(grid.bounds),
            "width": grid.width,
            "height": grid.height,
            "elevation_m": [low, high],
            "sha256": hashes,
        }
        (stage / MANIFEST).write_text(
            json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8", newline="\n"
        )
        validate(stage)
        stage.rename(destination)


def validate(directory):
    """Check metadata, fixed filenames, byte counts and SHA-256 payload digests."""
    directory = Path(directory)
    metadata = directory / MANIFEST
    if metadata.is_symlink() or not metadata.is_file():
        raise ValueError(f"Expected a regular {MANIFEST} file.")
    data = json.loads(metadata.read_text(encoding="utf-8"))
    fields = {"format", "height", "width", "bounds", "crs", "elevation_m", "sha256"}
    if not isinstance(data, dict) or set(data) != fields or data["format"] != FORMAT:
        raise ValueError("Unrecognized terrain manifest or fields.")
    if not isinstance(data["crs"], str) or not isinstance(data["bounds"], list):
        raise ValueError("Expected a CRS string and an array of bounds.")
    grid = Grid(CRS.from_string(data["crs"]), tuple(data["bounds"]), data["width"], data["height"])
    levels = data["elevation_m"]
    if not isinstance(levels, list) or len(levels) != 2 or not all(map(is_number, levels)):
        raise ValueError("Expected two finite elevation endpoints.")
    elevation_range(*levels)
    hashes = data["sha256"]
    if not isinstance(hashes, dict) or set(hashes) not in ({HEIGHT}, {HEIGHT, ROADS}):
        raise ValueError("Unexpected payload list.")
    if {entry.name for entry in directory.iterdir()} != {MANIFEST, *hashes}:
        raise ValueError("Directory contents differ from the manifest.")
    for name, digest in hashes.items():
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Expected a regular file for {name}.")
        expected = grid.width * grid.height * (2 if name == HEIGHT else 1)
        if path.stat().st_size != expected:
            raise ValueError(f"Wrong byte count for {name} (expected {expected}).")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"Invalid SHA-256 value for {name}.")
        if _digest(path) != digest:
            raise ValueError(f"SHA-256 mismatch for {name}.")
    if levels[0] == levels[1] and np.any(np.fromfile(directory / HEIGHT, dtype="<u2")):
        raise ValueError("A constant elevation range requires zero-valued samples.")
    if ROADS in hashes:
        mask = np.fromfile(directory / ROADS, dtype="uint8")
        if ((mask != 0) & (mask != 255)).any():
            raise ValueError("The road mask contains values other than 0 and 255.")
    return grid, tuple(levels)


def restore(directory, output, *, raw=None, levels=None, flip_y=False):
    directory, output = Path(directory), Path(output)
    if output.resolve().is_relative_to(directory.resolve()):
        raise ValueError("Write the GeoTIFF outside the terrain bundle.")
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"Output already exists: {output}")
    grid, original_levels = validate(directory)
    source = Path(raw) if raw is not None else directory / HEIGHT
    expected = grid.width * grid.height
    if source.stat().st_size != expected * 2:
        raise ValueError(f"RAW16 requires exactly {expected * 2} bytes on this grid.")
    samples = np.fromfile(source, dtype="<u2").reshape(grid.height, grid.width)
    if flip_y:
        samples = samples[::-1]
    values = decode(samples, *(levels if levels is not None else original_levels))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as scratch:
        staged = Path(scratch) / "dem.tif"
        with rasterio.open(
            staged,
            "w",
            driver="GTiff",
            crs=grid.crs,
            transform=grid.transform,
            width=grid.width,
            height=grid.height,
            count=1,
            dtype="float64",
        ) as dst:
            dst.write(values, 1)
            dst.set_band_unit(1, "m")
        # Publish the finished file without overwriting a concurrent writer.
        if os.name == "nt":
            staged.rename(output)
        else:
            os.link(staged, output)
