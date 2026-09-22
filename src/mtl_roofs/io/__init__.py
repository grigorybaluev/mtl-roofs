"""Readers for the three open datasets, and PostGIS access.

The city distributes LiDAR and the reference model only as multi-gigabyte ZIP
archives. :mod:`mtl_roofs.io.remotezip` reads their central directories over HTTP
range requests so a single 1 km tile costs a few hundred megabytes instead of 20 GB.
"""

from mtl_roofs.io.remotezip import RemoteZip, ZipMember

__all__ = ["RemoteZip", "ZipMember"]
