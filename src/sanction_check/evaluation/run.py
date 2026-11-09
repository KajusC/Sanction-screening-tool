import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

from django.db import transaction

from sanction_check.db.manage import check_database
from sanction_check.db.models import EntityName
from sanction_check.helpers.log import log
from sanction_check.matching.normalize import CJK_CHARACTER_PATTERN
from sanction_check.schemas.decision_schema import Status
from sanction_check.schemas.evaluation_schema import (
    EvalCase,
    EvalCaseResult,
    EvalCategory,
    EvalCategoryReport,
    EvalMetric,
    EvalReport,
)
from sanction_check.schemas.screening_schema import ScreeningResult
from sanction_check.screening.result import UNSAFE_FILE_NAME_CHARACTERS
from sanction_check.screening.rules import STATUS_RANK
from sanction_check.screening.supplier_screening import screen_supplier
from sanction_check.settings import get_settings

RESULTS_DIRECTORY = Path("eval/results")
INTERMEDIATE_DIRECTORY = RESULTS_DIRECTORY / "individual_review"
REPORT_TIMESTAMP_FORMAT = "%Y%m%dT%H"
CONFIRMED_CATEGORIES: list[EvalCategory] = ["confirmed"]
POSITIVE_CATEGORIES: list[EvalCategory] = ["exact", "variant"]
CLEAN_CATEGORIES: list[EvalCategory] = ["negative", "common_name"]
CROSS_SCRIPT_POSITIVE_CATEGORIES: list[EvalCategory] = ["cross_script"]
CROSS_SCRIPT_CLEAN_CATEGORIES: list[EvalCategory] = ["cross_script_clean"]
CONFIRMED_LIKELY_HIT_TARGET = 1.0
DIRECT_RECALL_TARGET = 0.99
CROSS_SCRIPT_RECALL_TARGET = 0.90
CLEAN_LIKELY_HIT_LIMIT = 0.01
CLEAN_REVIEW_LIMIT = 0.10
LATENCY_QUANTILE_COUNT = 20
PROGRESS_LOG_INTERVAL = 25


def read_cases(cases_path: Path) -> list[EvalCase]:
    lines = cases_path.read_text(encoding="utf-8").splitlines()

    return [EvalCase.model_validate_json(line) for line in lines if line]


def screen_case(case: EvalCase, should_search_web: bool) -> ScreeningResult:
    if not case.should_hide_cjk_names:
        return screen_supplier(case.input, should_search_web=should_search_web)

    with transaction.atomic():
        EntityName.objects.filter(entity_id=case.expected_entity_id, name_raw__regex=CJK_CHARACTER_PATTERN).delete()

        result = screen_supplier(case.input, should_search_web=should_search_web)
        transaction.set_rollback(True)
    return result


def build_intermediate_directory(should_search_web: bool) -> Path:
    search_mode = "web_search" if should_search_web else "no_web_search"
    started_at = datetime.now(UTC).strftime(REPORT_TIMESTAMP_FORMAT)
    directory_name = UNSAFE_FILE_NAME_CHARACTERS.sub("_", f"{started_at}_{get_settings().llm_model}_{search_mode}")

    return INTERMEDIATE_DIRECTORY / directory_name


def load_or_screen_case(case: EvalCase, should_search_web: bool, intermediate_directory: Path) -> ScreeningResult:
    intermediate_path = intermediate_directory / f"{case.id}.json"
    if intermediate_path.exists():
        log.info("reusing intermediate result of case %s from %s", case.id, intermediate_path)
        return ScreeningResult.model_validate_json(intermediate_path.read_text(encoding="utf-8"))

    result = screen_case(case, should_search_web=should_search_web)

    intermediate_path.parent.mkdir(parents=True, exist_ok=True)
    intermediate_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")

    return result


def run_case(case: EvalCase, should_search_web: bool, intermediate_directory: Path) -> EvalCaseResult:
    result = load_or_screen_case(case, should_search_web, intermediate_directory)

    log.info("case %s (%s) status %s in %.1fs", case.id, case.category, result.status, result.meta.latency_seconds)

    return EvalCaseResult(
        case=case,
        status=result.status,
        matched_entity_id=result.matched_entity.id if result.matched_entity else None,
        retry_count=result.meta.attempt_count - 1,
        is_fallback=result.meta.is_fallback,
        has_web_search=bool(result.web_searches),
        cost_usd=result.meta.cost_usd,
        latency_seconds=result.meta.latency_seconds,
    )


