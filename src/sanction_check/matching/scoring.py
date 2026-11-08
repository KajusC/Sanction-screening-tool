from rapidfuzz import fuzz
from rapidfuzz.distance import JaroWinkler

from sanction_check.matching.normalize import has_cjk_character
from sanction_check.schemas.candidate_schema import CandidateName, NameScore, ScoredCandidate, UnscoredCandidate
from sanction_check.schemas.screening_schema import ScreeningInput

TOKEN_SET_WEIGHT = 0.5
COUNTRY_BONUS = 0.03
REGISTRATION_NUMBER_SCORE = 1.0
MAX_SCORE = 1.0
PERCENT = 100


def score_token_set(normalized_name: str, candidate_normalized_name: str, token_weights: dict[str, float]) -> float:
    total_weight = sum(token_weights.values())
    if total_weight == 0:
        return fuzz.token_set_ratio(normalized_name, candidate_normalized_name) / PERCENT

    weighted_sum = 0.0
    for token, weight in token_weights.items():
        weighted_sum += weight * fuzz.token_set_ratio(token, candidate_normalized_name) / PERCENT

    return weighted_sum / total_weight


def score_space_free(normalized_name: str, candidate_normalized_name: str) -> float:
    return fuzz.ratio(normalized_name.replace(" ", ""), candidate_normalized_name.replace(" ", "")) / PERCENT


def should_ignore_spaces(supplier_name: str, candidate_name: CandidateName) -> bool:
    return has_cjk_character(supplier_name) or has_cjk_character(candidate_name.name_raw)


def score_name(
    normalized_name: str, candidate_name: CandidateName, token_weights: dict[str, float], is_ignoring_spaces: bool
) -> NameScore:
    token_set = score_token_set(normalized_name, candidate_name.name_norm, token_weights)
    jaro_winkler = JaroWinkler.similarity(normalized_name, candidate_name.name_norm)
    space_free = score_space_free(normalized_name, candidate_name.name_norm) if is_ignoring_spaces else None

    blended = TOKEN_SET_WEIGHT * token_set + (1 - TOKEN_SET_WEIGHT) * jaro_winkler

    return NameScore(
        name=candidate_name,
        token_set=token_set,
        jaro_winkler=jaro_winkler,
        space_free=space_free,
        score=blended if space_free is None else max(blended, space_free),
    )


def describe_name(candidate: UnscoredCandidate, name_score: NameScore) -> str:
    name_kind = "name" if name_score.name.name_raw == candidate.name else "alias"

    components = [f"token_set={name_score.token_set:.2f}", f"jaro_winkler={name_score.jaro_winkler:.2f}"]
    if name_score.space_free is not None:
        components.append(f"space_free={name_score.space_free:.2f}")

    return f"{name_kind} '{name_score.name.name_raw}' score={name_score.score:.2f} ({' '.join(components)})"


def has_country_match(candidate: UnscoredCandidate, supplier: ScreeningInput) -> bool:
    return supplier.country in candidate.countries


def has_registration_number_match(candidate: UnscoredCandidate) -> bool:
    return "reg_no" in candidate.search_methods


def score_candidate(
    normalized_name: str, candidate: UnscoredCandidate, supplier: ScreeningInput, token_weights: dict[str, float]
) -> ScoredCandidate:
    name_scores = [
        score_name(normalized_name, name, token_weights, should_ignore_spaces(supplier.name, name))
        for name in candidate.names
    ]
    best_name_score = max(name_scores, key=lambda name_score: name_score.score)

    score = best_name_score.score
    reasons = [describe_name(candidate, best_name_score)]

    if has_country_match(candidate, supplier):
        score += COUNTRY_BONUS
        reasons.append("country match")

    if candidate.vector_similarity is not None:
        reasons.append(f"vector similarity={candidate.vector_similarity:.2f}")

    if has_registration_number_match(candidate):
        score = REGISTRATION_NUMBER_SCORE
        reasons.append("reg_no exact")

    return ScoredCandidate(**candidate.model_dump(), score=min(score, MAX_SCORE), reasons=reasons)
