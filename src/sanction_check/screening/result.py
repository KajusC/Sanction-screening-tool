import re
from pathlib import Path

from sanction_check.schemas.candidate_schema import ScoredCandidate
from sanction_check.schemas.decision_schema import Evidence
from sanction_check.schemas.screening_schema import ScreeningResult

OPENSANCTIONS_ENTITY_URL = "https://www.opensanctions.org/entities/{id}/"
SCREENING_DIRECTORY = Path("data/screening")
FILE_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"
UNSAFE_FILE_NAME_CHARACTERS = re.compile(r"[^\w.-]")


def build_evidence(candidate: ScoredCandidate) -> list[Evidence]:
    url = OPENSANCTIONS_ENTITY_URL.format(id=candidate.id)
    snippet = "; ".join(candidate.sanctions)

    return [Evidence(source="; ".join(candidate.datasets), url=url, snippet=snippet, last_change=candidate.last_change)]


def build_result_file_name(result: ScreeningResult) -> str:
    supplier = result.input
    parts = [f"{result.meta.screened_at:{FILE_TIMESTAMP_FORMAT}}", supplier.name, supplier.country]
    if supplier.reg_no:
        parts.append(supplier.reg_no)

    return f"{UNSAFE_FILE_NAME_CHARACTERS.sub('_', '_'.join(parts))}.json"


def write_result(result: ScreeningResult) -> Path:
    path = SCREENING_DIRECTORY / build_result_file_name(result)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.model_dump_json(indent=2), encoding="utf-8")

    return path
