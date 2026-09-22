"""(v1) Spatially blocked train/val/test splitting.

Adjacent buildings in Montreal are frequently identical row houses built by the same
developer, so a random split leaks: the model sees a neighbour of almost every test
building. Splits are therefore taken on a block grid, never per building.
"""

from __future__ import annotations


def block_id(easting: float, northing: float, block_size: float = 250.0) -> tuple[int, int]:
    """Index of the spatial block containing a coordinate.

    Args:
        easting: Projected X in metres.
        northing: Projected Y in metres.
        block_size: Block edge length in metres.

    Raises:
        ValueError: if ``block_size`` is not positive.
    """
    if block_size <= 0:
        msg = f"block_size must be positive, got {block_size}"
        raise ValueError(msg)
    return int(easting // block_size), int(northing // block_size)
