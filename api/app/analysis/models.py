from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ImageQuality(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rating: Literal["excellent", "good", "fair", "poor"]
    summary: str = Field(min_length=1, max_length=1200)
    confidence: float = Field(ge=0, le=1)


class VisualObservations(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=1, max_length=1200)
    background: str = Field(min_length=1, max_length=1200)
    stars: str = Field(min_length=1, max_length=1200)
    noise: str = Field(min_length=1, max_length=1200)
    color: str = Field(min_length=1, max_length=1200)


class AnalysisIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    severity: Literal["low", "medium", "high"]
    evidence: str = Field(min_length=1, max_length=1000)
    recommendation: str = Field(min_length=1, max_length=1200)


class ProcessingStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order: int = Field(ge=1, le=20)
    step: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1, max_length=600)
    guidance: str = Field(min_length=1, max_length=1200)


class ProfessionalAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overview: str = Field(min_length=1, max_length=2000)
    image_quality: ImageQuality
    observations: VisualObservations
    issues: list[AnalysisIssue] = Field(max_length=12)
    workflow: list[ProcessingStep] = Field(min_length=1, max_length=12)
    caveats: list[str] = Field(min_length=1, max_length=8)
