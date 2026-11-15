import contextlib
import hashlib
import io
import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import rasterio
from gis_terrain_lab.bundle import HEIGHT, MANIFEST, ROADS, pack, restore, validate
from gis_terrain_lab.cli import main
from gis_terrain_lab.dem import decode, encode
from gis_terrain_lab.grid import Grid
from rasterio.crs import CRS
from rasterio.transform import from_origin
from rasterio.warp import transform


class ExchangeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.dem = self.root / "input.tif"
        self.tile = self.root / "tile"
        self.output = self.root / "result.tif"
        self.values = np.array(
            [[101, -3, 27, 43], [13, 17, 19, 23], [0, -11, 5, 7]], dtype="float64"
        )
        self.affine = from_origin(0, 60, 20, 20)
        self.crs = CRS.from_epsg(3857)
        self.write_dem()

    def write_dem(self, transform=None, crs="EPSG:3857", **options):
        with rasterio.open(
            self.dem,
            "w",
            driver="GTiff",
            count=1,
            height=3,
            width=4,
            dtype="float64",
            transform=transform or self.affine,
            crs=crs,
            **options,
        ) as dst:
            dst.write(self.values, 1)

    def metadata(self):
        return json.loads((self.tile / MANIFEST).read_text())

    def change(self, **fields):
        metadata = self.metadata()
        metadata.update(fields)
        (self.tile / MANIFEST).write_text(json.dumps(metadata))

    def roads(self, geometry):
        features = (
            []
            if geometry is None
            else [{"type": "Feature", "geometry": geometry, "properties": {"id": "ignored"}}]
        )
        path = self.root / "lines.geojson"
        path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
        return path

    def test_round_trip_placement_scaling_and_endian(self):
        pack(self.dem, self.tile)
        grid, bounds = validate(self.tile)
        self.assertEqual(bounds, (-11, 101))
        self.assertEqual((grid.height, grid.width), (3, 4))
        self.assertEqual(grid.bounds, (0, 0, 80, 60))
        self.assertEqual((self.tile / HEIGHT).read_bytes()[:2], b"\xff\xff")
        restore(self.tile, self.output)
        with rasterio.open(self.output) as src:
            self.assertEqual(src.crs, self.crs)
            self.assertEqual(src.transform, self.affine)
            self.assertEqual(src.units, ("m",))
            np.testing.assert_allclose(src.read(1), self.values, rtol=0, atol=112 / 131070)

    def test_wire_values(self):
        values = np.array([[0.0, 1.0, 256.0, 65535.0]])
        self.assertEqual(encode(values, 0, 65535).tobytes(), bytes([0, 0, 1, 0, 0, 1, 255, 255]))

    def test_flat_and_out_of_range(self):
        self.values.fill(-17.5)
        self.write_dem()
        pack(self.dem, self.tile)
        self.assertEqual((self.tile / HEIGHT).read_bytes(), bytes(24))
        self.assertEqual(validate(self.tile)[1], (-17.5, -17.5))
        restore(self.tile, self.output)
        with rasterio.open(self.output) as src:
            np.testing.assert_array_equal(src.read(1), self.values)
        for levels in [(-17, 0), (1, -1), (0, float("inf"))]:
            with self.subTest(levels=levels), self.assertRaises(ValueError):
                encode(self.values, *levels)
        with self.assertRaises(ValueError):
            decode(np.ones((1, 2)), 0, 0)

    def test_band_offset_scale_and_unit(self):
        with rasterio.open(self.dem, "r+") as dst:
            dst.scales, dst.offsets = (0.5,), (50.0,)
            dst.set_band_unit(1, "metre")
        pack(self.dem, self.tile)
        self.assertEqual(validate(self.tile)[1], (44.5, 100.5))
        with rasterio.open(self.dem, "r+") as dst:
            dst.set_band_unit(1, "ft")
        with self.assertRaisesRegex(ValueError, "vertical units"):
            pack(self.dem, self.root / "feet")

    def test_nodata_stops_publish_and_allows_retry(self):
        self.write_dem(nodata=-11)
        with self.assertRaisesRegex(ValueError, "1 missing"):
            pack(self.dem, self.tile)
        self.assertFalse(self.tile.exists())
        self.write_dem()
        pack(self.dem, self.tile)
        self.assertTrue((self.tile / MANIFEST).is_file())

    def test_internal_mask(self):
        with rasterio.open(self.dem, "r+") as dst:
            mask = np.full((3, 4), 255, dtype="uint8")
            mask[0, 1] = 0
            dst.write_mask(mask)
        with self.assertRaisesRegex(ValueError, "1 missing"):
            pack(self.dem, self.tile)

    def test_geographic_dem_and_metric_target(self):
        self.write_dem(from_origin(0, 0.003, 0.001, 0.001), "EPSG:4326")
        with self.assertRaisesRegex(ValueError, "projected"):
            pack(self.dem, self.tile)
        radius = 6378137
        east = radius * math.radians(0.004)
        north = radius * math.asinh(math.tan(math.radians(0.003)))
        grid = Grid(self.crs, (0, 0, east, north), 4, 3)
        pack(self.dem, self.tile, target=grid)
        self.assertEqual(validate(self.tile)[0], grid)
        restore(self.tile, self.output)
        with rasterio.open(self.output) as src:
            self.assertEqual(src.crs, self.crs)
            self.assertEqual(src.transform, from_origin(0, north, east / 4, north / 3))
            np.testing.assert_allclose(src.read(1), self.values, rtol=0, atol=0.001)

    def test_cropped_grid_alignment(self):
        grid = Grid(self.crs, (20, 0, 80, 40), 3, 2)
        pack(self.dem, self.tile, target=grid)
        restore(self.tile, self.output)
        with rasterio.open(self.output) as src:
            np.testing.assert_allclose(src.read(1), self.values[1:, 1:], atol=0.001)

    def test_partial_dem_coverage(self):
        grid = Grid(self.crs, (-40, 0, 80, 60), 6, 3)
        with self.assertRaisesRegex(ValueError, "missing"):
            pack(self.dem, self.tile, target=grid)

    def test_road_mask_geography_and_orientation(self):
        lon, lat = transform(self.crs, "OGC:CRS84", [3, 76], [50, 50])
        line = list(zip(lon, lat))
        roads = self.roads({"type": "MultiLineString", "coordinates": [line]})
        pack(self.dem, self.tile, roads=roads)
        pixels = np.fromfile(self.tile / ROADS, dtype="uint8").reshape(3, 4)
        np.testing.assert_array_equal(pixels[0], np.full(4, 255))
        self.assertEqual(int(pixels[1:].sum()), 0)
        self.assertFalse((self.tile / "lines.geojson").exists())

    def test_empty_mask(self):
        pack(self.dem, self.tile, roads=self.roads(None))
        self.assertEqual((self.tile / ROADS).read_bytes(), bytes(12))

    def test_unsupported_road_inputs_leave_no_bundle(self):
        for geometry in [
            {"type": "Polygon", "coordinates": []},
            {"type": "LineString", "coordinates": [[181, 0], [0, 0]]},
            {"type": "LineString", "coordinates": [[179, 0], [-179, 0]]},
            {"type": "LineString", "coordinates": [[1, 2]]},
        ]:
            with self.subTest(geometry=geometry), self.assertRaises(ValueError):
                pack(self.dem, self.tile, roads=self.roads(geometry))
            self.assertFalse(self.tile.exists())

    def test_edited_raw_flip_and_new_range(self):
        pack(self.dem, self.tile)
        edited = self.root / "edited.r16"
        raw = np.arange(12, dtype="<u2").reshape(3, 4)
        raw[::-1].tofile(edited)
        restore(self.tile, self.output, raw=edited, levels=(0, 65535), flip_y=True)
        with rasterio.open(self.output) as src:
            np.testing.assert_array_equal(src.read(1), raw)
            self.assertEqual(src.transform, self.affine)

    def test_short_raw_prevents_output(self):
        pack(self.dem, self.tile)
        edited = self.root / "edited.r16"
        edited.write_bytes(bytes(23))
        with self.assertRaisesRegex(ValueError, "24 bytes"):
            restore(self.tile, self.output, raw=edited)
        self.assertFalse(self.output.exists())

    def test_existing_outputs_survive(self):
        pack(self.dem, self.tile)
        original = (self.tile / HEIGHT).read_bytes()
        with self.assertRaises(FileExistsError):
            pack(self.dem, self.tile)
        self.assertEqual((self.tile / HEIGHT).read_bytes(), original)
        self.output.write_bytes(b"keep")
        with self.assertRaises(FileExistsError):
            restore(self.tile, self.output)
        self.assertEqual(self.output.read_bytes(), b"keep")
        with self.assertRaisesRegex(ValueError, "outside"):
            restore(self.tile, self.tile / "nested" / "result.tif")

    def test_validation_detects_corruption_and_truncation(self):
        pack(self.dem, self.tile)
        (self.tile / HEIGHT).write_bytes(bytes(24))
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            validate(self.tile)
        (self.tile / HEIGHT).write_bytes(bytes(16))
        with self.assertRaisesRegex(ValueError, "byte count"):
            validate(self.tile)

    def test_reject_invalid_manifest(self):
        pack(self.dem, self.tile)
        metadata = self.metadata()
        for change in [
            {"width": True},
            {"bounds": [0, 1, 0, 2]},
            {"format": "future"},
            {"sha256": {"../elsewhere": "0" * 64}},
            {"crs": 3857},
            {"elevation_m": [1, -3]},
            {"elevation_m": [None, 1]},
            {"extra": "x"},
        ]:
            (self.tile / MANIFEST).write_text(json.dumps(metadata))
            self.change(**change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate(self.tile)

    def test_extra_payload_and_mask_values(self):
        pack(self.dem, self.tile, roads=self.roads(None))
        extra = self.tile / "extra.raw"
        extra.touch()
        with self.assertRaisesRegex(ValueError, "contents"):
            validate(self.tile)
        extra.unlink()
        bad_mask = bytes(range(12))
        (self.tile / ROADS).write_bytes(bad_mask)
        hashes = self.metadata()["sha256"]
        hashes[ROADS] = hashlib.sha256(bad_mask).hexdigest()
        self.change(sha256=hashes)
        with self.assertRaisesRegex(ValueError, "road mask"):
            validate(self.tile)

    def test_two_builds_have_identical_bytes(self):
        other = self.root / "second"
        for output in (self.tile, other):
            pack(self.dem, output, roads=self.roads(None))
        for filename in (HEIGHT, ROADS, MANIFEST):
            self.assertEqual((self.tile / filename).read_bytes(), (other / filename).read_bytes())

    def test_cli_exit_codes_and_entrypoint(self):
        command = [sys.executable, "-m", "gis_terrain_lab"]
        subprocess.run([*command, "pack", self.dem, self.tile], check=True)
        result = subprocess.run(
            [*command, "validate", self.tile], check=False, text=True, capture_output=True
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("4 x 3", result.stdout)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["pack", str(self.dem), str(self.tile)]), 1)
            with self.assertRaises(SystemExit) as error:
                main(["pack", str(self.dem), str(self.tile), "--size", "5", "5"])
            self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
