"""Command-line entry point for heightfield exchange."""

import argparse
import sys

from rasterio.crs import CRS
from rasterio.errors import RasterioError

from .bundle import pack, restore, validate
from .grid import Grid


def main(argv=None):
    parser = argparse.ArgumentParser(prog="gis-terrain-lab", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("pack", help="Convert a GeoTIFF DEM to a terrain bundle.")
    create.add_argument("dem", help="Single-band elevation GeoTIFF in metres.")
    create.add_argument("directory", help="New bundle directory.")
    create.add_argument("--roads", help="WGS 84 GeoJSON road lines.")
    create.add_argument("--target-crs", help="Projected metric CRS, for example EPSG:3857.")
    create.add_argument(
        "--bounds",
        type=float,
        nargs=4,
        metavar=("W", "S", "E", "N"),
        help="Target pixel-edge bounds in the target CRS.",
    )
    create.add_argument("--size", type=int, nargs=2, metavar=("WIDTH", "HEIGHT"))
    check = commands.add_parser("validate", help="Check the bundle and its payload digests.")
    check.add_argument("directory")
    back = commands.add_parser("restore", help="Write a georeferenced elevation GeoTIFF.")
    back.add_argument("directory")
    back.add_argument("output", help="New GeoTIFF path outside the bundle.")
    back.add_argument("--raw", help="Replacement little-endian RAW16 from a terrain editor.")
    back.add_argument("--flip-y", action="store_true", help="Read the RAW16 as south-first.")
    for cmd in (create, back):
        cmd.add_argument(
            "--elevation-range",
            type=float,
            nargs=2,
            metavar=("MIN_M", "MAX_M"),
            help="Elevation in metres represented by codes 0 and 65535.",
        )
    args = parser.parse_args(argv)
    try:
        if args.command == "pack":
            target = None
            parts = (args.target_crs, args.bounds, args.size)
            if any(part is not None for part in parts):
                if any(part is None for part in parts):
                    parser.error("--target-crs, --bounds and --size must be supplied together")
                target = Grid(CRS.from_string(args.target_crs), tuple(args.bounds), *args.size)
            pack(
                args.dem,
                args.directory,
                target=target,
                roads=args.roads,
                levels=args.elevation_range,
            )
        elif args.command == "restore":
            restore(
                args.directory,
                args.output,
                raw=args.raw,
                levels=args.elevation_range,
                flip_y=args.flip_y,
            )
        else:
            grid, levels = validate(args.directory)
            print(
                f"Verified {grid.width} x {grid.height} samples, {levels[0]:g} to {levels[1]:g} m."
            )
    except (ValueError, OverflowError, OSError, RasterioError) as exc:
        print(f"gis-terrain-lab: {exc}", file=sys.stderr)
        return 1
    return 0