def select_results(results: list[EvalCaseResult], categories: list[EvalCategory]) -> list[EvalCaseResult]:
    return [result for result in results if result.case.category in categories]


def compute_rate(matching_count: int, total_count: int, label: str) -> float:
    if total_count == 0:
        raise ValueError(f"no eval cases for {label}")

    return matching_count / total_count


def is_missed_hit(result: EvalCaseResult) -> bool:
    return STATUS_RANK[result.status] < STATUS_RANK[result.case.expected_status_min]


def compute_recall(positives: list[EvalCaseResult], label: str) -> float:
    return compute_rate(sum(not is_missed_hit(result) for result in positives), len(positives), label)


def compute_status_rate(clean: list[EvalCaseResult], status: Status, label: str) -> float:
    return compute_rate(sum(result.status == status for result in clean), len(clean), label)


def build_reported_metric(name: str, value: float) -> EvalMetric:
    return EvalMetric(name=name, value=value, target=None, is_passed=None)


def build_minimum_metric(name: str, value: float, minimum: float) -> EvalMetric:
    return EvalMetric(name=name, value=value, target=f">= {minimum:.0%}", is_passed=value >= minimum)


def build_maximum_metric(name: str, value: float, maximum: float) -> EvalMetric:
    return EvalMetric(name=name, value=value, target=f"<= {maximum:.0%}", is_passed=value <= maximum)


def build_group_metrics(
    label: str, positives: list[EvalCaseResult], clean: list[EvalCaseResult], recall_target: float
) -> list[EvalMetric]:
    if not positives and not clean:
        return []

    return [
        build_minimum_metric(
            f"{label} recall (review + likely_hit)", compute_recall(positives, f"{label} hits"), recall_target
        ),
        build_maximum_metric(
            f"{label} clean suppliers marked likely_hit",
            compute_status_rate(clean, "likely_hit", f"{label} clean"),
            CLEAN_LIKELY_HIT_LIMIT,
        ),
        build_maximum_metric(
            f"{label} clean suppliers marked review",
            compute_status_rate(clean, "review", f"{label} clean"),
            CLEAN_REVIEW_LIMIT,
        ),
    ]


def build_confirmed_metrics(confirmed: list[EvalCaseResult]) -> list[EvalMetric]:
    if not confirmed:
        return []

    return [
        build_minimum_metric(
            "Confirmed hits marked likely_hit", compute_recall(confirmed, "confirmed hits"), CONFIRMED_LIKELY_HIT_TARGET
        )
    ]


def build_metrics(results: list[EvalCaseResult]) -> list[EvalMetric]:
    positives = select_results(results, POSITIVE_CATEGORIES)
    correct_entity_rate = compute_rate(
        sum(result.matched_entity_id == result.case.expected_entity_id for result in positives),
        len(positives),
        "direct hits",
    )
    latencies = [result.latency_seconds for result in results]
    total_cost_usd = sum(result.cost_usd for result in results)

    return [
        *build_confirmed_metrics(select_results(results, CONFIRMED_CATEGORIES)),
        *build_group_metrics("Direct", positives, select_results(results, CLEAN_CATEGORIES), DIRECT_RECALL_TARGET),
        *build_group_metrics(
            "Cross-script",
            select_results(results, CROSS_SCRIPT_POSITIVE_CATEGORIES),
            select_results(results, CROSS_SCRIPT_CLEAN_CATEGORIES),
            CROSS_SCRIPT_RECALL_TARGET,
        ),
        build_reported_metric("Direct hits with the expected entity", correct_entity_rate),
        build_reported_metric("LLM retries", sum(result.retry_count for result in results)),
        build_reported_metric("LLM fallbacks", sum(result.is_fallback for result in results)),
        build_reported_metric("Cases where the LLM searched the web", sum(result.has_web_search for result in results)),
        build_reported_metric("Total cost (USD)", total_cost_usd),
        build_reported_metric("Cost per supplier (USD)", total_cost_usd / len(results)),
        build_reported_metric("Latency p50 (seconds)", statistics.median(latencies)),
        build_reported_metric(
            "Latency p95 (seconds)", statistics.quantiles(latencies, n=LATENCY_QUANTILE_COUNT, method="inclusive")[-1]
        ),
    ]


