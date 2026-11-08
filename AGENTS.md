# AGENTS.md

How code in this repo must look. Every change follows these rules, so that all code reads as if one person wrote it. When a rule and existing code disagree, the rule wins. Fix the code when you touch it.

## Principles

1. **Self-explanatory code.** The names and the structure say what the code does. If code needs an explanation, rename or split it until it does not.
2. **Consistency over preference.** Before you write something, find how the repo already does it and do it the same way.
3. **Small and flat.** Short functions, few branches, little nesting, no layers that only pass values through.
4. **Minimal change.** Change only what the task needs. Do not refactor, reformat or rename unrelated code.
5. **No speculative code.** No unused parameters, fields that no code reads, "for later" hooks, abstract base classes with one implementation, or config for things that do not change.
6. **Once.** Write each value, condition and sequence of steps once. When you need it a second time, give it a name and reuse it.

## Comments and docstrings

- **Do not write comments.** This includes `#` and `TODO`s. The only exception: files that Django generates (migrations). Leave them as Django writes them.
- **Do not write docstrings.** The only exception: a Typer command's docstring, because it is the CLI help text. Keep it to one line.
- Do not explain code with comments. Make it clear with:
  - a better name: `is_target` instead of `flag`, `normalized_name` instead of `n`;
  - a small named function: `has_exact_registration_number_match(...)` instead of a commented condition;
  - a named constant: `MAX_RATIONALE_LENGTH = 600` instead of `600  # max length`.
- Do not leave commented-out code. Delete it. Git keeps history.

## Naming

- Modules, functions, variables: `snake_case`. Classes: `PascalCase`. Constants: `UPPER_SNAKE_CASE`.
- Functions are verbs: `normalize_name`, `find_candidates`, `build_evidence`.
- Booleans read as questions: `is_target`, `has_owner`, `should_retry`.
- Use the domain's words: *supplier*, *target*, *candidate*, *search method*, *status*. The full list is in `docs/specs/sanctions-screening.md`, Terms. Use one word per concept everywhere: in names, parameters, log messages and prompts. Do not mix *entity* / *record* / *item* for the same thing, or `selected` / `matched_candidate` for the chosen candidate.
- No abbreviations except `db`. Write `registration_number`, `normalized`, `language_model`, `length`, `random_generator`. Names that are stored or public keep their spelling: DB columns, JSON keys, environment variables and CLI options (`name_norm`, `reg_no`, `llm_model`).
- No vague names: `data`, `info`, `result2`, `tmp`, `obj`, `handle`, `process`, `manager`. A module name says what it holds: `web_search.py`, not `ai_tools.py`.
- No `utils.py`, `helpers.py`, `common.py`. The `helpers/` package holds shared infrastructure only (`helpers/log.py`). A function belongs in the module of its responsibility.

## Functions

- One function does one thing. If you describe it with "and", split it.
- Keep it short. If a function does not fit on one screen, split it.
- Do not split a function only for the sake of splitting. Prefer readable code over complex code. Code that reads top to bottom in one place is better than many tiny helpers.
- Use early returns instead of nested `if`/`else`.
- Do not use `for ... else` or `while ... else`. Check a count after the loop.
- Build the return value in one place. Do not write the same constructor call in two branches.
- Do not keep running totals in a loop. Collect the items, then compute the totals after the loop.
- Compute a value once. When a second function needs it, return it from the first one (several values: a Pydantic model).
- Do not write by hand what the standard library has: `statistics.median`, `itertools.batched`, `typing.get_args`.
- Inside a function, separate logical steps with one blank line: setup, each loop, `try` or `if` block that does a separate job, and the final `return`. Do not put blank lines between lines that belong to the same step.
- At most 4 parameters. With more, group them into a Pydantic model.
- Do not change arguments in place. Return new values.
- Keep logic pure. Put I/O (database queries, HTTP, files, stdout) at the edges. Scoring and rule functions take plain values or Pydantic objects, not querysets.
- Use plain functions. Add a class only for data (Pydantic) or for state that is truly necessary.

## Types and data

