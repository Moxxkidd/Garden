"""Load command services only when selected, keeping doctor independent of storage."""

from importlib import import_module

from typer.core import TyperGroup
from typer.main import get_command, get_group

_GROUPS = {
    "target": "targets",
    "cred": "credentials",
    "job": "jobs",
    "login": "login",
    "session": "sessions",
    "inventory": "inventory",
    "checks": "checks",
    "findings": "findings",
    "evidence": "evidence",
    "retest": "retest",
    "report": "report",
    "baseline": "baseline",
    "db": "database",
}
_COMMANDS = {"scan", "coverage", "stop"}


class GardenCommandGroup(TyperGroup):
    def list_commands(self, ctx):
        return sorted(set(super().list_commands(ctx)) | set(_GROUPS) | _COMMANDS)

    def get_command(self, ctx, cmd_name):
        existing = super().get_command(ctx, cmd_name)
        if existing is not None:
            return existing
        if cmd_name in _GROUPS:
            command = get_group(import_module(f"app.cli.{_GROUPS[cmd_name]}").app)
        elif cmd_name in _COMMANDS:
            import typer

            container = typer.Typer()
            container.command(cmd_name)(getattr(import_module(f"app.cli.{cmd_name}"), cmd_name))
            command = get_command(container)
        else:
            return None
        command.name = cmd_name
        self.add_command(command, cmd_name)
        return command
