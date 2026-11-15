"""A north-up, metre-based grid. Bounds describe the outside edges of pixels."""

import math
from dataclasses import dataclass

from rasterio.crs import CRS
from rasterio.transform import from_bounds


@dataclass(frozen=True)
class Grid:
    crs: CRS
    bounds: tuple[float, float, float, float]
    width: int
    height: int

    def __post_init__(self):
        if not self.crs.is_projected or not math.isclose(self.crs.linear_units_factor[1], 1.0):
            raise ValueError("Use a projected horizontal CRS with metre units.")
        if any(type(n) is not int or n < 2 for n in (self.width, self.height)):
            raise ValueError("Width and height must be integers of at least two samples.")
        if len(self.bounds) != 4 or not all(is_number(n) for n in self.bounds):
            raise ValueError("Bounds require four finite coordinates.")
        west, south, east, north = self.bounds
        if west >= east or south >= north:
            raise ValueError("West and south must precede east and north.")
        steps = ((east - west) / self.width, (north - south) / self.height)
        if not all(math.isfinite(step) and step > 0 for step in steps):
            raise ValueError("Bounds and dimensions must yield finite positive pixel sizes.")

    @property
    def transform(self):
        return from_bounds(*self.bounds, self.width, self.height)


def is_number(value):
    return type(value) in (int, float) and math.isfinite(value)
