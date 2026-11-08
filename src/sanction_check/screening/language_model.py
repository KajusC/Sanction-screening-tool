import importlib.resources
import json
from typing import Literal

import openai
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionMessage,
    ChatCompletionMessageParam,
    ChatCompletionUserMessageParam,
)
from pydantic import ValidationError

from sanction_check.helpers.log import log
from sanction_check.helpers.openrouter import DENY_DATA_COLLECTION, build_openrouter_client
from sanction_check.schemas.candidate_schema import ScoredCandidate
from sanction_check.schemas.decision_schema import DecisionOutcome, LanguageModelDecision
from sanction_check.schemas.screening_schema import ScreeningInput
from sanction_check.schemas.web_search_schema import WebSearchRecord
from sanction_check.screening.web_search import (
    WEB_SEARCH_MAX_CALLS,
    WEB_SEARCH_TOOL,
    answer_tool_calls,
    build_tool_message,
)
from sanction_check.settings import get_settings

ToolChoice = Literal["auto", "none"]
WebSearchMode = Literal["off", "allowed", "used_up"]

LANGUAGE_MODEL_TEMPERATURE = 0
MAX_ATTEMPTS = 2
FALLBACK_SUGGESTED_ACTION = (
    "The language model did not return a valid decision. "
    "An analyst must review the supplier before onboarding or payment."
)
PROMPT_FILE_NAME = "review_prompt.md"
HIDDEN_CANDIDATE_FIELDS = {"score", "reasons", "vector_similarity"}
DECISION_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {"name": "decision", "schema": LanguageModelDecision.model_json_schema()},
}
PROVIDER_EXTRA_BODY = {"provider": DENY_DATA_COLLECTION, "usage": {"include": True}}
WEB_SEARCH_EXTRA_BODY = {"provider": {**DENY_DATA_COLLECTION, "require_parameters": True}, "usage": {"include": True}}
TOOL_CHOICE_BY_WEB_SEARCH_MODE: dict[WebSearchMode, ToolChoice | openai.Omit] = {
    "off": openai.omit,
    "allowed": "auto",
    "used_up": "none",
}


def load_prompt() -> str:
    path = importlib.resources.files("sanction_check.screening") / "prompts" / PROMPT_FILE_NAME

    return path.read_text(encoding="utf-8")


def build_user_message(supplier: ScreeningInput, candidates: list[ScoredCandidate]) -> str:
    candidates_json = json.dumps(
        [candidate.model_dump(mode="json", exclude=HIDDEN_CANDIDATE_FIELDS) for candidate in candidates],
        ensure_ascii=False,
    )

    user_message = (
        f"<supplier>\n{supplier.model_dump_json()}\n</supplier>\n<candidates>\n{candidates_json}\n</candidates>"
    )

    log.debug("user message for supplier %r:\n%s", supplier.name, user_message)

    return user_message


def choose_web_search_mode(*, should_search_web: bool, search_count: int) -> WebSearchMode:
    if not should_search_web:
        return "off"
    if search_count >= WEB_SEARCH_MAX_CALLS:
        return "used_up"
    return "allowed"


def request_completion(
    client: openai.OpenAI, messages: list[ChatCompletionMessageParam], web_search_mode: WebSearchMode
) -> ChatCompletion:
    is_web_search_on = web_search_mode != "off"

    return client.chat.completions.create(
        model=get_settings().llm_model,
        messages=messages,
        response_format=DECISION_RESPONSE_FORMAT,
        temperature=LANGUAGE_MODEL_TEMPERATURE,
        tools=[WEB_SEARCH_TOOL] if is_web_search_on else openai.omit,
        tool_choice=TOOL_CHOICE_BY_WEB_SEARCH_MODE[web_search_mode],
        extra_body=WEB_SEARCH_EXTRA_BODY if is_web_search_on else PROVIDER_EXTRA_BODY,
    )


def build_tool_round_messages(
    message: ChatCompletionMessage, records: list[WebSearchRecord]
) -> list[ChatCompletionMessageParam]:
    assistant_message = {
        "role": "assistant",
        "content": message.content or "",
        "tool_calls": [tool_call.model_dump(exclude_none=True) for tool_call in message.tool_calls],
    }

    tool_messages = [
        build_tool_message(tool_call.id, record) for tool_call, record in zip(message.tool_calls, records, strict=True)
    ]

    return [assistant_message, *tool_messages]


