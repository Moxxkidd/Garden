"""Domain enums for Garden persistence and validation."""

from enum import Enum


class TargetType(str, Enum):
    WEB = "web"
    API = "api"
    ADMIN = "admin"
    INTERNAL = "internal"
    OTHER = "other"


class TargetStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class AuthType(str, Enum):
    PASSWORD = "password"
    TOKEN = "token"
    COOKIE = "cookie"
    SSO = "sso"
    OTHER = "other"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SessionStatus(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    INVALID = "invalid"
    ERROR = "error"
    REFRESHED = "refreshed"


class SessionType(str, Enum):
    HTTP_COOKIE_JAR = "http_cookie_jar"
    HTTP_BEARER = "http_bearer"
    PLAYWRIGHT_STORAGE_STATE = "playwright_storage_state"


class LoginFailureReason(str, Enum):
    CONFIG_ERROR = "config_error"
    NETWORK_FAILURE = "network_failure"
    LOGIN_REJECTED = "login_rejected"
    FORM_NOT_FOUND = "form_not_found"
    UNEXPECTED_RESPONSE = "unexpected_response"
    SESSION_EXPIRED = "session_expired"


class AuditEventType(str, Enum):
    LOGIN_ATTEMPT = "login_attempt"
    SESSION_VALIDATION = "session_validation"
    SESSION_REFRESH = "session_refresh"
    FINDING_STATUS_UPDATE = "finding_status_update"
    EVIDENCE_EXPORT = "evidence_export"
    RETEST_RUN = "retest_run"
    REPORT_EXPORT = "report_export"


class AuditEventStatus(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"


class InventoryRunStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class InventoryParameterSource(str, Enum):
    QUERY = "query"
    BODY = "body"
    FORM = "form"


class InventorySubjectType(str, Enum):
    PAGE = "page"
    ENDPOINT = "endpoint"
    PARAMETER = "parameter"


class FindingSeverity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class FindingConfidence(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class FindingStatus(str, Enum):
    NEW = "new"
    TRIAGED = "triaged"
    ACCEPTED = "accepted"
    FIXED = "fixed"
    RETEST_PENDING = "retest-pending"
    CLOSED = "closed"


class RetestStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


class EvidenceType(str, Enum):
    HTTP_EXCHANGE = "http_exchange"
    PAGE_CAPTURE = "page_capture"
