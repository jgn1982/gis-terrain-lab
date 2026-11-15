"""Burn geographic road centrelines onto a terrain grid."""

import json
from pathlib import Path

import numpy as np
from rasterio.features import rasterize
from rasterio.warp import transform

from .grid import is_number


def _project_line(points, grid):
    if not isinstance(points, list) or len(points) < 2:
        raise ValueError("A road line needs at least two positions.")
    xy = []
    for point in points:
        if (
            not isinstance(point, list)
            or len(point) not in (2, 3)
            or not all(map(is_number, point))
        ):
            raise ValueError("A road position needs two or three finite numbers.")
        lon, lat = point[:2]
        if abs(lon) > 180 or abs(lat) > 90:
            raise ValueError("Road coordinates must be WGS 84 longitude and latitude.")
        if xy and abs(lon - xy[-1][0]) > 180:
            raise ValueError("Split road lines at the antimeridian before packing.")
        xy.append((lon, lat))
    x, y = transform("OGC:CRS84", grid.crs, *zip(*xy))
    if not (np.isfinite(x).all() and np.isfinite(y).all()):
        raise ValueError("A road position falls outside the target projection.")
    return {"type": "LineString", "coordinates": list(zip(x, y))}


def road_mask(path, grid):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("type") != "FeatureCollection" or "crs" in data:
        raise ValueError("Supply an RFC 7946 road FeatureCollection in WGS 84.")
    features = data.get("features")
    if not isinstance(features, list):
        raise ValueError("Road features must be an array.")
    lines = []
    for feature in features:
        if not isinstance(feature, dict) or feature.get("type") != "Feature" or "crs" in feature:
            raise ValueError("Invalid road feature.")
        geom = feature.get("geometry")
        if not isinstance(geom, dict) or "crs" in geom:
            raise ValueError("Each road feature needs a geographic line geometry.")
        coords = geom.get("coordinates")
        kind = geom.get("type")
        if kind == "LineString":
            lines.append(_project_line(coords, grid))
        elif kind == "MultiLineString" and isinstance(coords, list) and coords:
            lines.extend(_project_line(part, grid) for part in coords)
        else:
            raise ValueError("Road geometries must be LineString or MultiLineString.")
    shape = (grid.height, grid.width)
    if not lines:
        return np.zeros(shape, dtype="uint8")
    return rasterize(
        lines,
        out_shape=shape,
        transform=grid.transform,
        default_value=255,
        fill=0,
        all_touched=True,
        dtype="uint8",
    )
