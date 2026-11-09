# SanctionCheck

Supplier sanctions screening POC. Input: supplier name, country, optional registration number. It searches OpenSanctions lists, lets an LLM judge the candidates, applies fixed rules and writes a JSON decision: `clear`, `review` or `likely_hit`.

## Setup

Needs Python 3.13, [uv](https://docs.astral.sh/uv/), Docker, an [OpenRouter](https://openrouter.ai/) key and, for web search, a [Tavily](https://tavily.com/) key.

```sh
cp .env.example .env    # POSTGRES_PASSWORD, OPENROUTER_API_KEY, optional TAVILY_API_KEY
uv sync
docker compose up -d    # Postgres 17 with pg_trgm and pgvector
uv run main migrate
uv run main load        # sources from LOAD_SOURCES
uv run main embed       # run again after each load
```

`load` takes any CSV in the OpenSanctions `targets.simple.csv` format. Sources on the command line replace `LOAD_SOURCES`. Each load replaces all entities; duplicates across files are merged.

```sh
uv run main load https://data.opensanctions.org/datasets/latest/sanctions/targets.simple.csv
```

## Use

```sh
uv run main candidates "Mundo Informatico Paraguay" --country PY                  # scored candidates, no LLM
uv run main screen "Mundo Informatico Paraguay" --country PY --reg-no 80068367-6  # writes data/screening/<timestamp>_<name>_<country>[_<reg_no>].json
uv run main screen "UAB Vitfora" --country LT --web-search
```

## API

```sh
docker compose up -d --build              # API on 127.0.0.1:8000
docker compose run --rm api main migrate  # first start only
uv run main serve                         # without Docker
```

```sh
curl -X POST "localhost:8000/screen?web_search=false" -H "content-type: application/json" \
  -d '{"name": "Mundo Informatico Paraguay", "country": "PY", "reg_no": "80068367-6"}'
```

Also `GET /health`, docs at `/scalar` and `/docs`. No authentication: keep it on `127.0.0.1`.

## Matching

1. **Normalize**: remove legal forms (`UAB`, `LLC`, `有限公司`, ...), transliterate to Latin, lowercase, strip punctuation.
2. **Search**, `top_k` (10) each: `reg_no` exact, `trigram` on names and aliases, `phonetic` (Metaphone), `vector` (embedding of the raw name, for cross-script).
3. **Score** the best name: weighted `token_set_ratio` blended with Jaro-Winkler; rare tokens weigh more. +`COUNTRY_BONUS` for a country match. `reg_no` match = 1.0.
4. **Decide**: the LLM returns matched candidate, status, rationale, evidence and suggested action. Invalid output is retried once, then falls back to `review`.
5. **Rules** only make the status stricter:
   - `reg_no` match in the same country → at least `likely_hit`;
   - score ≥ `hit_threshold` with country match → at least `likely_hit`;
   - score ≥ `review_threshold` → at least `review`.

| Name | Value | Where |
| --- | --- | --- |
| `hit_threshold` | 0.99 | `.env` |
| `review_threshold` | 0.90 | `.env` |
| `vector_review_threshold` | 0.80 | `.env`; keeps vector candidates beyond the top 10 |
| `COUNTRY_BONUS` | 0.03 | `matching/scoring.py` |
| `TOKEN_SET_WEIGHT` | 0.5 | `matching/scoring.py` |

## Eval

```sh
uv run main eval run                                          # writes eval/results/<timestamp>.md and .json
uv run main eval run --web-search                             # same, with web search on
uv run python scripts/build_eval_set.py eval/cases.new.jsonl  # regenerate cases (needs DB, GLEIF API, OpenRouter)
```

`eval/cases.jsonl` is static example evaluation file made by a temporary script `scripts/build_eval_set.py` - generated dataset is preliminary.

| Metric | Target |
| --- | --- |
| Confirmed hits marked `likely_hit` | 100% |
| Recall on exact names and variants | ≥ 99% |
| Clean suppliers `likely_hit` / `review` | ≤ 1% / ≤ 10% |
| Cross-script recall | ≥ 90% |

Categories and target rationale: spec, section "Eval".
