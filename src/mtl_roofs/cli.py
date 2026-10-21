"""Command-line interface.

``mtl-roofs <command>``: ``data``, ``reconstruct``, ``evaluate``, ``export``, ``report``.
``export`` has two subcommands: ``tiles`` (not implemented) and ``building-form``.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated
from urllib.request import Request, urlopen

import typer
from rich.console import Console
from rich.table import Table

from mtl_roofs import CRS_EPSG, __version__
from mtl_roofs.config import Settings, StudyArea
from mtl_roofs.io.lidar import tile_member
from mtl_roofs.io.reference import extract_citygml
from mtl_roofs.io.remotezip import RemoteZip, ZipMember
from mtl_roofs.manifest import (
    ChecksumMismatchError,
    Member,
    Source,
    load_manifest,
    member_yaml_block,
    sha256_file,
    verify_or_raise,
)

if TYPE_CHECKING:
    from mtl_roofs.evaluation.evaluate import FootprintOutcome

app = typer.Typer(
    name="mtl-roofs",
    help="Reconstruct LOD2 building roofs for Montreal from open aerial LiDAR.",
    no_args_is_help=True,
    add_completion=False,
)
data_app = typer.Typer(name="data", help="Fetch and verify source data.", no_args_is_help=True)
app.add_typer(data_app)

console = Console()

#: Artefact kinds a study area resolves to; ``--only`` narrows the data commands to some.
ARTIFACT_KINDS = ("lidar", "reference", "footprints")
_OnlyOption = Annotated[
    list[str] | None,
    typer.Option(
        "--only",
        help="Restrict to these artefact kinds (lidar, reference, footprints); repeatable.",
    ),
]


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
    only: _OnlyOption = None,
) -> None:
    """Show what `data fetch` would download for an area, without downloading it."""
    plan = _plan_artifacts(area, only)
    settings = Settings()

    table = Table("kind", "source", "artefact", "size", "pinned", title=f"fetch plan for {area}")
    total = 0
    for item in plan.items:
        if item.source_key is None:
            table.add_row(item.label, "[red]not in manifest[/red]", item.member.artifact, "-", "-")
            continue
        source = plan.sources[item.source_key]
        size = _member_size(source.url, item, settings) if source.is_archive else source.size
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
    only: _OnlyOption = None,
) -> None:
    """Download and verify every source a study area needs.

    Tiles are extracted from the city's bulk archives over HTTP range requests, so an
    area costs a few hundred megabytes per tile rather than the 20 GB of the enclosing
    archive.

    Every artefact is checked against its pinned SHA-256. An existing file that fails
    verification is re-downloaded once; if it still fails, the command exits non-zero
    rather than letting a corrupt tile into the pipeline.

    ``--only reference --only footprints`` skips the LiDAR, for consumers of the
    reference model alone: a whole borough's LiDAR is tens of gigabytes.
    """
    plan = _plan_artifacts(area, only)
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
    only: _OnlyOption = None,
) -> None:
    """Re-check already-downloaded artefacts against the manifest, without network."""
    plan = _plan_artifacts(area, only)
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
        if plan.sources[source_key].is_archive:
            console.print(member_yaml_block(members))
        else:
            (pinned,) = members
            console.print(f'    sha256: "{pinned.sha256}"\n    size: {pinned.size}')
        console.print()


@data_app.command("load")
def data_load(
    area: Annotated[str, typer.Option("--area", "-a", help="Study area name.")],
) -> None:
    """Load an area's footprints and reference model into PostGIS.

    Reads the artefacts `data fetch` left in data/raw/<area>/ and replaces the area's
    rows. Footprints are those intersecting the area's bbox; reference buildings are
    every building of the area's reference tiles. Run `data verify` first if the files
    may have changed. Needs `pixi run db-up`.
    """
    from mtl_roofs.io.footprints import load_footprints
    from mtl_roofs.io.postgis import connect, load_area
    from mtl_roofs.io.reference import iter_buildings, qualify_grouped, tile_of

    plan = _plan_artifacts(area)
    settings = Settings()
    raw = settings.raw_dir / plan.study.name
    paths = {
        label: [raw / i.member.artifact for i in plan.items if i.label == label]
        for label in ("footprints", "reference")
    }
    missing = [p.name for ps in paths.values() for p in ps if not p.exists()]
    if missing:
        console.print(f"[red]not downloaded:[/red] {', '.join(missing)}")
        console.print(f"[dim]run `mtl-roofs data fetch -a {area}` first[/dim]")
        raise typer.Exit(code=1)

    bbox = plan.study.bbox
    (footprints_zip,) = paths["footprints"]
    footprints = load_footprints(footprints_zip, bbox=(bbox.xmin, bbox.ymin, bbox.xmax, bbox.ymax))
    buildings = (
        b for gml in paths["reference"] for b in qualify_grouped(iter_buildings(gml), tile_of(gml))
    )
    with connect(settings) as conn:
        counts = load_area(conn, plan.study.name, footprints, buildings)

    table = Table("table", "rows", "of which")
    table.add_row(
        "footprints", str(counts.footprints), f"{counts.lidar_derived_footprints} LiDAR-derived"
    )
    table.add_row(
        "reference_buildings",
        str(counts.reference_buildings),
        f"{counts.grouped_buildings} Groupe*",
    )
    table.add_row("reference_roof_surfaces", str(counts.roof_surfaces), "")
    console.print(table)

    expected = plan.study.expected_buildings
    if expected is not None and counts.reference_buildings != expected:
        console.print(
            f"[bold red]expected {expected} reference buildings, loaded "
            f"{counts.reference_buildings}[/bold red]"
        )
        raise typer.Exit(code=1)
    console.print("[bold green]done[/bold green]")


@app.command()
def reconstruct(
    area: Annotated[str, typer.Option("--area", "-a", help="Study area name.")],
    force: Annotated[
        bool, typer.Option("--force", help="Discard earlier results and start over.")
    ] = False,
    limit: Annotated[
        int | None, typer.Option("--limit", help="Reconstruct at most this many footprints.")
    ] = None,
) -> None:
    """Reconstruct every footprint of a study area into PostGIS and a CSV.

    Needs `data load` (footprints in PostGIS) and the area's LiDAR tiles. Resumable:
    footprints that already have a row are skipped. Every footprint gets a row with
    its failure-log status, and an exception on one is recorded as `error` without
    stopping the run. The run log, with wall-clock time and peak memory, is written
    to data/processed/<area>/runs/.
    """
    import time

    from rich.progress import BarColumn, MofNCompleteColumn, Progress, TimeRemainingColumn

    from mtl_roofs.batch import (
        USED_CLASSES,
        RunLog,
        beyond_coverage,
        covering_bbox,
        lidar_paths,
        new_run_id,
        peak_memory_mb,
        process,
        write_csv,
        write_log,
    )
    from mtl_roofs.io.lidar import PointIndex
    from mtl_roofs.io.postgis import (
        RECONSTRUCTION_COLUMNS,
        area_footprints,
        clear_reconstructions,
        completed_footprints,
        connect,
        init_schema,
        reconstruction_rows,
        save_reconstruction,
    )
    from mtl_roofs.pipeline import PipelineParams, Reconstruction

    started = time.perf_counter()
    study = StudyArea.load(area)
    settings = Settings()
    params = PipelineParams(planes=study.planes, topology=study.topology)
    run = RunLog(run_id=new_run_id(), area=area)
    out = settings.processed_dir / area

    # Autocommit: each footprint is committed as it is saved, so a run is resumable.
    with connect(settings, autocommit=True) as conn:
        init_schema(conn)
        footprints = area_footprints(conn, area)
        if not footprints:
            console.print(
                f"[red]no footprints for {area}[/red]; run `mtl-roofs data load -a {area}`"
            )
            raise typer.Exit(code=1)
        if force:
            clear_reconstructions(conn, area)
        done = completed_footprints(conn, area)
        todo = [f for f in footprints if f[0] not in done][:limit]
        run.n_footprints, run.n_skipped = len(footprints), len(done)
        console.print(
            f"{area}: {len(footprints)} footprints, {len(done)} already done, "
            f"{len(todo)} to reconstruct"
        )

        if todo:
            tiles = study.bbox.lidar_tiles()
            paths = lidar_paths(settings.raw_dir / area, tiles)
            run.beyond_coverage = beyond_coverage(todo, tiles)
            if run.beyond_coverage:
                console.print(
                    f"[yellow]{len(run.beyond_coverage)} footprint(s) reach outside the "
                    "area's LiDAR tiles[/yellow]; listed in the run log"
                )
            bbox = covering_bbox(todo)
            load_start = time.perf_counter()
            with console.status("reading LiDAR ..."):
                index = PointIndex.from_las(paths, bbox, USED_CLASSES)
            run.load_seconds = time.perf_counter() - load_start
            run.n_points = len(index)
            console.print(f"{len(index):,} points in {run.load_seconds:.0f} s")

            def save(footprint_id: str, result: Reconstruction, seconds: float) -> None:
                save_reconstruction(conn, area, footprint_id, result, seconds, run.run_id)

            columns = (BarColumn(), MofNCompleteColumn(), TimeRemainingColumn())
            with Progress(*Progress.get_default_columns()[:1], *columns, console=console) as bar:
                task = bar.add_task("reconstructing", total=len(todo))
                counts = process(todo, index, params, save, on_done=lambda *_: bar.advance(task))
            run.n_processed = sum(counts.values())
            run.status_counts = dict(sorted(counts.items()))

        write_csv(
            out / "reconstructions.csv", RECONSTRUCTION_COLUMNS, reconstruction_rows(conn, area)
        )

    run.seconds = time.perf_counter() - started
    run.peak_memory_mb = peak_memory_mb()
    write_log(out / "runs" / f"{run.run_id}.json", run)

    table = Table("status", "footprints", title=f"run {run.run_id}")
    for status, n in run.status_counts.items():
        table.add_row(status, str(n))
    console.print(table)
    console.print(
        f"{run.n_processed} reconstructed, {run.n_skipped} skipped in {run.seconds:.0f} s, "
        f"peak memory {run.peak_memory_mb:.0f} MB; rows in {out / 'reconstructions.csv'}"
    )


@app.command()
def evaluate(
    area: Annotated[str, typer.Option("--area", "-a")] = "",
    smoke: Annotated[bool, typer.Option("--smoke", help="Run on tests/fixtures only.")] = False,
    fixtures: Annotated[Path, typer.Option("--fixtures")] = Path("tests/fixtures"),
    out: Annotated[Path, typer.Option("--out")] = Path(".eval-smoke"),
) -> None:
    """Evaluate reconstructions against the reference model.

    With ``--smoke``, reconstruct and score the fixtures and write ``report.md``,
    ``metrics.json`` and ``buildings.csv`` to ``--out`` for the eval-smoke check. It
    exits 1 if a headline metric regressed beyond the baseline's tolerance. While the
    baseline is ``null`` it reports without gating.
    """
    from mtl_roofs.evaluation.smoke import run_smoke

    if not smoke:
        if not area:
            console.print("[red]give --area, or --smoke for the fixtures[/red]")
            raise typer.Exit(code=2)
        _evaluate_area(area)
        return

    result = run_smoke(fixtures, out)
    console.print((out / "report.md").read_text())
    if result.failed:
        raise typer.Exit(code=1)


export_app = typer.Typer(
    name="export", help="Write data products for an area.", no_args_is_help=True
)
app.add_typer(export_app)


@export_app.command("tiles")
def export_tiles(
    area: Annotated[str, typer.Option("--area", "-a")],
) -> None:
    """Export a 3D Tiles tileset for the viewer."""
    raise NotImplementedError


@export_app.command("building-form")
def export_building_form(
    area: Annotated[str, typer.Option("--area", "-a", help="Study area name.")],
    out: Annotated[
        Path | None, typer.Option("--out", help="Parquet path; default data/processed/.")
    ] = None,
) -> None:
    """Write one row per reference building -- outline, ground, heights, roof form.

    Reads the area's CityGML tiles from data/raw/<area>/ directly; no database and no
    LiDAR needed (`data fetch -a <area> --only reference`). See
    `mtl_roofs.export.building_form` for the columns.
    """
    from mtl_roofs.export.building_form import write_building_form

    plan = _plan_artifacts(area, ["reference"])
    settings = Settings()
    raw = settings.raw_dir / plan.study.name
    gmls = [raw / i.member.artifact for i in plan.items]
    missing = [g.name for g in gmls if not g.exists()]
    if not gmls or missing:
        console.print(f"[red]not downloaded:[/red] {', '.join(missing) or 'no reference tiles'}")
        console.print(f"[dim]run `mtl-roofs data fetch -a {area} --only reference` first[/dim]")
        raise typer.Exit(code=1)
    dest = out or settings.processed_dir / plan.study.name / "building_form.parquet"
    n = write_building_form(gmls, dest)
    console.print(f"[bold green]{n} buildings[/bold green] from {len(gmls)} tiles -> {dest}")


@app.command()
def report(
    area: Annotated[str, typer.Option("--area", "-a", help="Study area name.")],
    gallery: Annotated[
        int, typer.Option("--gallery", help="Worst buildings to render; 0 for none.")
    ] = 20,
) -> None:
    """Write the breakdown report and the failure gallery.

    Reads the per-building rows `evaluate -a` stored. Writes breakdown.md and
    breakdown.csv to data/processed/<area>/report/, and the gallery of the worst
    buildings by RMSE, with a diagnosis each, to report/gallery/.
    """
    from mtl_roofs.evaluation.breakdown import write_breakdown
    from mtl_roofs.evaluation.gallery import write_gallery
    from mtl_roofs.io.postgis import (
        area_buildings,
        area_outcomes,
        connect,
        load_building_results,
    )

    settings = Settings()
    out = settings.processed_dir / area / "report"
    with connect(settings) as conn:
        rows = load_building_results(conn, area)
        if not rows:
            console.print(
                f"[red]no evaluation for {area}[/red]; run `mtl-roofs evaluate -a {area}`"
            )
            raise typer.Exit(code=1)
        md, csv_path = write_breakdown(rows, out, f"Evaluation of {area}")
        console.print(f"wrote {md} and {csv_path}")
        if gallery <= 0:
            return
        outcomes = area_outcomes(conn, area)
        buildings = {b.gml_id: b for b in area_buildings(conn, area)}
    outcomes = _with_roof_points(outcomes, StudyArea.load(area), settings)
    with console.status(f"rendering the {gallery} worst buildings ..."):
        page = write_gallery(
            rows,
            {o.footprint_id: o for o in outcomes},
            buildings,
            out / "gallery",
            n=gallery,
            title=f"Failure gallery: {area}",
        )
    console.print(f"wrote {page}")


def _with_roof_points(
    outcomes: list[FootprintOutcome], study: StudyArea, settings: Settings
) -> list[FootprintOutcome]:
    """The footprints with their clipped LiDAR roof points, read from the area's tiles."""
    from rich.progress import Progress

    from mtl_roofs.batch import USED_CLASSES, footprint_points, lidar_paths
    from mtl_roofs.geometry.clip import clip_footprint
    from mtl_roofs.io.lidar import PointIndex

    bbox = study.bbox
    with console.status("reading LiDAR ..."):
        index = PointIndex.from_las(
            lidar_paths(settings.raw_dir / study.name, bbox.lidar_tiles()),
            (bbox.xmin - 50, bbox.ymin - 50, bbox.xmax + 50, bbox.ymax + 50),
            USED_CLASSES,
        )
    result = []
    with Progress(console=console) as bar:
        task = bar.add_task("clipping roof points", total=len(outcomes))
        for o in outcomes:
            points = footprint_points(index, o.polygon)
            roof = clip_footprint(points, o.polygon).roof.xyz if len(points) else points.xyz
            result.append(dataclasses.replace(o, roof_points=roof))
            bar.advance(task)
    return result


