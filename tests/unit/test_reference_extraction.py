"""Unpacking CityGML out of the reference model's nested per-tile archives."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from mtl_roofs.io.reference import extract_citygml

GML = b'<?xml version="1.0"?><CityModel/>'


def make_tile_archive(path: Path, gml_names: list[str], textures: int = 3) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in gml_names:
            zf.writestr(name, GML)
        for i in range(textures):
            zf.writestr(f"CDNNDG03_2016_Appearance/{i}.jpg", b"\xff\xd8\xff" + bytes(500))
    return path


def test_keeps_the_gml_and_discards_the_textures(tmp_path: Path) -> None:
    """Textures are most of the bulk and this project never reads them."""
    archive = make_tile_archive(tmp_path / "tile.zip", ["CDNNDG03_2016.gml"])
    out = tmp_path / "out"
    extracted = extract_citygml(archive, out)

    assert extracted.name == "CDNNDG03_2016.gml"
    assert extracted.read_bytes() == GML
    assert [p.name for p in out.iterdir()] == ["CDNNDG03_2016.gml"]


def test_flattens_a_nested_path(tmp_path: Path) -> None:
    archive = make_tile_archive(tmp_path / "tile.zip", ["CDNNDG_2016_GML/CDNNDG03_2016.gml"])
    extracted = extract_citygml(archive, tmp_path / "out")
    assert extracted.name == "CDNNDG03_2016.gml"
    assert extracted.parent.name == "out"


def test_an_archive_with_no_gml_is_an_error(tmp_path: Path) -> None:
    archive = make_tile_archive(tmp_path / "tile.zip", [])
    with pytest.raises(ValueError, match="found 0"):
        extract_citygml(archive, tmp_path / "out")


def test_an_ambiguous_archive_is_an_error(tmp_path: Path) -> None:
    archive = make_tile_archive(tmp_path / "tile.zip", ["a_2016.gml", "b_2016.gml"])
    with pytest.raises(ValueError, match="found 2"):
        extract_citygml(archive, tmp_path / "out")


def test_leaves_no_partial_file_when_the_archive_is_corrupt(tmp_path: Path) -> None:
    archive = make_tile_archive(tmp_path / "tile.zip", ["CDNNDG03_2016.gml"])
    raw = bytearray(archive.read_bytes())
    raw[40] ^= 0xFF  # damage the deflate stream, keep the directory intact
    archive.write_bytes(bytes(raw))

    out = tmp_path / "out"
    with pytest.raises((zipfile.BadZipFile, ValueError, OSError)):
        extract_citygml(archive, out)
    assert not list(out.iterdir()) if out.exists() else True
