"""Explicit connection diagnostic; never initializes scan storage."""

import json
from typing import Annotated

import typer

from app.core.settings import get_settings
from app.services.connection_test import test_connection


def connection_test(url: str, json_output: Annotated[bool, typer.Option("--json")] = False):
    """对已授权 URL 发起一次 GET；不跟随跳转、不重试、不创建扫描。"""
    result = test_connection(url, get_settings())
    typer.echo(json.dumps(result, ensure_ascii=False) if json_output else result["message"])
    raise typer.Exit(0 if result["code"] == "ok" else 1)
