"""数据库迁移 CLI 命令。"""

import typer

from app.cli.utils import console
from app.core.settings import get_settings
from app.db.bootstrap import (
    get_database_revision,
    stamp_existing_database,
    upgrade_database,
)

app = typer.Typer(help="Manage Garden database migrations.")


@app.command("upgrade")
def upgrade(revision: str = typer.Argument("head")) -> None:
    """Upgrade the configured database to a migration revision."""
    settings = get_settings()
    upgrade_database(settings.database_url, revision)
    console.print(f"Database upgraded to {revision}.")


@app.command("stamp-existing")
def stamp_existing(revision: str = typer.Argument("0001")) -> None:
    """Mark an existing schema as the baseline without changing its tables."""
    settings = get_settings()
    stamp_existing_database(settings.database_url, revision)
    console.print(f"Existing database stamped at {revision}.")


@app.command("current")
def current() -> None:
    """Show the configured database migration revision."""
    revision = get_database_revision(get_settings().database_url)
    console.print(revision or "unversioned")
