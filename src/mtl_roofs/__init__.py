"""LOD2 roof reconstruction for Montreal from open aerial LiDAR.

The package is organised as a pipeline: :mod:`mtl_roofs.io` reads LiDAR, footprints
and the reference CityGML model; :mod:`mtl_roofs.geometry` reconstructs roof solids;
:mod:`mtl_roofs.evaluation` scores them against the reference; :mod:`mtl_roofs.export`
writes 3D Tiles for the viewer.

All geometry is in EPSG:2950 (NAD83(CSRS) / MTM zone 8), metres, with CGVD28
elevations. See ``docs/data-sources.md`` for why, and ``CLAUDE.md`` for the
conventions every module is expected to honour.
"""

__version__ = "0.0.0"

#: Horizontal CRS every dataset is reprojected to before processing.
CRS_EPSG = 2950

#: Vertical datum of both the LiDAR and the reference model.
VERTICAL_DATUM = "CGVD28"

__all__ = ["CRS_EPSG", "VERTICAL_DATUM", "__version__"]