def _evaluate_area(area: str) -> None:
    """Score every reference building of an area and store the rows (`evaluate -a`)."""
    import json
    import time

    from mtl_roofs.batch import write_csv
    from mtl_roofs.evaluation.evaluate import evaluate as score
    from mtl_roofs.evaluation.evaluate import summarise
    from mtl_roofs.evaluation.report import BuildingResult
    from mtl_roofs.io.postgis import area_buildings, area_outcomes, connect, save_building_results

    started = time.perf_counter()
    study = StudyArea.load(area)
    settings = Settings()
    out = settings.processed_dir / area
    with connect(settings, autocommit=True) as conn:
        outcomes = area_outcomes(conn, area)
        buildings = area_buildings(conn, area)
        missing = [o.footprint_id for o in outcomes if o.status == "not-run"]
        if missing:
            console.print(
                f"[red]{len(missing)} footprints have no reconstruction[/red]; "
                f"run `mtl-roofs reconstruct -a {area}` first"
            )
            raise typer.Exit(code=1)

        # The temporal flag compares the LiDAR itself with the reference, so the roof
        # points are clipped again; the reconstruction is not re-run.
        outcomes = _with_roof_points(outcomes, study, settings)

        with console.status(f"scoring {len(buildings)} reference buildings ..."):
            rows = score(outcomes, buildings)
        save_building_results(conn, area, rows)

    names = [f.name for f in dataclasses.fields(BuildingResult)]
    write_csv(
        out / "buildings.csv",
        names,
        ([getattr(r, name) for name in names] for r in rows),
    )
    overall = summarise(rows)
    by_type = {
        t: summarise([r for r in rows if r.roof_type == t]) for t in ("flat", "mixed", "pitched")
    }
    (out / "metrics.json").write_text(
        json.dumps({"overall": overall, "by_roof_type": by_type}, indent=2) + "\n"
    )

    table = Table("metric", "value")
    for key in (
        "vertical_rmse_m",
        "median_building_rmse_m",
        "coverage",
        "orientation_error_median_deg",
        "orientation_error_p90_deg",
        "face_count_agreement",
        "match_rate",
        "buildings_reconstructed",
        "n_buildings",
        "n_no_reference",
    ):
        value = overall[key]
        table.add_row(key, "—" if value is None else f"{value:.4g}")
    console.print(table)
    console.print("statuses: " + ", ".join(f"{k} {v}" for k, v in overall["status_counts"].items()))
    console.print(f"done in {time.perf_counter() - started:.0f} s; rows in {out / 'buildings.csv'}")


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


