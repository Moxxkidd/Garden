"""Open a private managed WebUI session without printing its bearer token."""

import webbrowser

from app.cli.paths import GardenPaths
from app.cli.utils import console, handle_cli_error
from app.cli.web_runtime import WebRuntimeError, WebRuntimeManager
from app.core.settings import get_settings


def ui():
    try:
        runtime = WebRuntimeManager(
            paths=GardenPaths.from_environment(), default_port=get_settings().api_port
        ).ensure()
        webbrowser.open(runtime.browser_url)
        console.print(f"已打开 Garden：{runtime.base_url}（本地私有会话）。")
    except WebRuntimeError as error:
        handle_cli_error(error)
