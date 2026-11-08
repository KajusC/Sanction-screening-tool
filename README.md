# SanctionCheck

Supplier sanctions screening POC. Give it a supplier name, a country and optionally a registration number. It searches the OpenSanctions sanctions lists, lets an LLM judge the candidates, applies fixed rules, and writes an auditable JSON decision: `clear`, `review` or `likely_hit`.

The full design is in [docs/specs/sanctions-screening.md](docs/specs/sanctions-screening.md). The task brief is in [docs/task.md](docs/task.md).

## Terminology

### Screening terms

| Term | Meaning |
| --- | --- |
| supplier | The company that is screened. The input is a name, a country and optionally a registration number. |
| registration number (`reg_no`) | The official company number in its country, for example a tax or company register ID. |
| sanctions list | A list of people, companies and vessels that a government or union has sanctioned, for example the EU lists. |
| OpenSanctions | A public project that collects sanctions lists into one format. This repo loads its `targets.simple.csv` files. |
| entity | One row of the loaded OpenSanctions data: a person, an organization, a vessel and so on. |
| target | An entity that is on a sanctions list. Every loaded entity is a target. |
| dataset | One source list inside OpenSanctions, for example "EU Financial Sanctions Files (FSF)". |
| program | One sanctions program code, for example `US-GLOMAG`. |
| alias | An alternative name of an entity. Entities have several names, and all of them are searched. |
| candidate | A target that a search found for the supplier. A candidate is only a possible match. |
| status | The decision: `clear` (no match), `review` (a person must look), `likely_hit` (probably a sanctioned supplier). |
| LLM | The language model, called through OpenRouter. It reads the supplier and the candidates and judges them. |
| rule | A fixed check after the LLM. It can make the status stricter, never looser. |

### Matching terms

| Term | Meaning |
| --- | --- |
| normalize | Turn a name into a plain comparable form: Latin letters, lowercase, no punctuation, no legal forms. |
| legal form | The company type in a name, for example `UAB`, `SIA`, `OOO`, `LLC` or `有限公司`. It says nothing about who the company is, so it is removed. |
| transliterate | Write a name in another script, for example Cyrillic `Газпром` as `Gazprom`. |
| CJK name | A name with Chinese characters (hanzi), Japanese kana or kanji. |
| pinyin | The Latin spelling of Chinese. For example, `开兴隆` is `Kai Xing Long`. |
| cross-script | The supplier name and the stored name are written in different scripts, for example hanzi against pinyin. |
| search method | One way to find candidates: `reg_no`, `trigram`, `phonetic` or `vector`. |
| trigram | A group of 3 letters. Two names are similar when they share many trigrams, so typos and partial names still match. Done by the Postgres extension `pg_trgm`. |
| phonetic | Names that sound alike. Each name is turned into a Metaphone code and compared by trigram similarity. |
| embedding | A list of numbers that stands for the meaning of a name. It is made by a model and stored with pgvector. |
| vector similarity | Cosine similarity of two embeddings. High similarity means the names mean about the same, even in another script. |
| `top_k` | How many candidates each search method returns (10). |
| score | A number from 0 to 1 for how well a candidate matches the supplier, with the reasons. |
| `token_set_ratio` | A word-based similarity that ignores word order and extra words. |
| Jaro-Winkler | A character-based similarity that is good at typos and small spelling changes. |
| token | One word of a normalized name. A rare token weighs more than a common one (`trading`, `group`). |
| threshold | A score limit. `hit_threshold` is 0.99 and `vector_review_threshold` is 0.80. |

### Eval terms

| Term | Meaning |
| --- | --- |
| eval case | One test: a supplier input plus the expected result. The cases are the lines of `eval/cases.jsonl`. |
| category | A group of eval cases that test one thing: `exact`, `variant`, `negative`, `common_name`, `cross_script`, `cross_script_clean`. |
| positive case | A case built from a real target. The expected status is at least `review`, and the expected entity is that target. |
| negative case | A case built from a real company that is not sanctioned. The expected status is `clear`. |
| variant | A target name with one change: a typo, another script, no legal form, reversed words or a dropped last word. |
| common name | A clean company whose name shares a word with many targets. It tests false alarms. |
| GLEIF | The Global Legal Entity Identifier Foundation. Its public API is the source of the real, unsanctioned companies in the negative cases. |
| recall | The share of positive cases that the system flags (`review` or `likely_hit`). |
| false positive | A clean supplier that is flagged. |
| static dataset | `eval/cases.jsonl` as committed. It does not change unless someone regenerates it. |

## Setup