def _plan_artifacts(area: str, only: list[str] | None = None) -> _Plan:
    """Resolve a study area to the artefacts it needs and the archives holding them.

    Included: the LiDAR tiles implied by the bounding box, the reference-model tiles
    named in the area config, and the footprint layer. Earlier the fetch command
    silently handled only the LiDAR, and later left out the footprints, which are
    the pipeline's primary input. ``only`` narrows that to some artefact kinds; an
    unknown kind raises rather than quietly selecting nothing.
    """
    if only:
        unknown = sorted(set(only) - set(ARTIFACT_KINDS))
        if unknown:
            msg = f"unknown artefact kind(s) {unknown}; choose from {list(ARTIFACT_KINDS)}"
            raise typer.BadParameter(msg, param_hint="--only")
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

    # City-wide, so every area needs the same file. It is small enough (100 MB) to
    # download whole, and is pinned at the source level rather than per member.
    footprints = sources.get(FOOTPRINTS_SOURCE)
    if footprints is None:
        missing = Member(key=FOOTPRINTS_SOURCE, artifact=FOOTPRINTS_SOURCE)
        items.append(_Artifact(member=missing, source_key=None, label="footprints"))
    else:
        name = footprints.url.rsplit("/", 1)[-1]
        member = Member(key=name, artifact=name, sha256=footprints.sha256, size=footprints.size)
        items.append(_Artifact(member=member, source_key=FOOTPRINTS_SOURCE, label="footprints"))

    if only:
        items = [i for i in items if i.label in only]
    return _Plan(study=study, sources=sources, items=items)


