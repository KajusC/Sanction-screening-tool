from datetime import datetime
from typing import Literal

from pydantic import BaseModel

SearchMethod = Literal["reg_no", "trigram", "phonetic", "vector"]
EntityTypeName = Literal["Organization", "Company", "LegalEntity", "PublicBody", "Person"]


class CandidateName(BaseModel):
    name_raw: str
    name_norm: str


class UnscoredCandidate(BaseModel):
    id: str
    name: str
    entity_type: EntityTypeName
    countries: list[str]
    datasets: list[str]
    programs: list[str]
    sanctions: list[str]
    last_change: datetime
    names: list[CandidateName]
    search_methods: list[SearchMethod]
    vector_similarity: float | None


class NameScore(BaseModel):
    name: CandidateName
    token_set: float
    jaro_winkler: float
    space_free: float | None
    score: float


class ScoredCandidate(UnscoredCandidate):
    score: float
    reasons: list[str]
