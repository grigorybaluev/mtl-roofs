"""Command-line interface.

``mtl-roofs <command>``: ``data``, ``reconstruct``, ``evaluate``, ``export``, ``report``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from mtl_roofs import CRS_EPSG, __version__
from mtl_roofs.config import Settings, StudyArea
from mtl_roofs.io.lidar import tile_member
from mtl_roofs.io.reference import extract_citygml
from mtl_roofs.io.remotezip import RemoteZip
from mtl_roofs.manifest import (
    ChecksumMismatchError,
    Member,
    Source,
    load_manifest,
    member_yaml_block,
    sha256_file,
    verify_or_raise,
)

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
    plan = _plan_artifacts(area)
    settings = Settings()

    table = Table("kind", "source", "artefact", "size", "pinned", title=f"fetch plan for {area}")
    total = 0
    for item in plan.items:
        if item.source_key is None:
            table.add_row(item.label, "[red]not in manifest[/red]", item.member.artifact, "-", "-")
            continue
        size = _member_size(plan.sources[item.source_key].url, item.member.key, settings)
        total += size or 0
        table.add_row(
            item.label,
            item.source_key,
            item.member.artifact,
            _human(size),
            "[green]yes[/green]" if item.member.is_pinned else "[yellow]no[/yellow]",
        )
    console.print(table)
    console.print(f"[bold]total download: {_human(total)}[/bold]")


@data_app.command("fetch")
def data_fetch(
    area: Annotated[str, typer.Option("--area", "-a", help="Study area name.")],
    force: Annotated[bool, typer.Option("--force", help="Re-download existing files.")] = False,
) -> None:
    """Download and verify every source a study area needs.

    Tiles are extracted from the city's bulk archives over HTTP range requests, so an
    area costs a few hundred megabytes per tile rather than the 20 GB of the enclosing
    archive.

    Every artefact is checked against its pinned SHA-256. An existing file that fails
    verification is re-downloaded once; if it still fails, the command exits non-zero
    rather than letting a corrupt tile into the pipeline.
    """
    plan = _plan_artifacts(area)
    settings = Settings()
    raw = settings.raw_dir / plan.study.name
    raw.mkdir(parents=True, exist_ok=True)

    unpinned: list[str] = []
    failed: list[str] = []

    for item in plan.items:
        if item.source_key is None:
            console.print(f"[red]skip[/red]  {item.member.artifact}: no archive in the manifest")
            failed.append(item.member.artifact)
            continue

        dest = raw / item.member.artifact
        if not item.member.is_pinned:
            unpinned.append(item.member.artifact)

        if dest.exists() and not force:
            if not item.member.is_pinned:
                console.print(
                    f"[yellow]have[/yellow]  {item.member.artifact} [dim](unpinned)[/dim]"
                )
                continue
            try:
                verify_or_raise(dest, item.member.sha256)
            except ChecksumMismatchError as mismatch:
                console.print(f"[red]stale[/red] {item.member.artifact}: {mismatch}")
                console.print("        re-downloading ...")
            else:
                console.print(f"[green]have[/green]  {item.member.artifact} [dim](verified)[/dim]")
                continue

        try:
            _retrieve(item, plan.sources[item.source_key], raw, settings)
        except (OSError, ValueError, KeyError) as exc:
            console.print(f"[red]fail[/red]  {item.member.artifact}: {exc}")
            failed.append(item.member.artifact)
            continue

        try:
            verify_or_raise(dest, item.member.sha256)
        except ChecksumMismatchError as mismatch:
            console.print(f"[red]fail[/red]  {mismatch}")
            failed.append(item.member.artifact)
            continue

        state = "verified" if item.member.is_pinned else "unpinned"
        console.print(
            f"[green]ok[/green]    {item.member.artifact} "
            f"[dim]{_human(dest.stat().st_size)} ({state})[/dim]"
        )

    _report_summary(unpinned, failed, area)


@data_app.command("verify")
def data_verify(
    area: Annotated[str, typer.Option("--area", "-a", help="Study area name.")],
) -> None:
    """Re-check already-downloaded artefacts against the manifest, without network."""
    plan = _plan_artifacts(area)
    raw = Settings().raw_dir / plan.study.name

    unpinned: list[str] = []
    failed: list[str] = []
    for item in plan.items:
        dest = raw / item.member.artifact
        if not dest.exists():
            console.print(f"[yellow]missing[/yellow] {item.member.artifact}")
            failed.append(item.member.artifact)
            continue
        if not item.member.is_pinned:
            console.print(f"[yellow]unpinned[/yellow] {item.member.artifact}")
            unpinned.append(item.member.artifact)
            continue
        try:
            verify_or_raise(dest, item.member.sha256)
        except ChecksumMismatchError as mismatch:
            console.print(f"[red]MISMATCH[/red] {mismatch}")
            failed.append(item.member.artifact)
        else:
            console.print(f"[green]ok[/green]       {item.member.artifact}")

    _report_summary(unpinned, failed, area)


@data_app.command("checksums")
def data_checksums(
    area: Annotated[str, typer.Option("--area", "-a", help="Study area name.")],
) -> None:
    """Print the YAML to paste into `data/manifest.yaml` to pin this area's artefacts.

    Nothing is written automatically. Pinning is a deliberate act: a tool that pinned
    whatever it had just downloaded would certify a corrupt file as confidently as a
    good one, which is the whole thing checksums exist to prevent.
    """
    plan = _plan_artifacts(area)
    raw = Settings().raw_dir / plan.study.name

    by_source: dict[str, list[Member]] = {}
    missing: list[str] = []
    for item in plan.items:
        dest = raw / item.member.artifact
        if item.source_key is None or not dest.exists():
            missing.append(item.member.artifact)
            continue
        pinned = Member(
            key=item.member.key,
            artifact=item.member.artifact,
            sha256=sha256_file(dest),
            size=dest.stat().st_size,
        )
        by_source.setdefault(item.source_key, []).append(pinned)

    if missing:
        console.print(f"[yellow]not downloaded, skipped:[/yellow] {', '.join(missing)}")
        console.print(f"[dim]run `mtl-roofs data fetch -a {area}` first[/dim]\n")

    for source_key, members in by_source.items():
        console.print(f"[bold]{source_key}:[/bold]")
        console.print(member_yaml_block(members))
        console.print()


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


# ----------------------------------------------------------------- planning
@dataclass(frozen=True, slots=True)
class _Artifact:
    """One artefact a study area needs, resolved against the manifest."""

    member: Member
    source_key: str | None
    label: str


@dataclass(frozen=True, slots=True)
class _Plan:
    """Everything a study area needs, resolved once and shared by the data commands."""

    study: StudyArea
    sources: Mapping[str, Source]
    items: list[_Artifact]


def _plan_artifacts(area: str) -> _Plan:
    """Resolve a study area to the artefacts it needs and the archives holding them.

    Both the LiDAR tiles implied by the bounding box and the reference-model tiles
    named in the area config are included; earlier the fetch command silently
    handled only the former.
    """
    study = StudyArea.load(area)
    sources = load_manifest()
    items: list[_Artifact] = []

    for tile in study.bbox.lidar_tiles():
        key = _lidar_archive_for(tile, sources)
        name = tile_member(tile)
        default = Member(key=name, artifact=name)
        member = sources[key].members.get(name, default) if key else default
        items.append(_Artifact(member=member, source_key=key, label="lidar"))

    for tile in study.reference_tiles:
        key = _reference_archive_for(tile, sources)
        # An unpinned reference tile still lands as CityGML: the archive member is a
        # nested ZIP, but only its .gml is kept, so the artefact name differs from
        # the member key and cannot fall back to it.
        default = Member(key=tile, artifact=f"{tile}_2016.gml")
        member = sources[key].members.get(tile, default) if key else default
        items.append(_Artifact(member=member, source_key=key, label="reference"))

    return _Plan(study=study, sources=sources, items=items)


def _retrieve(item: _Artifact, source: Source, raw: Path, settings: Settings) -> None:
    """Fetch one artefact out of its remote archive into ``raw``."""
    archive = RemoteZip(source.url, settings.user_agent)
    zip_member = archive.member(item.member.key)
    console.print(f"[dim]...[/dim]   {item.member.artifact} from {source.key}")

    if item.label == "lidar":
        archive.extract_member(zip_member, raw / item.member.artifact)
        return

    # The reference model nests one ZIP per tile; stream it, keep the CityGML,
    # discard the textures that make up most of its bulk.
    nested = raw / f"{item.member.key}.zip.tmp"
    try:
        archive.extract_member(zip_member, nested)
        extracted = extract_citygml(nested, raw)
        if extracted.name != item.member.artifact:
            extracted.replace(raw / item.member.artifact)
    finally:
        nested.unlink(missing_ok=True)


def _report_summary(unpinned: list[str], failed: list[str], area: str) -> None:
    """Print the closing summary and exit non-zero if anything failed."""
    if unpinned:
        console.print(
            f"\n[yellow]{len(unpinned)} artefact(s) are not pinned:[/yellow] {', '.join(unpinned)}"
        )
        console.print(f"[dim]pin them with `mtl-roofs data checksums -a {area}`[/dim]")
    if failed:
        console.print(
            f"\n[bold red]{len(failed)} artefact(s) failed:[/bold red] {', '.join(failed)}"
        )
        raise typer.Exit(code=1)
    console.print("\n[bold green]done[/bold green]")


# ----------------------------------------------------------------- helpers
def _lidar_archive_for(tile: str, sources: Mapping[str, Source]) -> str | None:
    """Find the manifest key of the bulk archive holding a LiDAR tile.

    Archives are keyed ``lidar2015_<xmin>-<xmax>`` by easting band.
    """
    easting = int(tile.split("-")[0])
    for key, source in sources.items():
        if not key.startswith("lidar2015_") or not source.is_archive:
            continue
        band = key.removeprefix("lidar2015_").split("-")
        # Not every lidar2015_* key is an easting band: lidar2015_tile_index is the
        # (stale) tile index CSV, and must not be parsed as one.
        if len(band) != 2 or not all(part.isdigit() for part in band):
            continue
        if int(band[0]) <= easting <= int(band[1]):
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
