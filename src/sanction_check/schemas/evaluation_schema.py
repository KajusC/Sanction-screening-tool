from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from sanction_check.schemas.decision_schema import Status
from sanction_check.schemas.screening_schema import ScreeningInput

EvalCategory = Literal[
    "confirmed", "exact", "variant", "negative", "common_name", "cross_script", "cross_script_clean"
]


class EvalTarget(BaseModel):
    entity_id: str
    supplier: ScreeningInput


class EvalCase(BaseModel):
    id: str
    category: EvalCategory
    input: ScreeningInput
    expected_status_min: Status
    expected_entity_id: str | None
    should_hide_cjk_names: bool = False


class EvalCaseResult(BaseModel):
    case: EvalCase
    status: Status
    matched_entity_id: str | None
    retry_count: int
    is_fallback: bool
    has_web_search: bool
    cost_usd: float
    latency_seconds: float


class EvalMetric(BaseModel):
    name: str
    value: float
    target: str | None
    is_passed: bool | None


class EvalCategoryReport(BaseModel):
    category: EvalCategory
    status_counts: dict[Status, int]


class EvalReport(BaseModel):
    generated_at: datetime
    llm_model: str
    metrics: list[EvalMetric]
    category_reports: list[EvalCategoryReport]
    case_results: list[EvalCaseResult]
