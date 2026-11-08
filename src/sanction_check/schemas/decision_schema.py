from typing import Literal

from pydantic import BaseModel, ConfigDict

from sanction_check.schemas.web_search_schema import WebSearchRecord

Status = Literal["clear", "review", "likely_hit"]


class LanguageModelDecision(BaseModel):
    model_config = ConfigDict(extra="ignore")

    matched_candidate_id: str | None
    status: Status
    rationale: str
    suggested_action: str


class DecisionOutcome(BaseModel):
    decision: LanguageModelDecision
    is_fallback: bool
    attempt_count: int
    cost_usd: float
    web_searches: list[WebSearchRecord]
