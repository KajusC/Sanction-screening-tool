from sanction_check.helpers.log import log
from sanction_check.matching.scoring import has_country_match, has_registration_number_match
from sanction_check.schemas.candidate_schema import ScoredCandidate
from sanction_check.schemas.decision_schema import Status
from sanction_check.schemas.screening_schema import ScreeningInput

STATUS_RANK: dict[Status, int] = {"clear": 0, "review": 1, "likely_hit": 2}


def choose_stricter_status(first: Status, second: Status) -> Status:
    return max(first, second, key=STATUS_RANK.__getitem__)


def is_vector_review_candidate(candidate: ScoredCandidate, vector_review_threshold: float) -> bool:
    return candidate.vector_similarity is not None and candidate.vector_similarity >= vector_review_threshold


def require_review_for_high_score(status: Status, best_score: float | None, hit_threshold: float) -> Status:
    if best_score is None or best_score < hit_threshold:
        return status

    return choose_stricter_status(status, "review")


def apply_registration_number_rule(
    status: Status, candidates: list[ScoredCandidate], supplier: ScreeningInput
) -> Status:
    for candidate in candidates:
        if not has_registration_number_match(candidate):
            continue

        minimum_status: Status = "likely_hit" if has_country_match(candidate, supplier) else "review"
        status = choose_stricter_status(status, minimum_status)

    return status


def apply_status_rules(
    status: Status, candidates: list[ScoredCandidate], supplier: ScreeningInput, hit_threshold: float
) -> Status:
    registration_status = apply_registration_number_rule(status, candidates, supplier)
    if registration_status != status:
        log.info("registration number rule raised status %s to %s", status, registration_status)

    best_score = candidates[0].score if candidates else None
    final_status = require_review_for_high_score(registration_status, best_score, hit_threshold)
    if final_status != registration_status:
        log.info("high score rule raised status %s to %s", registration_status, final_status)

    return final_status
