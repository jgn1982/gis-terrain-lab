# GIS Terrain Lab

Convert a georeferenced elevation raster into a 16-bit heightfield for a terrain editor, then bring the edited heights back to GIS on the same grid. An optional road mask follows the elevation pixels through reprojection and cropping.

## Try a synthetic tile

From a checkout, use Python 3.11 or newer in a virtual environment.

```sh
python -m pip install .
python examples/make_tile.py demo/input
gis-terrain-lab pack demo/input/dem.tif demo/tile --roads demo/input/roads.geojson --elevation-range 0 100
gis-terrain-lab validate demo/tile
gis-terrain-lab restore demo/tile demo/restored.tif
```

The example generates a 65 × 65 grid with 10 m pixels, a peak in the northwest and a diagonal road. Everything is generated locally. Open the restored GeoTIFF alongside the input DEM in a GIS to compare placement and elevations. For the 0–100 m encoding, the maximum rounding error is about 0.00077 m.

## Prepare a DEM

`pack` reads a local, single-band GeoTIFF and applies band scale, offset and validity masks. Elevations must be in metres. An unlabelled band is assumed to use metres. Supply a DEM in the intended vertical datum. The exchange records a horizontal CRS and a height range and performs no vertical datum conversion.

A north-up raster in a projected metre CRS can use its native grid. To crop, reproject or resample, supply the destination CRS, outer pixel-edge bounds and dimensions together. For example, this crops the synthetic tile to its central 400 m and samples it on a 40 × 40 grid.

```sh
gis-terrain-lab pack demo/input/dem.tif demo/crop --target-crs EPSG:3857 --bounds 125 125 525 525 --size 40 40
```

Reprojection uses bilinear sampling. Missing cells in the resulting DEM stop the export and report how many cells need attention. The default height range spans the elevation samples. Use `--elevation-range` to reserve headroom or share a scale across tiles. Values outside the specified range are rejected. Flat terrain with the default range encodes as zero everywhere.

Roads use GeoJSON FeatureCollections of LineString or MultiLineString features in longitude, latitude order. The mask marks every pixel touched by a reprojected centreline. Properties are ignored. Supply vertices at sufficient spacing to describe curves in the destination projection and split lines at the antimeridian. [GeoJSON coordinate conventions](https://www.rfc-editor.org/rfc/rfc7946#section-4)

## Exchange with World Machine

1. In File Input, open `elevation.r16` as unsigned 16-bit PC little-endian RAW. Set the sample dimensions to match `terrain.json`. The first sample is the northwest pixel centre and rows proceed south. Use the example peak to check orientation in your editor.
2. Set the input elevation mapping and project vertical scale so that codes 0 and 65535 represent the manifest endpoints in metres. For the example use 0 and 100 m. Match horizontal placement to the GIS grid. The example's outer edges span 650 m and the corner sample centres span 640 m. In a placement control that positions corner samples, use the centre span and a half-pixel inset from each outer edge.
3. If present, load `roads.r8` as an unsigned 8-bit mask on the same grid. Black is background and white marks a road.
4. Connect the terrain to File Output and choose RAW16. Write the edited file **outside** the bundle. Preserve dimensions, placement and scale. An identity pass with the asymmetric example makes flips, shifts and vertical remapping easier to spot before working on a real DEM.
5. Restore the exported heightfield with the command below. If the first file row represents the southern edge, add `--flip-y`. If the editor changed the elevation endpoints, pass its actual `--elevation-range MIN_M MAX_M`.

```sh
gis-terrain-lab restore demo/tile demo/edited.tif --raw demo/editor-output.r16
```

RAW contains sample values only. A same-size export can still be mirrored, shifted or scaled incorrectly. Keep the bundle as the original grid reference and verify a recognizable feature after an editor round trip. [World Machine File Input and Output](https://help.world-machine.com/topic/file-input-and-output/) · [RAW16 output](https://help.world-machine.com/topic/device-fileoutput/)

## Bundle format

| File | Contents |
| --- | --- |
| `terrain.json` | Format version, horizontal CRS, west/south/east/north pixel-edge bounds, width, height, elevation endpoints in metres and payload SHA-256 digests |
| `elevation.r16` | Row-major unsigned little-endian samples, two bytes per pixel, north first and west to east |
| `roads.r8` | Optional row-major road mask with the same orientation, one byte per pixel, values 0 or 255 |

The first pixel centre is half a pixel east and south of the northwest outer edge. Given endpoints `lo` and `hi`, the recovered elevation is `lo + (code / 65535) × (hi − lo)`. Restore writes a single-band Float64 GeoTIFF. Inputs and grids are processed in memory. Use a new path for each output. On POSIX systems, GeoTIFF restoration requires a destination filesystem with hard-link support.

`validate` checks the manifest fields, ranges, grid, file set, byte counts and SHA-256 digests. Payload digests detect changes against the manifest. Preserve a trusted copy of the manifest when exchanging data.

## Development

```sh
python -m pip install -e .
python -m unittest discover -s tests -v
```

The test suite creates its own small rasters and vector lines. Runtime dependencies are NumPy and Rasterio. The project code is released under the [MIT License](LICENSE).

## Project maintenance

This is the maintained public repository for GIS Terrain Lab. Source is available under the MIT License. Pull requests are disabled; the owner reviews and publishes project updates. GitHub Actions is disabled, and this repository has no cloud deployment access.