- Use type hints on every parameter and return value. Use modern syntax: `str | None`, `list[str]`, `dict[str, int]`. Do not use `Optional`, `List`, `Dict` or `Any`.
- Data that crosses a module boundary is a Pydantic model. Do not pass structured data as a `dict` or a `tuple`.
- Django models are the storage layer only. Convert them to Pydantic models before they go to scoring, the LLM or the result. Do not send Django model instances outside the data layer.
- One Pydantic model per concept. Do not add a second model with the same data only to copy it into the first.
- Do not store what you can derive: counts (`len()`), totals, or fields that a nested model already holds.
- Build a model with all its fields at once. Do not patch it afterwards with `model_copy(update=...)`.
- For a library's payloads, use the library's types (`ChatCompletionMessageParam`), not `dict[str, str]`.
- Use `Literal[...]` for fixed sets of values: `Status = Literal["clear", "review", "likely_hit"]`. Do not compare free strings.
- Name every state in the `Literal`. Do not add `None` as an extra state: `Literal["off", "allowed", "used_up"]`, not `Literal["auto", "none"] | None`.
- Get the values of a `Literal` with `typing.get_args`. Do not copy them into a list.
- Validate and normalize data once, where it enters the system. Store values in the same form as the input (country codes upper case). After that, trust the types. Do not re-check, do not rebuild a model only to see if it raises, and do not convert case or format when you compare.

## Imports

- Use absolute imports: `from sanction_check import db`, `from sanction_check.settings import get_settings`.
- Order: standard library, third party, `sanction_check`, with a blank line between groups.
- Put imports at the top of the module. No imports inside functions.
- No `import *`.

## Errors

- Fail loudly. Do not catch an exception unless you can handle it in a useful way.
- Do not use bare `except:` or `except Exception:` to hide errors.
- Catch the most specific exception type.
- Raise with a message that names the bad value: `raise ValueError(f"unknown country: {country!r}")`.
- Do not return `None` or `False` to signal an error. Raise.

## Settings and secrets

- Get settings with `get_settings()`. Do not call `Settings()` directly. Do not read `os.environ`.
- The pydantic `Settings` class is the only source of configuration. Django settings are built from `get_settings()`. Do not put values directly in Django settings, and do not read Django `settings` in application code.
- Django is configured in one place only: `sanction_check/db/__init__.py` calls `settings.configure(...)` and `django.setup()`. No other module calls them. Do not set `DJANGO_SETTINGS_MODULE`.
- `Settings` holds only secrets, connection details and values that change per environment or are tuned: database, API keys, model names, thresholds, `top_k`, `log_level`.
- Every other fixed value (URLs, timeouts, batch sizes, limits, weights) is a named constant in the module that uses it. Do not put such values in `Settings`.
- When you add a setting, add it to `.env.example` in the same change. Write optional settings as a commented line with the default value.
- Secrets are `SecretStr`. Call `.get_secret_value()` only where the secret is used. Never log or print a secret.
- Never commit `.env`, `data/` or `eval/results/`.

## Database (Django ORM)

- Use the Django ORM for all database access. **No raw SQL**: no `.raw()`, `.extra()`, `RawSQL`, `connection.cursor()` or `.sql` files.
- Use the Postgres features through Django:
  - trigram: `django.contrib.postgres.search.TrigramSimilarity`, `TrigramExtension`;
  - unaccent: `django.contrib.postgres.lookups.Unaccent`, `UnaccentExtension`;
  - arrays: `django.contrib.postgres.fields.ArrayField`;
  - vectors: `pgvector.django` (`VectorField`, `HnswIndex`, `CosineDistance`, `VectorExtension`).
- When Django has no wrapper for a Postgres function (for example `dmetaphone`), write one `Func` subclass for it in `db/functions.py`. Do not use SQL strings in queries.
- Django models live in `db/models.py`. Pydantic models live in the `schemas/` package, one module per area. Do not mix them.
- Model rules:
  - Field names are `snake_case`. Model names are singular `PascalCase` (`Entity`, `EntityName`).
  - Set `on_delete` and `related_name` on every `ForeignKey`.
  - Use `models.TextChoices` for fixed values such as the status.
  - Declare every index in `Meta.indexes` (`GinIndex(opclasses=["gin_trgm_ops"])`, `HnswIndex`). Do not create indexes by hand.
