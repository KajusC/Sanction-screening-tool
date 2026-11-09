from pathlib import Path
from typing import Annotated

import typer
import uvicorn
from django.core.management import call_command

from sanction_check.db.manage import check_database
from sanction_check.evaluation.run import run_eval
from sanction_check.loading.embed import embed_missing_names
from sanction_check.loading.opensanctions import load_sources
from sanction_check.schemas.screening_schema import ScreeningInput
from sanction_check.screening.result import write_result
from sanction_check.screening.supplier_screening import find_ranked_candidates, screen_supplier

EVAL_CASES_PATH = Path("eval/cases.jsonl")
API_PORT = 8000

check_database()

app = typer.Typer(no_args_is_help=True)
eval_app = typer.Typer(no_args_is_help=True)
app.add_typer(eval_app, name="eval")


@app.command()
def migrate() -> None:
    call_command("migrate")


@app.command()
def makemigrations(check: bool = False) -> None:
    call_command("makemigrations", "db", check=check)


@app.command()
def load(sources: Annotated[list[str] | None, typer.Argument()] = None) -> None:
    """Load OpenSanctions targets.simple.csv files from the given URLs or paths, or from LOAD_SOURCES."""
    report = load_sources(sources)
    typer.echo(f"{report.entity_count} entities, {report.name_count} names")


@app.command()
def embed() -> None:
    """Embed all names that have no embedding yet."""
    report = embed_missing_names()
    typer.echo(f"{report.name_count} names, {report.token_count} tokens")


@app.command()
def candidates(name: str, country: Annotated[str, typer.Option()], reg_no: str | None = None) -> None:
    """Find and score sanctions candidates for a supplier."""
    supplier = ScreeningInput(name=name, country=country, reg_no=reg_no)

    for candidate in find_ranked_candidates(supplier):
        typer.echo(f"{candidate.score:.3f}  {candidate.name}  {candidate.search_methods}")
        typer.echo(f"    {'; '.join(candidate.reasons)}")


@app.command()
def screen(
    name: str,
    country: Annotated[str, typer.Option()],
    reg_no: str | None = None,
    web_search: Annotated[bool, typer.Option("--web-search")] = False,
) -> None:
    """Screen a supplier against the loaded sanctions lists."""
    supplier = ScreeningInput(name=name, country=country, reg_no=reg_no)

    typer.echo(write_result(screen_supplier(supplier, should_search_web=web_search)))


@app.command()
def serve(host: str = "127.0.0.1", port: int = API_PORT) -> None:
    """Serve the screening API."""
    uvicorn.run("sanction_check.api:app", host=host, port=port)


@eval_app.command("run")
def eval_run(
    cases: Path = EVAL_CASES_PATH,
    web_search: Annotated[bool, typer.Option("--web-search")] = False,
) -> None:
    """Run the eval cases and write the report."""
    typer.echo(run_eval(cases, should_search_web=web_search))
