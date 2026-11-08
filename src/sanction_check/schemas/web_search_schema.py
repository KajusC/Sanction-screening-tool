from pydantic import BaseModel, ConfigDict, Field

MIN_QUERY_LENGTH = 2
MAX_QUERY_LENGTH = 200
MAX_SNIPPET_LENGTH = 700


class WebSearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=MIN_QUERY_LENGTH, max_length=MAX_QUERY_LENGTH)


class WebSearchHit(BaseModel):
    title: str
    url: str
    snippet: str


class WebSearchRecord(BaseModel):
    query: str
    hits: list[WebSearchHit]
    error: str | None
