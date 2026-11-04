"""An explicitly requested, single GET using the scan destination policy."""

import importlib.util

import httpx

from app.core.errors import InputValidationError, TargetPolicyError
from app.core.settings import Settings
from app.services.scan_network import TargetNetworkPolicy


def test_connection(url: str, settings: Settings) -> dict:
    policy = TargetNetworkPolicy(settings)
    try:
        preflight = policy.preflight(url)
    except (InputValidationError, TargetPolicyError):
        return {
            "code": "target_policy_error",
            "message": "地址无效、解析失败或超出允许范围；未发送请求。",
        }
    proxy = policy.proxy_for_url(preflight.normalized_url)
    if (
        proxy
        and proxy.startswith(("socks5:", "socks5h:"))
        and importlib.util.find_spec("socksio") is None
    ):
        return {
            "code": "proxy_dependency_missing",
            "message": "SOCKS 支持未安装，请重新安装或升级 Garden。",
        }
    try:
        with httpx.Client(
            proxy=proxy,
            trust_env=False,
            timeout=settings.scan_request_timeout_seconds,
            follow_redirects=False,
        ) as client:
            with client.stream("GET", preflight.normalized_url) as response:
                status = response.status_code
    except httpx.ProxyError:
        return {
            "code": "proxy_unavailable",
            "message": "代理不可用，请检查显式代理地址、认证及代理进程。",
        }
    except httpx.TransportError:
        return {
            "code": "proxy_unavailable" if proxy else "connection_failed",
            "message": "经代理连接失败，请检查代理及其目标连通性。"
            if proxy
            else "连接失败，请检查目标是否可达。",
        }
    return {
        "code": "target_http_error" if status >= 400 else "ok",
        "status_code": status,
        "message": f"目标返回 HTTP {status}；仅测试一次连接，不代表扫描或覆盖完成。",
    }