Needs Python 3.13, [uv](https://docs.astral.sh/uv/), Docker and an [OpenRouter](https://openrouter.ai/) key. Web search also needs a [Tavily](https://tavily.com/) key.

```sh
cp .env.example .env              # set POSTGRES_PASSWORD, OPENROUTER_API_KEY, optional TAVILY_API_KEY
uv sync
docker compose up -d              # Postgres 17 with pg_trgm and pgvector on 127.0.0.1:5432
uv run main migrate
uv run main load                  # downloads the EU FSF and EU Sanctions Map lists
uv run main embed                 # embeds all ~296k names through OpenRouter; run again after each load
```

`load` reads the sources in `LOAD_SOURCES` in `.env` (required; a JSON list of URLs or file paths; `.env.example` has the two EU lists). Sources given on the command line replace that list. Any source works as long as the CSV has the OpenSanctions `targets.simple.csv` columns. Entities that appear in several files are merged. Each load replaces all loaded entities.

```sh
uv run main load https://data.opensanctions.org/datasets/latest/sanctions/targets.simple.csv
uv run main load data/sanctioned.csv data/other.csv
```

## Use

```sh
uv run main candidates "Mundo Informatico Paraguay" --country PY
uv run main screen "Mundo Informatico Paraguay" --country PY --reg-no 80068367-6
uv run main screen "UAB Vitfora" --country LT --web-search
```

`candidates` prints the scored candidates without the LLM. `screen` writes the full result to `data/screening/<timestamp>_<name>_<country>[_<reg_no>].json` and prints the path.

## How matching works

1. **Normalize** the name: remove CJK legal forms (`有限公司`, ...), transliterate any script to Latin (`anyascii`), lowercase, remove punctuation and legal forms (`UAB`, `SIA`, `OOO`, `LLC`, ...).
2. **Search** with four methods, `top_k` (10) entities each:
   - `reg_no`: exact match on the normalized registration number;
   - `trigram`: `pg_trgm` similarity on the normalized names and aliases (typos, partial names);
   - `phonetic`: trigram similarity on Metaphone codes (spelling variants that sound alike);
   - `vector`: embedding similarity of the raw name (translated and cross-script names).
3. **Score** each candidate on its best name: a blend of a token-weighted `token_set_ratio` (word order, extra words) and Jaro-Winkler (typos). Rare tokens weigh more than common ones (`trading`, `group`), so very common names do not score high on shared words. Add 0.03 for a country match. A registration-number match scores 1.0. Every score comes with its reasons.
4. **Decide**: the LLM gets the supplier and the candidates and returns a JSON decision: matched candidate, status, rationale and suggested action. The code validates it, drops and logs extra fields, retries once, and falls back to `review`.
5. **Rules** after the LLM only make the status stricter:
   - a registration-number match in the same country is at least `likely_hit`;
   - a best score of 0.99 or more is at least `review`, even when the LLM says `clear`.

The two thresholds are in `Settings` and can change in `.env`. The two scoring weights are constants in `matching/scoring.py`. They start at these values and are tuned with the eval:

| Name | Value | Meaning |
| --- | --- | --- |
| `hit_threshold` | 0.99 | A near-exact name match cannot be cleared by the LLM alone. |
| `vector_review_threshold` | 0.80 | A candidate with this vector similarity stays in the list for the LLM, also below the top 10. |
| `COUNTRY_BONUS` | 0.03 | A country match lifts a candidate a little, but never makes a weak name match strong. |
| `TOKEN_SET_WEIGHT` | 0.5 | The balance between word overlap and character similarity. |

## Eval

```sh
uv run main eval run                          # screens eval/cases.jsonl, writes eval/results/<timestamp>.md and .json
uv run main eval build eval/cases.new.jsonl   # optional: regenerates cases from real targets and real GLEIF companies
```

`eval/cases.jsonl` is a static dataset committed to git, and `eval run` only reads it. `eval build` is a generator that writes only to the path you give it. To replace the dataset, build to a new path, review the diff, and move the file over `eval/cases.jsonl`.

The eval set is small on purpose: about 17 cases per category, 99 in all. A run takes about 12 minutes and costs about $0.13.

| Metric | Target |
| --- | --- |
| Recall on direct hits (exact names and variants) | ≥ 99% |
| Clean suppliers marked `likely_hit` / `review` | ≤ 1% / ≤ 10% |
| Cross-script recall (Chinese names and pinyin) | ≥ 90% |
| LLM retries, fallbacks, cost and latency per supplier | reported |

The case categories and the reasons for the targets are in the spec, section "Eval".
