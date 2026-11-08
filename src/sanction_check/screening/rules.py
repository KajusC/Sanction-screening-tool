from sanction_check.helpers.log import log
from sanction_check.matching.scoring import has_country_match, has_registration_number_match
from sanction_check.schemas.candidate_schema import ScoredCandidate
from sanction_check.schemas.decision_schema import Status
from sanction_check.schemas.screening_schema import ScreeningInput
from sanction_check.settings import get_settings

STATUS_RANK: dict[Status, int] = {"clear": 0, "review": 1, "likely_hit": 2}


def choose_stricter_status(first: Status, second: Status) -> Status:
    return max(first, second, key=STATUS_RANK.__getitem__)


def is_vector_review_candidate(candidate: ScoredCandidate, vector_review_threshold: float) -> bool:
    return candidate.vector_similarity is not None and candidate.vector_similarity >= vector_review_threshold


def find_score_status(best_candidate: ScoredCandidate, supplier: ScreeningInput) -> Status:
    settings = get_settings()

    if best_candidate.score >= settings.hit_threshold and has_country_match(best_candidate, supplier):
        return "likely_hit"
    if best_candidate.score >= settings.review_threshold:
        return "review"
    return "clear"


def apply_score_rule(status: Status, candidates: list[ScoredCandidate], supplier: ScreeningInput) -> Status:
    if not candidates:
        return status

    return choose_stricter_status(status, find_score_status(candidates[0], supplier))


def apply_registration_number_rule(
    status: Status, candidates: list[ScoredCandidate], supplier: ScreeningInput
) -> Status:
    for candidate in candidates:
        if not has_registration_number_match(candidate):
            continue

        minimum_status: Status = "likely_hit" if has_country_match(candidate, supplier) else "review"
        status = choose_stricter_status(status, minimum_status)

    return status


def apply_status_rules(status: Status, candidates: list[ScoredCandidate], supplier: ScreeningInput) -> Status:
    registration_status = apply_registration_number_rule(status, candidates, supplier)
    if registration_status != status:
        log.info("registration number rule raised status %s to %s", status, registration_status)

    final_status = apply_score_rule(registration_status, candidates, supplier)
    if final_status != registration_status:
        log.info("score rule raised status %s to %s", registration_status, final_status)

    return final_status
