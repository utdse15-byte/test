"""Shared base for Manju's human-editable Pydantic models."""

from pydantic import BaseModel, ConfigDict


class ManjuModel(BaseModel):
    model_config = ConfigDict(extra="allow", validate_assignment=True)
