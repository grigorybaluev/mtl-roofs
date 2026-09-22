"""Command-line interface.

``mtl-roofs <command>``: ``data``, ``reconstruct``, ``evaluate``, ``export``, ``report``.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from mtl_roofs import CRS_EPSG, __version__
from mtl_roofs.config import Settings, StudyArea
from mtl_roofs.io.lidar import tile_member
from mtl_roofs.io.remotezip import RemoteZip
from mtl_roofs.manifest import Source, load_manifest, sha256_file

app = typer.Typer(
    name="mtl-roofs",
    help="Reconstruct LOD2 building roofs for Montreal from open aerial LiDAR.",
    no_args_is_help=True,
    add_completion=False,
)
data_app = typer.Typer(name="data", help="Fetch and verify source data.", no_args_is_help=True)
app.add_typer(data_app)

console = Console()


@app.callback()
def _main() -> None:
    """Reconstruct and evaluate LOD2 roofs for Montreal."""


@app.command()
def version() -> None:
    """Print the package version and the CRS everything is processed in."""
    console.print(f"mtl-roofs {__version__} (EPSG:{CRS_EPSG})")


@data_app.command("areas")
def data_areas() -> None:
    """List the study areas defined in configs/areas/."""
    from mtl_roofs.config import AREAS_DIR

    table = Table("area", "borough", "buildings", "km²", "LiDAR tiles")
    for path in sorted(AREAS_DIR.glob("*.yaml")):
        area = StudyArea.load(path.stem)
        table.add_row(
            area.name,
            area.borough,
            str(area.expected_buildings or "?"),
            f"{area.bbox.area_km2:.2f}",
            str(len(area.bbox.lidar_tiles())),
        )
    console.print(table)


@data_app.command("plan")
def data_plan(
    area: Annotated[str, typer.Option("--area", "-a", help="Study area name.")],
) -> None:
    """Show what `data fetch` would download for an area, without downloading it."""
    study = StudyArea.load(area)
    settings = Settings()
    sources = load_manifest()

    table = Table("source", "member", "size", title=f"fetch plan for {study.name}")
    total = 0
    for tile in study.bbox.lidar_tiles():
        member = tile_member(tile)
        key = _lidar_archive_for(tile, sources)
        if key is None:
            table.add_row("[red]not in manifest[/red]", member, "-")
            continue
        size = _member_size(sources[key].url, member, settings)
        total += size or 0
        table.add_row(key, member, _human(size))
    for ref in study.reference_tiles:
        key = _reference_archive_for(ref, sources)
        if key is None:
            table.add_row("[red]not in manifest[/red]", ref, "-")
            continue
        size = _member_size(sources[key].url, ref, settings)
        total += size or 0
        table.add_row(key, ref, _human(size))
    console.print(table)
    console.print(f"[bold]total download: {_human(total)}[/bold]")


@data_app.command("fetch")
def data_fetch(
    area: Annotated[str, typer.Option("--area", "-a", help="Study area name.")],
    force: Annotated[bool, typer.Option("--force", help="Re-download existing files.")] = False,
) -> None:
    """Download and verify every source needed for a study area.

    LiDAR tiles and reference-model tiles are extracted from the city's bulk archives
    over HTTP range requests, so a study area costs a few hundred megabytes per tile
    rather than the 20 GB of the enclosing archive.
    """
    study = StudyArea.load(area)
    settings = Settings()
    sources = load_manifest()
    raw = settings.raw_dir / study.name
    raw.mkdir(parents=True, exist_ok=True)

    for tile in study.bbox.lidar_tiles():
        member = tile_member(tile)
        key = _lidar_archive_for(tile, sources)
        if key is None:
            console.print(f"[yellow]skip[/yellow] {member}: no archive in manifest")
            continue
        dest = raw / member
        if dest.exists() and not force:
            console.print(f"[dim]have[/dim] {member}")
            continue
        console.print(f"fetching {member} from {key} ...")
        archive = RemoteZip(sources[key].url, settings.user_agent)
        archive.extract_member(archive.member(member), dest)
        console.print(
            f"  [green]ok[/green] {_human(dest.stat().st_size)} sha256={sha256_file(dest)[:16]}…"
        )

    console.print("[bold green]done[/bold green]")


@app.command()
def reconstruct(
    area: Annotated[str, typer.Option("--area", "-a")],
) -> None:
    """Reconstruct LOD2 roofs for a study area."""
    raise NotImplementedError


@app.command()
def evaluate(
    area: Annotated[str, typer.Option("--area", "-a")] = "",
    smoke: Annotated[bool, typer.Option("--smoke", help="Run on tests/fixtures only.")] = False,
    fixtures: Annotated[Path, typer.Option("--fixtures")] = Path("tests/fixtures"),
    out: Annotated[Path, typer.Option("--out")] = Path(".eval-smoke"),
) -> None:
    """Evaluate reconstructions against the reference model."""
    raise NotImplementedError


@app.command()
def export(
    area: Annotated[str, typer.Option("--area", "-a")],
) -> None:
    """Export a 3D Tiles tileset for the viewer."""
    raise NotImplementedError


@app.command()
def report(
    area: Annotated[str, typer.Option("--area", "-a")],
) -> None:
    """Write the evaluation breakdown report."""
    raise NotImplementedError


# ----------------------------------------------------------------- helpers
def _lidar_archive_for(tile: str, sources: Mapping[str, Source]) -> str | None:
    """Find the manifest key of the bulk archive holding a LiDAR tile.

    Archives are keyed ``lidar2015_<xmin>-<xmax>`` by easting band.
    """
    easting = int(tile.split("-")[0])
    for key in sources:
        if not key.startswith("lidar2015_"):
            continue
        low, high = key.removeprefix("lidar2015_").split("-")
        if int(low) <= easting <= int(high):
            return key
    return None


def _reference_archive_for(member: str, sources: Mapping[str, Source]) -> str | None:
    """Find the manifest key of the borough archive holding a reference tile."""
    prefix = "".join(c for c in member if not c.isdigit()).lower()
    for key in sources:
        if key.startswith("lod2_2016_") and key.removeprefix("lod2_2016_").startswith(prefix):
            return key
    return None


def _member_size(url: str, fragment: str, settings: Settings) -> int | None:
    """Compressed size of an archive member, read from the remote central directory."""
    try:
        archive = RemoteZip(url, settings.user_agent)
        return archive.member(fragment).compressed_size
    except (KeyError, OSError):
        return None


def _human(size: int | None) -> str:
    """Format a byte count for a terminal table."""
    if size is None:
        return "-"
    if size >= 1 << 30:
        return f"{size / (1 << 30):.2f} GB"
    return f"{size / (1 << 20):.0f} MB"
