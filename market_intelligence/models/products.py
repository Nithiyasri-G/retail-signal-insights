from __future__ import annotations

from pydantic import Field, model_validator

from .base import StrictModel


class Product(StrictModel):
    sku: str = Field(min_length=1)
    name: str = Field(min_length=1)
    category: str = Field(min_length=1)
    case_pack: int = Field(gt=0)
    moq: int = Field(ge=0)
    sellable_unit: str = "EA"

    @model_validator(mode="after")
    def validate_moq_pack_compatibility(self) -> "Product":
        if self.moq and self.moq % self.case_pack != 0:
            raise ValueError("moq must be a whole multiple of case_pack")
        return self
