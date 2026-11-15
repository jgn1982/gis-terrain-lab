"""Create a 65 x 65 synthetic DEM and a road line for the README walkthrough."""

import argparse
import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import transform


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="New directory for example inputs.")
    folder = parser.parse_args().directory
    folder.mkdir(parents=True, exist_ok=False)
    rows, cols = np.indices((65, 65))
    ridge = 60 * np.exp(-((rows - 10) ** 2 + (cols - 15) ** 2) / 90)
    heights = 20 + ridge + (64 - rows) / 8 + cols / 32
    with rasterio.open(
        folder / "dem.tif",
        "w",
        driver="GTiff",
        count=1,
        width=65,
        height=65,
        dtype="float32",
        crs="EPSG:3857",
        transform=from_origin(0, 650, 10, 10),
    ) as dst:
        dst.write(heights.astype("float32"), 1)
        dst.set_band_unit(1, "m")
    lon, lat = transform("EPSG:3857", "OGC:CRS84", [85, 185, 605], [615, 515, 385])
    feature = {
        "type": "Feature",
        "properties": {},
        "geometry": {"type": "LineString", "coordinates": list(zip(lon, lat))},
    }
    roads = {"type": "FeatureCollection", "features": [feature]}
    (folder / "roads.geojson").write_text(json.dumps(roads) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
