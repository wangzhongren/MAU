"""Typed contracts exchanged between MAUs."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Status(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class ArtifactRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    uri: str = Field(min_length=1, description="Filesystem path, object URI, or content URI")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    description: str = ""
    media_type: str = "application/octet-stream"
    size_bytes: int = Field(ge=0)


class BaseHandoff(BaseModel):
    """Small, serializable boundary object; large payloads belong in artifacts."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1)
    status: Status
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    open_issues: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