def _retrieve(item: _Artifact, source: Source, raw: Path, settings: Settings) -> None:
    """Fetch one artefact, from its remote archive or as a whole file, into ``raw``."""
    if not source.is_archive:
        console.print(f"[dim]...[/dim]   {item.member.artifact} from {source.key}")
        _download(source.url, raw / item.member.artifact, settings.user_agent)
        return

    archive = RemoteZip(source.url, settings.user_agent)
    zip_member, nested_layout = _resolve_member(archive, item)
    console.print(f"[dim]...[/dim]   {item.member.artifact} from {source.key}")

    if not nested_layout:
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
#: Manifest key of the city-wide footprint layer (CARTO-BAT-TOIT and friends).
FOOTPRINTS_SOURCE = "footprints_2d_2016"


def _download(url: str, dest: Path, user_agent: str, chunk_size: int = 1 << 20) -> None:
    """Stream a whole file to ``dest``, via a temporary name so a partial file never lands."""
    partial = dest.with_name(dest.name + ".part")
    req = Request(url, headers={"User-Agent": user_agent})
    try:
        with urlopen(req, timeout=300.0) as resp, partial.open("wb") as out:
            while chunk := resp.read(chunk_size):
                out.write(chunk)
        partial.replace(dest)
    finally:
        partial.unlink(missing_ok=True)


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


