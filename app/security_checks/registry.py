"""Registry for built-in low-risk checks."""

from __future__ import annotations

from app.security_checks.administrative_surface import AdministrativeSurfaceCheck
from app.security_checks.auth_headers_cache import AuthenticatedHeadersCacheCheck
from app.security_checks.base import LowRiskCheckPlugin
from app.security_checks.debug_leakage import DebugLeakageCheck
from app.security_checks.idor_indicators import IdorIndicatorsCheck
from app.security_checks.sensitive_disclosure import SensitiveDisclosureCheck
from app.security_checks.upload_import import UploadImportCheck


def get_registered_checks() -> list[LowRiskCheckPlugin]:
    return [
        DebugLeakageCheck(),
        AuthenticatedHeadersCacheCheck(),
        AdministrativeSurfaceCheck(),
        IdorIndicatorsCheck(),
        UploadImportCheck(),
        SensitiveDisclosureCheck(),
    ]
