"""The per-building form export (#91).

Each case is a failure that would produce a plausible-looking row rather than an
error: a ``Groupe`` id silently merged with another tile's, a blank ``parcelle``
exported as a value, a height measured from the extruded wall base instead of the
ground, a degenerate ring aborting a whole borough.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mtl_roofs.export.building_form import (
    MODEL_VINTAGE,
    building_row,
    iter_rows,
    roof_z_p50,
    tile_of,
    write_building_form,
)
from mtl_roofs.io.reference import ReferenceBuilding, RoofPolygon, iter_buildings

_HEAD = """<?xml version="1.0" encoding="UTF-8"?>
<CityModel xmlns="http://www.opengis.net/citygml/2.0"
           xmlns:bldg="http://www.opengis.net/citygml/building/2.0"
           xmlns:gen="http://www.opengis.net/citygml/generics/2.0"
           xmlns:gml="http://www.opengis.net/gml">
"""


def _surface(kind: str, poslist: str) -> str:
    return f"""<bldg:boundedBy><bldg:{kind}>
      <bldg:lod2MultiSurface><gml:MultiSurface><gml:surfaceMember><gml:Polygon>
        <gml:exterior><gml:LinearRing><gml:posList>{poslist}</gml:posList></gml:LinearRing>
        </gml:exterior></gml:Polygon></gml:surfaceMember></gml:MultiSurface>
      </bldg:lod2MultiSurface></bldg:{kind}></bldg:boundedBy>"""


def _building(gml_id: str, roof_z: float, base_z: float, parcelle: str = " ") -> str:
    """A 10 x 10 m box: flat roof at ``roof_z``, ground surface at ``base_z``."""
    roof = f"0 0 {roof_z} 10 0 {roof_z} 10 10 {roof_z} 0 10 {roof_z} 0 0 {roof_z}"
    ground = f"0 0 {base_z} 0 10 {base_z} 10 10 {base_z} 10 0 {base_z} 0 0 {base_z}"
    return f"""<cityObjectMember><bldg:Building gml:id="{gml_id}">
      <gen:stringAttribute name="parcelle"><gen:value>{parcelle}</gen:value></gen:stringAttribute>
      {_surface("RoofSurface", roof)}{_surface("GroundSurface", ground)}
    </bldg:Building></cityObjectMember>"""


def _tile(tmp_path: Path, name: str, *buildings: str) -> Path:
    path = tmp_path / f"{name}_2016.gml"
    path.write_text(_HEAD + "".join(buildings) + "</CityModel>")
    return path


def test_solid_z_min_reads_the_ground_surface_not_just_the_roof(tmp_path: Path) -> None:
    (b,) = iter_buildings(_tile(tmp_path, "V01", _building("V0100001", 20.0, 7.0)))
    assert b.solid_z_min == pytest.approx(7.0)


def test_ground_is_lowest_vertex_plus_three_metres(tmp_path: Path) -> None:
    (b,) = iter_buildings(_tile(tmp_path, "V01", _building("V0100001", 20.0, 7.0)))
    row = building_row(b, "V01", "x" * 64)
    assert row["ground_z"] == pytest.approx(10.0)
    assert row["ground_method"] == "lod2_minus3"
    assert row["height_max_m"] == pytest.approx(10.0)
    assert row["footprint_area_m2"] == pytest.approx(100.0)
    assert row["roof_form"] == "flat"
    assert row["model_vintage"] == MODEL_VINTAGE


def test_blank_parcelle_is_none_not_a_space(tmp_path: Path) -> None:
    (b,) = iter_buildings(_tile(tmp_path, "V01", _building("V0100001", 20.0, 7.0)))
    assert building_row(b, "V01", "x" * 64)["parcelle"] is None
    (c,) = iter_buildings(_tile(tmp_path, "V02", _building("V0200001", 20.0, 7.0, "0123456")))
    assert building_row(c, "V02", "x" * 64)["parcelle"] == "0123456"


def test_groupe_ids_repeating_across_tiles_stay_distinct(tmp_path: Path) -> None:
    a = _tile(tmp_path, "V01", _building("Groupe152", 20.0, 7.0))
    b = _tile(tmp_path, "V02", _building("Groupe152", 30.0, 8.0))
    ids = [r["building_id"] for r in iter_rows([a, b])]
    assert ids == ["V01:Groupe152", "V02:Groupe152"]


def test_duplicate_ids_within_a_tile_raise(tmp_path: Path) -> None:
    dup = _tile(tmp_path, "V01", _building("Groupe1", 20.0, 7.0), _building("Groupe1", 21.0, 7.0))
    with pytest.raises(ValueError, match="duplicate building_id"):
        write_building_form([dup], tmp_path / "out.parquet")


def test_building_without_roof_is_kept_with_null_geometry() -> None:
    row = building_row(ReferenceBuilding("V0100009"), "V01", "x" * 64)
    assert row["outline"] is None
    assert row["height_max_m"] is None
    assert row["roof_form"] is None
    assert row["n_roof_surfaces"] == 0


def test_degenerate_ring_does_not_abort_the_row() -> None:
    """Verdun's V04 has a roof ring of fewer than four vertices."""
    square = [(0.0, 0.0, 9.0), (4.0, 0.0, 9.0), (4.0, 4.0, 9.0), (0.0, 4.0, 9.0), (0.0, 0.0, 9.0)]
    sliver = [(0.0, 0.0, 9.0), (1.0, 0.0, 9.0), (0.0, 0.0, 9.0)]
    b = ReferenceBuilding("V0400001", [RoofPolygon("a", square), RoofPolygon("b", sliver)])
    assert building_row(b, "V04", "x" * 64)["footprint_area_m2"] == pytest.approx(16.0)


def test_roof_p50_is_area_weighted() -> None:
    """A large low roof and a small high one: the median is the large one's height."""
    low = [
        (0.0, 0.0, 10.0),
        (10.0, 0.0, 10.0),
        (10.0, 10.0, 10.0),
        (0.0, 10.0, 10.0),
        (0.0, 0.0, 10.0),
    ]
    high = [
        (0.0, 0.0, 20.0),
        (2.0, 0.0, 20.0),
        (2.0, 2.0, 20.0),
        (0.0, 2.0, 20.0),
        (0.0, 0.0, 20.0),
    ]
    b = ReferenceBuilding(
        "1", [RoofPolygon("a", high), RoofPolygon("b", low), RoofPolygon("c", high)]
    )
    assert roof_z_p50(b) == pytest.approx(10.0)


def test_parquet_round_trip_keeps_crs_and_rows(tmp_path: Path) -> None:
    gpd = pytest.importorskip("geopandas")
    tile = _tile(tmp_path, "V01", _building("V0100001", 20.0, 7.0), _building("Groupe2", 15.0, 6.0))
    dest = tmp_path / "out" / "building_form.parquet"
    assert write_building_form([tile], dest) == 2
    frame = gpd.read_parquet(dest)
    assert frame.crs.to_epsg() == 2950
    assert list(frame.building_id) == ["V01:V0100001", "V01:Groupe2"]
    assert frame.is_grouped.tolist() == [False, True]
    assert not dest.with_name(dest.name + ".part").exists()


def test_tile_of() -> None:
    assert tile_of(Path("V01_2016.gml")) == "V01"
    assert tile_of(Path("CDNNDG03_2016.gml")) == "CDNNDG03"