def _resolve_member(archive: RemoteZip, item: _Artifact) -> tuple[ZipMember, bool]:
    """Find the archive member holding an artefact, and whether it is a nested ZIP.

    Two reference-model layouts exist. CDN-NDG, Outremont and the Plateau hold one
    nested ZIP per tile (``CDNNDG03.zip`` -> ``.gml`` + textures). Verdun stores each
    tile's ``V01_2016.gml`` at the top level beside its ``V01_2016_Appearance/``
    folder, so the tile key matches thousands of texture members while the CityGML
    itself is directly range-readable. A top-level member named exactly like the
    artefact wins; otherwise the member is the nested per-tile ZIP. LiDAR members
    are always read directly.
    """
    if item.label == "reference":
        direct = [m for m in archive.members() if m.name == item.member.artifact]
        if direct:
            return direct[0], False
        return archive.member(item.member.key), True
    return archive.member(item.member.key), False


def _member_size(url: str, item: _Artifact, settings: Settings) -> int | None:
    """Compressed size of an archive member, read from the remote central directory."""
    try:
        archive = RemoteZip(url, settings.user_agent)
        return _resolve_member(archive, item)[0].compressed_size
    except (KeyError, OSError):
        return None


def _human(size: int | None) -> str:
    """Format a byte count for a terminal table."""
    if size is None:
        return "-"
    if size >= 1 << 30:
        return f"{size / (1 << 30):.2f} GB"
    return f"{size / (1 << 20):.0f} MB"
