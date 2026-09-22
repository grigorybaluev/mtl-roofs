"""Configuration and study-area definitions.

A *study area* is the unit of work for the whole pipeline: it names a rectangle in
EPSG:2950 and the source tiles that cover it. Areas live in ``configs/areas/*.yaml``
so that a run is reproducible from a file rather than from command-line arguments.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Self

import yaml
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from mtl_roofs import CRS_EPSG

REPO_ROOT = Path(__file__).resolve().parents[2]
AREAS_DIR = REPO_ROOT / "configs" / "areas"


class BBox(BaseModel):
    """An axis-aligned rectangle in projected coordinates (metres)."""

    xmin: float
    ymin: float
    xmax: float
    ymax: float

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.xmax <= self.xmin or self.ymax <= self.ymin:
            msg = f"degenerate bbox: {self.xmin, self.ymin, self.xmax, self.ymax}"
            raise ValueError(msg)
        return self

    @property
    def width(self) -> float:
        """Extent along X, in metres."""
        return self.xmax - self.xmin

    @property
    def height(self) -> float:
        """Extent along Y, in metres."""
        return self.ymax - self.ymin

    @property
    def area_km2(self) -> float:
        """Area of the rectangle in square kilometres."""
        return self.width * self.height / 1e6

    def lidar_tiles(self) -> list[str]:
        """Names of the 1 km LiDAR tiles covering this bbox.

        Montreal's 2015 tiles are named ``<easting_km>-<northing_km>`` and are exactly
        1000 m square, so tile membership is pure integer arithmetic.
        """
        return [
            f"{x}-{y}"
            for x in range(int(self.xmin // 1000), int(self.xmax // 1000) + 1)
            for y in range(int(self.ymin // 1000), int(self.ymax // 1000) + 1)
        ]


class StudyArea(BaseModel):
    """A named area of interest plus the source tiles that cover it."""

    name: str
    description: str = ""
    borough: str = ""
    epsg: int = Field(default=CRS_EPSG)
    bbox: BBox
    #: Reference-model tile members, e.g. ``["CDNNDG03"]``.
    reference_tiles: list[str] = Field(default_factory=list)
    #: Expected building count, used only as a sanity check on a run.
    expected_buildings: int | None = None

    @classmethod
    def load(cls, name: str, areas_dir: Path | None = None) -> StudyArea:
        """Load a study area by name from ``configs/areas/<name>.yaml``."""
        path = (areas_dir or AREAS_DIR) / f"{name}.yaml"
        if not path.exists():
            available = sorted(p.stem for p in (areas_dir or AREAS_DIR).glob("*.yaml"))
            msg = f"unknown study area {name!r}; available: {available}"
            raise FileNotFoundError(msg)
        data: dict[str, Any] = yaml.safe_load(path.read_text())
        return cls.model_validate(data)


class Settings(BaseSettings):
    """Process-wide settings, overridable by environment or ``.env``."""

    model_config = SettingsConfigDict(env_prefix="MTL_ROOFS_", env_file=".env", extra="ignore")

    data_dir: Path = REPO_ROOT / "data"
    postgres_host: str = "localhost"
    postgres_port: int = 5433
    postgres_db: str = "mtl_roofs"
    postgres_user: str = "mtl_roofs"
    postgres_password: str = "mtl_roofs"

    #: Both city hosts reject non-browser agents with HTTP 403; see docs/data-sources.md.
    user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    )

    @property
    def raw_dir(self) -> Path:
        """Directory for untouched downloads."""
        return self.data_dir / "raw"

    @property
    def interim_dir(self) -> Path:
        """Directory for intermediate artefacts."""
        return self.data_dir / "interim"

    @property
    def processed_dir(self) -> Path:
        """Directory for pipeline outputs."""
        return self.data_dir / "processed"

    def dsn(self) -> str:
        """PostgreSQL connection string for the configured database."""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )
