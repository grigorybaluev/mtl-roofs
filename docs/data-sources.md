# Data sources

All facts below were verified against the live sources on **2026-09-22**, not copied from
documentation. Where a value was read out of the binary/XML payload itself, the method is
stated. Anything still unverified is marked **UNVERIFIED**.

All three datasets are published by the Ville de Montréal under
**Creative Commons Attribution 4.0 International (CC BY 4.0)** — verified via the CKAN
API field `license_id = cc-by` on each package.

## Required attribution

Reproduce in the README, the viewer UI, and any published figure:

> Contains information licensed under CC BY 4.0 from the Ville de Montréal
> (LiDAR aérien 2015; Bâtiments 3D 2016 — Maquette LOD2; Bâtiments 2D 2016).
> Portions of the 2D building layer derive from 2007 aerial photography
> © Communauté métropolitaine de Montréal.

The CMM credit is **not optional**: it appears in the `source` attribute of the
`CARTO-BAT-TOIT` records themselves (see §3).

---

## Common spatial reference

| Property | Value | How verified |
|---|---|---|
| Horizontal CRS | **EPSG:2950** — NAD83(CSRS) / MTM zone 8 | GeoTIFF GeoKey `3072 = 2950` read from a LAS VLR; `.prj` of `CARTO-BAT-TOIT` reads `NAD_1983_CRS98_MTM_8` (central meridian −73.5, false easting 304800, scale 0.9999) |
| Linear unit | metre | GeoKey `3076 = 9001` |
| Geodetic datum | NAD83(CSRS), GeoKey `2048 = 4140` | LAS VLR |
| Vertical datum | **CGVD28** (niveau moyen des mers) | dataset metadata + `reference` attribute `NAD83 SCRS / C-GVD28 (NMM)` in the footprint DBF |

> The prompt for this project assumed EPSG:32188. **That is wrong for these data.**
> 32188 is NAD83 / MTM 8; the city publishes NAD83(**CSRS**) / MTM 8 = **2950**.
> The two differ by the NAD83→NAD83(CSRS) shift (decimetre level) — enough to matter for a
> vertical RMSE reported in centimetres. Use 2950 everywhere; do not let a reader of
> `.prj` text silently pick 32188.

---

## 1. Aerial LiDAR — `lidar-aerien-2015`

- **Portal page:** https://donnees.montreal.ca/dataset/lidar-aerien-2015
- **Licence:** CC BY 4.0 · **Publisher:** Service des infrastructures du réseau routier
- **Acquisition:** 24 November – 8 December 2015. Processing stamp inside the LAS header is
  day 83 of 2016, generating software `TerraScan`.
- **Stated accuracy:** ±20 cm planimetric and altimetric.

### Verified from the payload

Read by inflating the first ~1.5 MB of member `290-5035_2015.las`:

| Property | Value |
|---|---|
| Format | LAS **1.2**, point data record format **1**, 28-byte records (uncompressed inside the ZIP) |
| Tile size | exactly **1000 m × 1000 m** (X 290000–291000, Y 5035000–5036000) |
| Points in that tile | 18,094,054 |
| **Point density** | **18.09 pts/m²**, all classes |
| Z range in that tile | 35.27 – 83.02 m |
| Tile naming | `<easting_km>-<northing_km>_2015.las` in EPSG:2950 |

**Classification codes.** Verified by decoding **every point** of tile `292-5035`
(19,496,435 points), not a sample:

| code | meaning | points | share |
|---:|---|---:|---:|
| 1 | unclassified | 6,090,660 | 31.24% |
| 2 | ground | 5,919,429 | 30.36% |
| 3 | low vegetation | 82,720 | 0.42% |
| 4 | medium vegetation | 206,414 | 1.06% |
| 5 | high vegetation | 2,369,157 | 12.15% |
| **6** | **building** | **3,758,075** | **19.28%** |
| 7 | low point (noise) | 7,755 | 0.04% |
| **28** | **undocumented** | **1,062,225** | **5.45%** |

**Class 6 is populated** — an earlier partial read of one contiguous slice of tile
`290-5035` showed no class 6 and was misleading; a full-tile decode settles it. Roof
points can be taken from class 6, though class 1 (31%) certainly also contains roof
returns, so the filter should not rely on class 6 alone.

