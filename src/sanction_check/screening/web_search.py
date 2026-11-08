import json
from urllib.parse import urlsplit

import httpx
from openai.types.chat import (
    ChatCompletionMessageToolCallUnion,
    ChatCompletionToolMessageParam,
    ChatCompletionToolParam,
)
from pydantic import ValidationError

from sanction_check.helpers.log import log
from sanction_check.schemas.web_search_schema import (
    MAX_QUERY_LENGTH,
    MAX_SNIPPET_LENGTH,
    WebSearchArguments,
    WebSearchHit,
    WebSearchRecord,
)
from sanction_check.settings import get_settings

TAVILY_BASE_URL = "https://api.tavily.com"
WEB_SEARCH_TIMEOUT_SECONDS = 10.0
WEB_SEARCH_MAX_CALLS = 3
WEB_SEARCH_MAX_RESULTS = 5
SEARCH_DEPTH = "basic"
SEARCH_UNAVAILABLE_ERROR = "search unavailable"
SEARCH_LIMIT_REACHED_ERROR = "search limit reached"
UNKNOWN_TOOL_ERROR = "unknown tool"
MISSING_API_KEY_MESSAGE = "TAVILY_API_KEY is not set"
WEB_SEARCH_TOOL_NAME = "search_web"
WEB_SEARCH_TOOL: ChatCompletionToolParam = {
    "type": "function",
    "function": {
        "name": WEB_SEARCH_TOOL_NAME,
        "description": "Search official sanctions lists, company registries and government sites.",
        "parameters": WebSearchArguments.model_json_schema(),
    },
}


def is_allowed_url(url: str, allowed_domains: list[str]) -> bool:
    parts = urlsplit(url)
    host = parts.hostname or ""

    is_allowed_host = any(host == domain or host.endswith(f".{domain}") for domain in allowed_domains)

    return parts.scheme == "https" and is_allowed_host


def build_error_record(query: str, error_text: str) -> WebSearchRecord:
    return WebSearchRecord(query=query, hits=[], error=error_text)


def fetch_search_hits(client: httpx.Client, query: str, allowed_domains: list[str]) -> list[WebSearchHit]:
    payload = {
        "query": query,
        "max_results": WEB_SEARCH_MAX_RESULTS,
        "include_domains": allowed_domains,
        "include_domains_mode": "restrict",
        "search_depth": SEARCH_DEPTH,
    }

    response = client.post("/search", json=payload)
    response.raise_for_status()

    return [
        WebSearchHit(title=result["title"], url=result["url"], snippet=result["content"][:MAX_SNIPPET_LENGTH])
        for result in response.json()["results"]
    ]


def search_web(query: str) -> WebSearchRecord:
    settings = get_settings()

    if settings.tavily_api_key is None:
        raise ValueError(MISSING_API_KEY_MESSAGE)
    try:
        with httpx.Client(
            base_url=TAVILY_BASE_URL,
            timeout=WEB_SEARCH_TIMEOUT_SECONDS,
            headers={"Authorization": f"Bearer {settings.tavily_api_key.get_secret_value()}"},
        ) as client:
            hits = fetch_search_hits(client, query, settings.web_search_allowed_domains)
    except httpx.HTTPError as error:
        log.warning("web search failed for %r: %s", query, error)
        return build_error_record(query, SEARCH_UNAVAILABLE_ERROR)
    allowed_hits = [hit for hit in hits if is_allowed_url(hit.url, settings.web_search_allowed_domains)]

    log.info("web search %r: %d hits, %d dropped", query, len(allowed_hits), len(hits) - len(allowed_hits))

    return WebSearchRecord(query=query, hits=allowed_hits, error=None)


def answer_tool_call(tool_call: ChatCompletionMessageToolCallUnion, search_count: int) -> WebSearchRecord:
    if tool_call.type != "function":
        return build_error_record(tool_call.custom.name, UNKNOWN_TOOL_ERROR)

    function = tool_call.function
    if function.name != WEB_SEARCH_TOOL_NAME:
        return build_error_record(function.name, UNKNOWN_TOOL_ERROR)
    try:
        arguments = WebSearchArguments.model_validate_json(function.arguments)
    except ValidationError as error:
        return build_error_record(
            function.arguments[:MAX_QUERY_LENGTH], str(error.errors(include_input=False, include_url=False))
        )

    if search_count >= WEB_SEARCH_MAX_CALLS:
        return build_error_record(arguments.query, SEARCH_LIMIT_REACHED_ERROR)
    return search_web(arguments.query)


def answer_tool_calls(tool_calls: list[ChatCompletionMessageToolCallUnion], search_count: int) -> list[WebSearchRecord]:
    return [answer_tool_call(tool_call, search_count + index) for index, tool_call in enumerate(tool_calls)]


def build_tool_message(tool_call_id: str, record: WebSearchRecord) -> ChatCompletionToolMessageParam:
    hits_json = json.dumps([hit.model_dump() for hit in record.hits], ensure_ascii=False)
    safe_hits_json = hits_json.replace("<", "\\u003c")

    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": f"<web_results>\n{record.error or safe_hits_json}\n</web_results>",
    }
