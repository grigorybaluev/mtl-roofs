"""Reader for the city's CityGML 2.0 LOD2 reference model.

Two properties of these files drive the design:

* the ``gml:Envelope`` carries **no** ``srsName``, so the CRS must be asserted
  externally as EPSG:2950 rather than read from the document;
* walls and ground surfaces are extrapolated from the roofs down to 3 m below grade,
  so only ``bldg:RoofSurface`` geometry is measured. Evaluation scores roofs.
"""

from __future__ import annotations

import math
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

NS = {
    "bldg": "http://www.opengis.net/citygml/building/2.0",
    "gml": "http://www.opengis.net/gml",
    "gen": "http://www.opengis.net/citygml/generics/2.0",
}

Point3 = tuple[float, float, float]


@dataclass(slots=True)
class RoofPolygon:
    """One planar roof surface from the reference model."""

    gml_id: str
    points: list[Point3]

    def newell_normal(self) -> Point3:
        """Unit normal via Newell's method, robust for non-planar rings."""
        return newell_normal(self.points)

    def area(self) -> float:
        """Area of the polygon in square metres."""
        nx, ny, nz = _newell_raw(self.points)
        return math.sqrt(nx * nx + ny * ny + nz * nz) / 2.0

    def slope_degrees(self) -> float:
        """Angle between the surface normal and vertical, in degrees."""
        nx, ny, nz = _newell_raw(self.points)
        norm = math.sqrt(nx * nx + ny * ny + nz * nz)
        if norm == 0.0:
            return 0.0
        return math.degrees(math.acos(min(1.0, abs(nz) / norm)))


@dataclass(slots=True)
class ReferenceBuilding:
    """A building from the reference model, restricted to its roof surfaces."""

    gml_id: str
    roofs: list[RoofPolygon] = field(default_factory=list)
    attributes: dict[str, str] = field(default_factory=dict)

    @property
    def is_grouped(self) -> bool:
        """Whether this is a merged block rather than a single addressed building.

        The city emits merged blocks as ``Groupe<number>``; those cannot be matched
        one-to-one against a footprint and are reported separately.
        """
        return self.gml_id.startswith("Groupe")

    def roof_area(self) -> float:
        """Total roof area in square metres."""
        return sum(r.area() for r in self.roofs)

    def mean_slope(self) -> float:
        """Area-weighted mean roof slope in degrees."""
        total = self.roof_area()
        if total <= 0:
            return 0.0
        return sum(r.slope_degrees() * r.area() for r in self.roofs) / total

    def steep_fraction(self, threshold_degrees: float = 15.0) -> float:
        """Fraction of roof area steeper than ``threshold_degrees``."""
        total = self.roof_area()
        if total <= 0:
            return 0.0
        steep = sum(r.area() for r in self.roofs if r.slope_degrees() >= threshold_degrees)
        return steep / total

    def roof_type(self) -> str:
        """Coarse roof class used for the evaluation breakdown.

        This is deliberately a geometric rule, not a learned label: it is the ground
        truth the v1 ML classifier is scored against, so it must be reproducible.
        """
        steep = self.steep_fraction()
        if steep < 0.10:
            return "flat"
        if steep < 0.60:
            return "mixed"
        return "pitched"


def _newell_raw(points: list[Point3]) -> Point3:
    """Unnormalised Newell normal; its magnitude is twice the polygon area."""
    nx = ny = nz = 0.0
    for i in range(len(points) - 1):
        x1, y1, z1 = points[i]
        x2, y2, z2 = points[i + 1]
        nx += (y1 - y2) * (z1 + z2)
        ny += (z1 - z2) * (x1 + x2)
        nz += (x1 - x2) * (y1 + y2)
    return nx, ny, nz


def newell_normal(points: list[Point3]) -> Point3:
    """Unit-length Newell normal of a closed ring.

    Raises:
        ValueError: if the ring is degenerate (zero area).
    """
    nx, ny, nz = _newell_raw(points)
    norm = math.sqrt(nx * nx + ny * ny + nz * nz)
    if norm == 0.0:
        msg = "degenerate ring: zero-area polygon has no normal"
        raise ValueError(msg)
    return nx / norm, ny / norm, nz / norm


def parse_poslist(text: str) -> list[Point3]:
    """Parse a ``gml:posList`` of 3D coordinates into triples."""
    values = [float(v) for v in text.split()]
    if len(values) % 3 != 0:
        msg = f"posList length {len(values)} is not a multiple of 3"
        raise ValueError(msg)
    return [(values[i], values[i + 1], values[i + 2]) for i in range(0, len(values), 3)]


def iter_buildings(path: Path) -> Iterator[ReferenceBuilding]:
    """Stream buildings out of a CityGML file.

    Uses incremental parsing and clears each element, because a single borough tile
    is 35-100 MB of XML and a whole borough will not fit in memory as a tree.
    """
    building_tag = f"{{{NS['bldg']}}}Building"
    roof_tag = f"{{{NS['bldg']}}}RoofSurface"
    poslist_tag = f"{{{NS['gml']}}}posList"
    id_attr = f"{{{NS['gml']}}}id"

    for _event, element in ElementTree.iterparse(str(path), events=("end",)):
        if element.tag != building_tag:
            continue
        roofs: list[RoofPolygon] = []
        for surface in element.iter(roof_tag):
            surface_id = surface.get(id_attr, "")
            for poslist in surface.iter(poslist_tag):
                if poslist.text:
                    roofs.append(RoofPolygon(surface_id, parse_poslist(poslist.text)))
        attributes = {
            attr.get("name", ""): (attr.findtext(f"{{{NS['gen']}}}value") or "")
            for attr in element.iter(f"{{{NS['gen']}}}stringAttribute")
        }
        yield ReferenceBuilding(element.get(id_attr, ""), roofs, attributes)
        element.clear()


def extract_citygml(nested_archive: Path, dest_dir: Path) -> Path:
    """Unpack the single CityGML file out of a per-tile reference archive.

    Each borough archive holds one nested ZIP per tile, and each of those holds one
    ``.gml`` beside a folder of texture JPEGs. Only the geometry is wanted: for tile
    PMR06 the CityGML is 35 MB of a 169 MB member, the rest being 399 textures that
    this project never reads.

    Args:
        nested_archive: The per-tile ZIP, as extracted from the borough archive.
        dest_dir: Directory to write the ``.gml`` into.

    Returns:
        Path to the extracted CityGML file.

    Raises:
        ValueError: if the archive holds no ``.gml``, or more than one.
    """
    with zipfile.ZipFile(nested_archive) as bundle:
        names = [n for n in bundle.namelist() if n.lower().endswith(".gml")]
        if len(names) != 1:
            msg = f"{nested_archive.name}: expected exactly one .gml, found {len(names)}"
            raise ValueError(msg)
        name = names[0]
        dest = dest_dir / Path(name).name
        dest_dir.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            with bundle.open(name) as src, tmp.open("wb") as out:
                while chunk := src.read(1 << 20):
                    out.write(chunk)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        tmp.replace(dest)
    return dest
