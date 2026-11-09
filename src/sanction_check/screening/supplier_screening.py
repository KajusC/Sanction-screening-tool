import time
from datetime import UTC, datetime

from sanction_check.helpers.log import log
from sanction_check.matching.normalize import normalize_name
from sanction_check.matching.scoring import score_candidate
from sanction_check.matching.search import find_entity_candidates, find_token_weights
from sanction_check.schemas.candidate_schema import ScoredCandidate
from sanction_check.schemas.decision_schema import DecisionOutcome
from sanction_check.schemas.screening_schema import (
    CandidateSummary,
    MatchedEntity,
    ScreeningInput,
    ScreeningMetadata,
    ScreeningResult,
)
from sanction_check.screening.language_model import PROMPT_FILE_NAME, decide_with_language_model
from sanction_check.screening.result import build_evidence
from sanction_check.screening.rules import apply_status_rules, is_vector_review_candidate
from sanction_check.settings import get_settings


def find_ranked_candidates(supplier: ScreeningInput) -> list[ScoredCandidate]:
    normalized_name = normalize_name(supplier.name)
    unscored_candidates = find_entity_candidates(supplier, normalized_name)
    token_weights = find_token_weights(normalized_name)

    candidates = [
        score_candidate(normalized_name, unscored_candidate, supplier, token_weights)
        for unscored_candidate in unscored_candidates
    ]
    ranked = sorted(candidates, key=lambda candidate: candidate.score, reverse=True)

    settings = get_settings()
    vector_review_candidates = [
        candidate
        for candidate in ranked[settings.top_k :]
        if is_vector_review_candidate(candidate, settings.vector_review_threshold)
    ]

    log.info("ranked %d candidates, %d extra from vector review", len(ranked), len(vector_review_candidates))

    return [*ranked[: settings.top_k], *vector_review_candidates]


def find_matched_candidate(
    matched_candidate_id: str | None, candidates: list[ScoredCandidate]
) -> ScoredCandidate | None:
    if matched_candidate_id is None:
        return candidates[0] if candidates else None

    return next((candidate for candidate in candidates if candidate.id == matched_candidate_id), None)


def build_metadata(outcome: DecisionOutcome, latency_seconds: float) -> ScreeningMetadata:
    return ScreeningMetadata(
        screened_at=datetime.now(UTC),
        llm_model=get_settings().llm_model,
        prompt_name=PROMPT_FILE_NAME,
        attempt_count=outcome.attempt_count,
        is_fallback=outcome.is_fallback,
        cost_usd=outcome.cost_usd,
        latency_seconds=latency_seconds,
    )


def screen_supplier(supplier: ScreeningInput, *, should_search_web: bool) -> ScreeningResult:
    started_at = time.perf_counter()
    log.info("screening supplier %r in %s", supplier.name, supplier.country)
    candidates = find_ranked_candidates(supplier)
    log.info("kept %d candidates", len(candidates))
    for candidate in candidates:
        log.debug(
            "candidate %s %.3f %r methods=%s reasons=%s",
            candidate.id,
            candidate.score,
            candidate.name,
            candidate.search_methods,
            "; ".join(candidate.reasons),
        )

    outcome = decide_with_language_model(supplier, candidates, should_search_web=should_search_web)

    status = apply_status_rules(outcome.decision.status, candidates, supplier)
    log.info("status %s (language model said %s)", status, outcome.decision.status)

    matched_candidate = (
        find_matched_candidate(outcome.decision.matched_candidate_id, candidates) if status != "clear" else None
    )

    return ScreeningResult(
        input=supplier,
        status=status,
        matched_entity=MatchedEntity.model_validate(matched_candidate, from_attributes=True)
        if matched_candidate
        else None,
        confidence=matched_candidate.score if matched_candidate else None,
        llm_confidence = outcome.decision.confidence,
        match_reasons=matched_candidate.reasons if matched_candidate else [],
        candidates=[CandidateSummary.model_validate(candidate, from_attributes=True) for candidate in candidates],
        evidence=[*(build_evidence(matched_candidate) if matched_candidate else []), *outcome.decision.evidence],
        web_searches=outcome.web_searches,
        recommended_action=outcome.decision.suggested_action,
        rationale=outcome.decision.rationale,
        meta=build_metadata(outcome, latency_seconds=time.perf_counter() - started_at),
    )
