from datetime import datetime

import pycountry
from pydantic import BaseModel, Field, field_validator

from sanction_check.schemas.candidate_schema import EntityTypeName, SearchMethod
from sanction_check.schemas.decision_schema import Evidence, Status
from sanction_check.schemas.web_search_schema import WebSearchRecord

MIN_NAME_LENGTH = 2
MAX_NAME_LENGTH = 200


class ScreeningInput(BaseModel):
    name: str = Field(min_length=MIN_NAME_LENGTH, max_length=MAX_NAME_LENGTH)
    country: str
    reg_no: str | None = None

    @field_validator("country")
    @classmethod
    def validate_country(cls, country: str) -> str:
        country_upper = country.upper()
        if pycountry.countries.get(alpha_2=country_upper) is None:
            raise ValueError(f"unknown country: {country!r}")

        return country_upper

    @field_validator("reg_no")
    @classmethod
    def empty_registration_number_to_none(cls, registration_number: str | None) -> str | None:
        return registration_number or None


class MatchedEntity(BaseModel):
    id: str
    name: str
    entity_type: EntityTypeName
    datasets: list[str]
    programs: list[str]


class CandidateSummary(BaseModel):
    id: str
    name: str
    score: float
    search_methods: list[SearchMethod]
    reasons: list[str]


class ScreeningMetadata(BaseModel):
    screened_at: datetime
    llm_model: str
    prompt_name: str
    attempt_count: int
    is_fallback: bool
    cost_usd: float


class ScreeningResult(BaseModel):
    input: ScreeningInput
    status: Status
    matched_entity: MatchedEntity | None
    llm_confidence: float | None
    confidence: float | None
    match_reasons: list[str]
    candidates: list[CandidateSummary]
    evidence: list[Evidence]
    web_searches: list[WebSearchRecord]
    recommended_action: str
    rationale: str
    meta: ScreeningMetadata