def find_extra_field_names(content: str) -> list[str]:
    return sorted(json.loads(content).keys() - LanguageModelDecision.model_fields.keys())


def reject_hit_without_candidate(decision: LanguageModelDecision) -> None:
    if decision.status != "clear" and decision.matched_candidate_id is None:
        raise ValueError(f"status {decision.status!r} needs a matched_candidate_id")


def reject_unknown_candidate_id(decision: LanguageModelDecision, candidates: list[ScoredCandidate]) -> None:
    if decision.matched_candidate_id is None:
        return

    candidate_ids = {candidate.id for candidate in candidates}

    if decision.matched_candidate_id not in candidate_ids:
        raise ValueError(f"unknown candidate id: {decision.matched_candidate_id!r}")


def parse_decision(completion: ChatCompletion, candidates: list[ScoredCandidate]) -> LanguageModelDecision:
    message = completion.choices[0].message
    content = message.content or ""

    try:
        decision = LanguageModelDecision.model_validate_json(content)
    except ValidationError as error:
        log.warning("model decision did not follow schema: content=%r refusal=%r", message.content, message.refusal)
        raise ValueError(
            f"model decision did not follow schema: {error.errors(include_input=False, include_url=False)}"
        ) from error

    extra_field_names = find_extra_field_names(content)
    if extra_field_names:
        log.warning("dropped extra fields from model decision: %s", extra_field_names)

    reject_unknown_candidate_id(decision, candidates)
    reject_hit_without_candidate(decision)
    log.debug(
        "model decision: status=%s matched_candidate_id=%s rationale=%r",
        decision.status,
        decision.matched_candidate_id,
        decision.rationale,
    )
    return decision


def response_cost_usd(completion: ChatCompletion) -> float:
    extra_usage = completion.usage.model_extra or {}

    return float(extra_usage.get("cost", 0.0))


def log_completion(completion: ChatCompletion) -> None:
    log.info(
        "received completion, %d prompt tokens, %d completion tokens, cost $%.6f",
        completion.usage.prompt_tokens,
        completion.usage.completion_tokens,
        response_cost_usd(completion),
    )


def retry_message(error_text: str) -> ChatCompletionUserMessageParam:
    return {"role": "user", "content": f"The decision was invalid: {error_text}. Answer again with a valid decision."}


def fallback_decision(error_text: str) -> LanguageModelDecision:
    return LanguageModelDecision(
        matched_candidate_id=None, status="review", rationale=error_text, suggested_action=FALLBACK_SUGGESTED_ACTION
    )


def decide_with_language_model(
    supplier: ScreeningInput, candidates: list[ScoredCandidate], *, should_search_web: bool
) -> DecisionOutcome:
    web_searches: list[WebSearchRecord] = []
    completions: list[ChatCompletion] = []
    decision: LanguageModelDecision | None = None
    error_text = ""
    attempt_count = 0

    client = build_openrouter_client()
    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": load_prompt()},
        {"role": "user", "content": build_user_message(supplier, candidates)},
    ]

    while decision is None and attempt_count < MAX_ATTEMPTS:
        web_search_mode = choose_web_search_mode(should_search_web=should_search_web, search_count=len(web_searches))
        log.info("requesting decision, attempt %d of %d", attempt_count + 1, MAX_ATTEMPTS)
        completion = request_completion(client, messages, web_search_mode)
        completions.append(completion)
        log_completion(completion)

        message = completion.choices[0].message
        if message.tool_calls and web_search_mode == "allowed":
            records = answer_tool_calls(message.tool_calls, len(web_searches))
            web_searches = [*web_searches, *records]
            messages = [*messages, *build_tool_round_messages(message, records)]
            continue

        attempt_count += 1
        try:
            decision = parse_decision(completion, candidates)
        except ValueError as error:
            error_text = str(error)
            log.warning("attempt %d rejected: %s", attempt_count, error_text)
            messages = [*messages, retry_message(error_text)]

    if decision is None:
        log.warning("falling back to review after %d attempts", MAX_ATTEMPTS)

    return DecisionOutcome(
        decision=decision or fallback_decision(error_text),
        is_fallback=decision is None,
        attempt_count=attempt_count,
        cost_usd=sum(response_cost_usd(completion) for completion in completions),
        web_searches=web_searches,
    )
