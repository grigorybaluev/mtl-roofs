# Verdun reference model — payload checks (#91)

Two facts about the city's 2016 LOD2 model were assumptions until checked on the real
Verdun files. Both were checked on **2026-10-21**, against all nine tiles
(`v_2016_gml_01_09.zip`, V01–V09, 9,001 buildings), before the building-form export
relied on either.

Reproduce:

```bash
pixi run mtl-roofs data fetch -a verdun --only reference
pixi run mtl-roofs export building-form -a verdun
pixi run mtl-roofs data fetch -a verdun-ground-sample --only lidar
pixi run python scripts/check_reference_ground.py
```

## 1. `parcelle` — empty in Verdun

**The hypothesis was that `parcelle` holds a cadastral lot number. In Verdun it holds
nothing.**

| measure | value |
|---|---|
| buildings carrying the attribute | 9,001 / 9,001 |
| buildings with a non-blank value | **0** |
| raw value on every building | `" "` (a single space) |

The attribute exists only as a placeholder, so there are no format, uniqueness or
example values to report. The export writes `parcelle = None` rather than the space. A
consumer that needs a building ↔ lot link has to make it spatially; `parcelle` cannot
cross-check that link. **It is not a Verdun quirk:** CDN–NDG tile 03 is blank too (0 of
1,109 buildings), so `docs/data-sources.md`'s "generic attributes present: `parcelle`"
means present, not populated. (The `0123456` in `tests/unit/test_reference.py` is a
synthetic fixture value.)

## 2. Ground level = lowest solid vertex + 3 m — holds

The publisher's method note says walls are extruded to 3 m below grade. Each building's
`GroundSurface` is flat (z-range median 0.00 m, P90 0.13 m) and sits at the solid's
lowest vertex on 99.96% of buildings, so the model offers one ground value per building:
**lowest vertex + 3 m** (`ground_method = lod2_minus3`).

Checked against the 2015 LiDAR's ground-class returns (class 2) in a ring 1–4 m outside
each building's outline. Sample: LiDAR tiles 299–300 × 5034–5035, the borough's dense
centre. 3,160 buildings lie wholly inside it; 3,159 had at least 10 ground returns
(median 760).

**Decision rule, fixed before running:** a median |difference| ≥ 0.5 m means the export
takes its ground from LiDAR instead.

| model − LiDAR | signed median | \|d\| median | \|d\| P90 | share \|d\| ≥ 0.5 m |
|---|---:|---:|---:|---:|
| vs ring **5th percentile** (downhill grade) | +0.09 m | **0.10 m** | **0.22 m** | 1.3% |
| vs ring median | −0.08 m | 0.09 m | 1.28 m | 17.1% |

**Verdict: keep `lod2_minus3`.** The model's ground agrees with the *downhill* grade
around a building to about a decimetre. The wider spread against the ring median is the
slope across a building's surroundings, not model error. So the exported heights are
measured from the lowest adjacent ground, which is the conservative reading for a height
limit. The ten largest disagreements (2.4–6.2 m) are nine where the model sits *below* the
LiDAR and one above; six are in tile V01. The script lists them; they are not
explained yet.

## 3. Two more traps found on the way

- **`Groupe<n>` ids restart in every tile.** 88 ids occur in more than one tile, and 87
  of those are different buildings (different heights and footprints). Within a
  single-tile area this never shows; across a borough, `gml:id` is not a key. The export
  qualifies every id as `<tile>:<gml:id>` and raises if that is ever not unique. **`data
  load -a verdun` fails** on the same collision (`UniqueViolation` on
  `reference_buildings`), because its key is `(area, gml_id)`. That is left for a
  separate change: the evaluation's building mapping (ADR 0005) is keyed on the same id.
- **Four id formats coexist** in Verdun: `V0100001`-style (4,555), `V07_2016_01744`-style
  (3,738), `V01_00001`-style (182) and `Groupe…` (526). None is the numeric city id
  CDN–NDG uses.
- **Degenerate roof rings.** One V04 building has two roof rings of fewer than four
  vertices; `roof_outline` now skips them instead of raising.

## Output, for the record

`data/processed/verdun/building_form.parquet`: 9,001 rows; 8,996 with a roof (5 have
none and are kept with null geometry). Footprint median 131 m² (P10 78, P90 245);
height to the highest roof vertex median 9.3 m (P10 7.5, P90 13.0); roofs 68.1% flat,
22.4% pitched, 9.5% mixed.
