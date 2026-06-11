from typing import Annotated

from pydantic import BaseModel, Field


class HduSummary(BaseModel):
    index: int
    name: str
    kind: str
    shape: list[int] | None
    dtype: str | None
    supported: bool


class BasicStatistics(BaseModel):
    minimum: float
    maximum: float
    mean: float
    median: Annotated[
        float,
        Field(
            description=(
                "Exact for up to 100,000 finite pixels; otherwise a deterministic median "
                "estimate from 100,000 evenly sampled finite pixels."
            )
        ),
    ]
    standard_deviation: float
    finite_pixel_count: int


class FitsInspection(BaseModel):
    hdus: list[HduSummary]
    selected_hdu: HduSummary
    statistics: BasicStatistics
    header: dict[str, str | int | float | bool]