**Class 28 is not in the city's published class list** and accounts for 5.45% of
points. It is most likely overlap/withheld points from adjacent flight lines. The
filter must decide explicitly whether to keep it rather than letting it fall through
an `else` branch; tracked by the point-filtering issue.

That tile also measures **19.50 pts/m²** over 1 km² (Z 9.9–99.1 m), against 18.09
pts/m² for tile `290-5035` — so density varies meaningfully between tiles and is
rightly a breakdown axis in the evaluation.

### Distribution — the important constraint

The per-tile download URLs advertised in the dataset's own index
(`indexlidar2015.csv`, 684 rows, pattern
`http://depot.ville.montreal.qc.ca/geomatique/lidar_aerien/2015/<tile>_2015_2-5-6.laz`)
are **all dead — every one returns HTTP 404** (tested; also tested five path variants).
The index CSV is stale. The ArcGIS download viewer is built on the same dead paths.

The only live distribution is **five bulk ZIPs**, total **119.47 GB**:

| ZIP | Tiles | Size |
|---|---:|---:|
| `LAS2015_classifiees_266-279.zip` | 164 | 26.06 GB |
| `LAS2015_classifiees_280-289.zip` | 128 | 21.56 GB |
| `LAS2015_classifiees_290-294.zip` | 108 | 20.57 GB |
| `LAS2015_classifiees_295-299.zip` | 141 | 28.47 GB |
| `LAS2015_classifiees_300-307.zip` | 143 | 22.81 GB |
| **total** | **684** | **119.47 GB** |

684 members matches the 684 rows of the stale index exactly, so coverage is complete:
X 266–307 km, Y 5028–5062 km (MTM 8).

**These ZIPs support HTTP range requests** (verified: `206 Partial Content` with
`Content-Range`). `mtl-roofs data fetch` therefore never downloads a 20 GB archive: it reads
the ZIP64 central directory from the tail, locates the wanted member, and streams only that
member's byte range, inflating on the fly. Cost per 1 km² tile is **~160–400 MB**
(mean 190 MB) instead of 20–28 GB.

**Both hosts reject the default `curl`/`urllib` user agent** — `donnees.montreal.ca` returns
`403 RBAC: access denied` and the depot returns 403. A browser `User-Agent` header is
required and must be set by the fetcher.

## 2. Reference 3D model — `batiment-3d-2016-maquette-citygml-lod2-avec-textures2`

- **Portal page:** https://donnees.montreal.ca/dataset/batiment-3d-2016-maquette-citygml-lod2-avec-textures2
- **Licence:** CC BY 4.0 · **Reference year:** 2016 (GML files written 2018)
- **Coverage — 6 boroughs:** Côte-des-Neiges–Notre-Dame-de-Grâce, Outremont,
  Plateau-Mont-Royal, Le Sud-Ouest, Ville-Marie, Verdun.
- **Formats:** CityGML 2.0 (GML), 3DM, FGDB, DWG (untextured).

### Verified from the payload

Extracted `PMR06_2016.gml`, `O01_2016.gml`, `CDNNDG03_2016.gml`:

- **CityGML 2.0**, produced by *RhinoCity / Rhinoterrain*, `gml:description = "Exported by Rhinocity"`.
- Every building carries `bldg:lod2Solid` plus semantic `bldg:RoofSurface`,
  `bldg:WallSurface`, `bldg:GroundSurface` — i.e. genuine LOD2 with typed roof geometry.
- Coordinates are EPSG:2950 metres, consistent with the LiDAR tile grid.
- **`gml:Envelope` carries no `srsName`.** The CRS is undeclared in the file and must be
  asserted externally as EPSG:2950. Readers that trust the file will produce unprojected junk.
- Building identity: `bldg:Building/@gml:id` is usually a numeric city id (e.g. `1585788`),
  but merged blocks appear as `Groupe<number>` (e.g. `Groupe9637915`). Generic attributes
  present: `parcelle` (string), `Area`, `Volume` (double).
