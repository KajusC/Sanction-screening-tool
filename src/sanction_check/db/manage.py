from django.db import OperationalError, connections
import typer

from sanction_check.helpers.log import log


def check_database() -> None:
    db_conn = connections["default"]
    try:
        db_conn.cursor()
    except OperationalError:
        log.error("database is not available")
        raise typer.Exit(1)