def build_category_reports(results: list[EvalCaseResult]) -> list[EvalCategoryReport]:
    reports = []

    for category in get_args(EvalCategory):
        category_results = select_results(results, [category])
        reports.append(
            EvalCategoryReport(
                category=category,
                status_counts={
                    status: sum(result.status == status for result in category_results) for status in get_args(Status)
                },
            )
        )

    return reports


def is_off_target(result: EvalCaseResult) -> bool:
    if result.case.expected_status_min == "clear":
        return result.status != "clear"

    return is_missed_hit(result)


def format_metric_row(metric: EvalMetric) -> str:
    verdict = "" if metric.is_passed is None else ("PASS" if metric.is_passed else "FAIL")

    return f"| {metric.name} | {metric.value:.4g} | {metric.target or ''} | {verdict} |"


def format_category_row(report: EvalCategoryReport) -> str:
    counts = " | ".join(str(report.status_counts[status]) for status in get_args(Status))

    return f"| {report.category} | {sum(report.status_counts.values())} | {counts} |"


def format_off_target_row(result: EvalCaseResult) -> str:
    case = result.case
    web_search_label = "LLM searched the web" if result.has_web_search else ""

    return (
        f"| {case.id} | {case.input.name} | {case.input.country} "
        f"| {case.category} | {result.status} | {result.matched_entity_id or ''} | {web_search_label} |"
    )


def format_markdown(report: EvalReport) -> str:
    off_target_results = [result for result in report.case_results if is_off_target(result)]

    lines = [
        "# Eval report: direct screening",
        "",
        f"- Generated: {report.generated_at.isoformat()}",
        f"- Cases: {len(report.case_results)}",
        f"- LLM model: {report.llm_model}",
        "",
        "## Metrics",
        "",
        "| Metric | Value | Target | Result |",
        "|---|---|---|---|",
        *[format_metric_row(metric) for metric in report.metrics],
        "",
        "## Status by category",
        "",
        "| Category | Cases | clear | review | likely_hit |",
        "|---|---|---|---|---|",
        *[format_category_row(category) for category in report.category_reports],
        "",
        f"## Cases off target ({len(off_target_results)})",
        "",
        "| Case | Name | Country | Category | Status | Matched entity | Web search |",
        "|---|---|---|---|---|---|---|",
        *[format_off_target_row(result) for result in off_target_results],
    ]

    return "\n".join(lines) + "\n"


def write_report(report: EvalReport, markdown_path: Path) -> None:
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(format_markdown(report), encoding="utf-8")
    markdown_path.with_suffix(".json").write_text(report.model_dump_json(indent=2), encoding="utf-8")


def run_eval(cases_path: Path, should_search_web: bool = False) -> Path:
    check_database()
    cases = read_cases(cases_path)
    log.info("running %d eval cases from %s", len(cases), "with web search" if should_search_web else "", cases_path)

    intermediate_directory = build_intermediate_directory(should_search_web)

    results = []
    for number, case in enumerate(cases, start=1):
        results.append(run_case(case, should_search_web, intermediate_directory))

        if number % PROGRESS_LOG_INTERVAL == 0:
            log.info("evaluated %d of %d cases", number, len(cases))

    generated_at = datetime.now(UTC)
    llm_model = get_settings().llm_model
    markdown_path = RESULTS_DIRECTORY / f"{generated_at:{REPORT_TIMESTAMP_FORMAT}}_{llm_model}.md"
    report = EvalReport(
        generated_at=generated_at,
        llm_model=llm_model,
        metrics=build_metrics(results),
        category_reports=build_category_reports(results),
        case_results=results,
    )

    write_report(report, markdown_path)

    log.info("wrote eval report to %s", markdown_path)

    return markdown_path