- Walls are extruded from the roof down to 3 m below grade (per publisher's method note), so
  **wall and ground geometry are synthetic** — only the roof surfaces are measured. Evaluation
  must therefore score roofs, not whole solids.

### Distribution

Per-borough ZIPs containing one **nested ZIP per tile**; each inner ZIP holds one `.gml` plus
a texture folder. Textures dominate: for tile PMR06 the GML is **35 MB** of a **169 MB** member
(399 JPEGs make up the rest). The inner ZIPs are deflated, so they cannot be range-read
recursively — `data fetch` range-streams the whole inner member and keeps only the `.gml`.

| Borough ZIP | Total | Tiles | Per-tile member |
|---|---:|---:|---|
| `pmr_2016_gml_01_11.zip` (Plateau) | 4.78 GB | 11 | 169–770 MB |
| `o01_2016_gml_01_03.zip` (Outremont) | 1.25 GB | 3 | 241–525 MB |
| `cdnndg_2016_gml_01_12.zip` (CDN–NDG) | 4.25 GB | 12 | 207–521 MB |

### Why 2016 and not 2020

A `batiments-3d-2020-maquette-lod2-avec-textures` package also exists, but:
- it covers only **3** boroughs (Outremont, CDN–NDG, Ville-Marie) versus 6, and
- it is **5 years** after the LiDAR rather than 1.

2016 is the correct reference for a 2015 LiDAR baseline. 2020 is useful later as a
change-detection control.

### Known temporal error source

LiDAR is **Nov–Dec 2015**; the reference model is **2016** (photogrammetry, published 2018).
Buildings constructed, demolished or re-roofed in that window will produce large apparent
errors that are not reconstruction errors. The per-building failure log must be able to flag
these, and headline metrics should be reported both raw and after excluding flagged buildings.

## 3. Building footprints — `batiment-2d`

- **Portal page:** https://donnees.montreal.ca/dataset/batiment-2d
- **Licence:** CC BY 4.0 · Package metadata last modified 2025-02-24; files stamped 2023-08-17.
- **Download (SHP):** `batiments_2d_2016_arrondissements.zip`, **100,284,823 bytes**
  SHA-256 `4bdc03865e47962b2b284c301b275189caea0e24e17f555934901b82feedd7f9`
  (a GPKG version of the same content is 154 MB).
- The layer is a **2.5D representation of building roofs**, not wall footprints — which is
  exactly the right input for roof reconstruction.

### Layers

| Layer | Geometry | Records | Use |
|---|---|---:|---|
| `CARTO-BAT-TOIT` | Polygon | 237,810 | **primary footprint** — roof outline |
| `CARTO-BAT-DETAIL` | Polyline | 1,693,394 | roof detail lines (ridges, breaks) — a strong prior for the topology solver and an independent check on detected ridges |
| `CARTO-BAT-COTE` | Point | 362,014 | spot elevations (`elevation` field) |
| `CARTO-BAT-SUPER-STRUCTURE` | Polyline | — | rooftop superstructures |
| `CARTO-BAT-CONSTRUCTION`, `CARTO-BAT-RUINE` | Polygon | small | under construction / ruins |

### Verified attributes and the identity problem

`CARTO-BAT-TOIT` fields: `calque, projection, reference, EQM_plani, EQM_alti, MAJ, methode,
source, producteur, superficie, version`.

**There is no building identifier of any kind.** There is consequently **no join key**
between the footprints and the CityGML `gml:id`. Building matching must be spatial
(centroid containment plus maximum IoU, with an explicit tie-break rule). This is a design
decision with real alternatives and gets an ADR before implementation; it also means a
matching failure rate is itself a metric to report, not an implementation detail to hide.

Provenance is mixed and recorded per feature:
- most records: `methode = photogrammétrie`, `source = photo aérienne 2007, (C) Communauté
  métropolitaine de Montréal`, `MAJ = juillet 2015`, `EQM_plani = ± 30 à ± 40 cm`;
- some records: `methode = modélisation automatique`, `source = LiDAR aérien 2015, Ville de
  Montréal`, `MAJ = novembre 2015`.

The second group is **derived from the same LiDAR this project reconstructs from**. Those
footprints are not independent of the input, and buildings falling in that group should be
flagged so the evaluation can report them separately. `EQM_alti` is `ND` (not determined)
throughout — the layer carries no stated vertical accuracy.

---

## Retrieval and integrity

`data/manifest.yaml` pins each source URL with its expected SHA-256 and byte size;
`mtl-roofs data fetch --area <name>` resolves the study area to the tiles it needs, streams
only those members, and verifies them. Checksums for whole-file downloads are recorded at
first fetch; for range-extracted members the checksum is taken over the extracted member,
since the enclosing 20 GB archive is never materialised.
