"""CityGML reading, roof classification and the Newell normal."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from mtl_roofs.io.reference import (
    ReferenceBuilding,
    RoofPolygon,
    iter_buildings,
    newell_normal,
    parse_poslist,
)

FLAT = [(0.0, 0.0, 10.0), (4.0, 0.0, 10.0), (4.0, 3.0, 10.0), (0.0, 3.0, 10.0), (0.0, 0.0, 10.0)]
PITCHED = [(0.0, 0.0, 0.0), (4.0, 0.0, 0.0), (4.0, 3.0, 3.0), (0.0, 3.0, 3.0), (0.0, 0.0, 0.0)]


def test_parse_poslist_groups_triples() -> None:
    assert parse_poslist("1 2 3 4 5 6") == [(1.0, 2.0, 3.0), (4.0, 5.0, 6.0)]


def test_parse_poslist_rejects_a_partial_triple() -> None:
    with pytest.raises(ValueError, match="not a multiple of 3"):
        parse_poslist("1 2 3 4")


def test_flat_roof_normal_is_vertical() -> None:
    assert newell_normal(FLAT) == pytest.approx((0.0, 0.0, 1.0))


def test_degenerate_ring_has_no_normal() -> None:
    with pytest.raises(ValueError, match="degenerate ring"):
        newell_normal([(0.0, 0.0, 0.0), (1.0, 1.0, 1.0), (0.0, 0.0, 0.0)])


def test_flat_polygon_area_and_slope() -> None:
    polygon = RoofPolygon("r1", FLAT)
    assert polygon.area() == pytest.approx(12.0)
    assert polygon.slope_degrees() == pytest.approx(0.0)


def test_pitched_polygon_slope() -> None:
    """A 3 m rise over a 3 m run is 45 degrees."""
    assert RoofPolygon("r2", PITCHED).slope_degrees() == pytest.approx(45.0, abs=1e-6)


@pytest.mark.parametrize(
    ("polygons", "expected"),
    [
        ([FLAT], "flat"),
        ([PITCHED], "pitched"),
        ([FLAT, PITCHED], "mixed"),
    ],
)
def test_roof_type_classification(polygons: list, expected: str) -> None:
    building = ReferenceBuilding("1", [RoofPolygon(str(i), p) for i, p in enumerate(polygons)])
    assert building.roof_type() == expected


def test_grouped_buildings_are_flagged() -> None:
    assert ReferenceBuilding("Groupe9637915").is_grouped
    assert not ReferenceBuilding("1585788").is_grouped


def test_building_without_roofs_is_flat_not_a_crash() -> None:
    empty = ReferenceBuilding("x")
    assert empty.roof_area() == 0.0
    assert empty.mean_slope() == 0.0
    assert empty.roof_type() == "flat"


def test_mean_slope_is_area_weighted() -> None:
    """A large flat face plus a tiny steep one is mostly flat."""
    big_flat = [
        (0.0, 0.0, 0.0),
        (100.0, 0.0, 0.0),
        (100.0, 100.0, 0.0),
        (0.0, 100.0, 0.0),
        (0.0, 0.0, 0.0),
    ]
    building = ReferenceBuilding("1", [RoofPolygon("a", big_flat), RoofPolygon("b", PITCHED)])
    assert building.mean_slope() < 1.0


CITYGML = """<?xml version="1.0" encoding="UTF-8"?>
<CityModel xmlns="http://www.opengis.net/citygml/2.0"
           xmlns:bldg="http://www.opengis.net/citygml/building/2.0"
           xmlns:gen="http://www.opengis.net/citygml/generics/2.0"
           xmlns:gml="http://www.opengis.net/gml">
  <cityObjectMember>
    <bldg:Building gml:id="1585788">
      <gen:stringAttribute name="parcelle"><gen:value>0123456</gen:value></gen:stringAttribute>
      <bldg:boundedBy>
        <bldg:RoofSurface gml:id="roof-a">
          <bldg:lod2MultiSurface><gml:MultiSurface><gml:surfaceMember><gml:Polygon>
            <gml:exterior><gml:LinearRing>
              <gml:posList>0 0 10 4 0 10 4 3 10 0 3 10 0 0 10</gml:posList>
            </gml:LinearRing></gml:exterior>
          </gml:Polygon></gml:surfaceMember></gml:MultiSurface></bldg:lod2MultiSurface>
        </bldg:RoofSurface>
      </bldg:boundedBy>
    </bldg:Building>
  </cityObjectMember>
  <cityObjectMember>
    <bldg:Building gml:id="Groupe9637915"/>
  </cityObjectMember>
</CityModel>
"""


def test_iter_buildings_streams_a_citygml_file(tmp_path: Path) -> None:
    path = tmp_path / "tile.gml"
    path.write_text(CITYGML)
    buildings = list(iter_buildings(path))
    assert [b.gml_id for b in buildings] == ["1585788", "Groupe9637915"]
    first = buildings[0]
    assert first.attributes["parcelle"] == "0123456"
    assert first.roof_area() == pytest.approx(12.0)
    assert first.roof_type() == "flat"
    assert math.isclose(first.roofs[0].newell_normal()[2], 1.0)