- Schema changes are migrations only. Run `makemigrations` and commit the migration together with the model change. Create extensions in a migration with the `CreateExtension` operations. Never edit a migration that has been applied. Add a new one.
- A data migration does not import application code. Put the logic it needs in the migration file.
- Put a reusable filter on a custom `QuerySet` and expose it with `as_manager()` (`Entity.objects.with_reg_no(...)`). Do not repeat the same `.filter(...)` in several modules.
- Do filtering, matching and ordering in the database. Do not load full tables into Python.
- No N+1 queries. Use `select_related` / `prefetch_related` when you read relations in a loop.
- For bulk loads, use `bulk_create(..., batch_size=...)`. Use `.iterator()` to read large querysets.
- Put writes that must succeed together in `transaction.atomic()`.

## CLI

- Typer (`main ...`) is the only entry point of the app. There is no `manage.py`, and there are no Django management commands.
- One-off tools that are not part of the app live in `scripts/`, one file each, outside the package. A script runs with `uv run python scripts/<name>.py`, starts with `typer.run`, and writes to the terminal with `typer.echo` like `cli.py`. The app never imports a script.
- Django commands that we need are thin Typer commands that call `django.core.management.call_command` (`main migrate`, `main makemigrations`).
- `cli.py` is thin. A command parses arguments, calls one function and prints its return value. No logic in commands. Writing a file is logic: a function writes it and returns the path, and the command prints the path.
- Command names are kebab-case: `eval run`, `makemigrations`.
- Only `cli.py` and the scripts in `scripts/` write to the terminal, with `typer.echo`. Results go to stdout. Messages go to stderr (`err=True`).
- No `print()` anywhere.

## Logging

- Import the logger from one place: `from sanction_check.helpers.log import log`. Do not call `logging.getLogger` in other modules.
- Log events, not narration. `log.info("loaded %d entities", count)` is good; `log.info("now loading...")` is not.
- Use `%s` arguments, not f-strings, in log calls.
- INFO: one line per step, with counts. DEBUG: one line per item, and full payloads (LLM messages, candidate lists, rationales).
- Log values. Do not log objects whose text is not a value, such as ORM expressions or querysets.

## Files and resources

- Read package files (prompts) with `importlib.resources`, not with relative paths.
- Open text files with `encoding="utf-8"` every time.
- Use `pathlib.Path`, not `os.path`.
- Build a file name from input only after you replace each character that is not a letter, a digit, `.` or `-` with `_`.
- Timestamps in file names are UTC, in the format `%Y%m%dT%H%M%SZ`.

## Prompts

- A prompt uses the domain words and the same tag and field names that the code sends (`<supplier>`, `matched_candidate_id`). When you change a name in one, change it in the other.
- A prompt states each rule once. Two sections must not contradict each other.

## Tooling

- Use `uv` only: `uv sync`, `uv add <pkg>`, `uv run ...`. Do not use `pip`. Do not edit `uv.lock` by hand.
- Ask before you add a dependency. Prefer the standard library and the dependencies that are already in `pyproject.toml`.

```sh
overlay use .venv\Scripts\activate.nu
uv sync
docker compose up -d
uv run main makemigrations
uv run main migrate
uv run main --help
```

## Agent workflow

Before you write code:

1. Read the files you will change, and one similar module, to see how the repo does it.
2. If the task is not clear, or it needs a new dependency, a schema change or a new module, ask first.
3. Search the repo for the condition, constant or steps you are about to write. If they exist, reuse them.

While you write code:

1. Follow this file. Match the code around you.
2. Keep the change to the task. Note unrelated problems in your report. Do not fix them.

Before you report:

1. Run the code you changed (the CLI command, or the eval if it exists). Do not report "done" without running it.
2. Check your diff against this file: no comments, no docstrings, no unused code, no hard-coded values, no `print`, no raw SQL, nothing written twice, no function with more than 4 parameters.
3. If you changed a Django model, run `uv run main makemigrations --check` and make sure the migration is in the diff.
4. If you changed behavior, output or names, update `docs/specs/sanctions-screening.md` and `README.md` in the same change.

Report in this format:

```text
Changed: <files and one line each on what changed>
Ran:     <command> -> <result>
Open:    <questions, problems found, things not done>
```
