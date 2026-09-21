"""Additive coverage explanations; absent counts are never zero."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BudgetGap(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    reason: Literal["max_pages", "max_resources", "max_depth"]
    count: int = Field(gt=0)
    limit: int = Field(ge=0)
    samples: list[str] = Field(default_factory=list, max_length=3)


class CoverageDetails(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal[1] = 1
    items: list[BudgetGap] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def unique_reasons(self):
        if len({item.reason for item in self.items}) != len(self.items):
            raise ValueError("Coverage reasons must be unique.")
        return self


class CoverageGapView(BaseModel):
    reason: str
    context: str | None = None
    count: int | None = Field(default=None, ge=0)
    samples: list[str] = Field(default_factory=list)
    summary: str
    next_step: str | None = None
