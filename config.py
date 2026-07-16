from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

logger = logging.getLogger(__name__)

VALID_MODES = frozenset({"off", "record-only", "manual", "auto"})
VALID_STUDENT_SOURCES = frozenset({"mock", "nju_table"})
VALID_ACTION_BACKENDS = frozenset({"astrbot_adapter", "http"})
DEFAULT_MODE = "record-only"
DEFAULT_ACTION_BACKEND = "astrbot_adapter"
SECRET_KEYS = frozenset(
    {
        "onebot_access_token",
        "njutable_api_token",
        "grad_njutable_api_token",
        "flag",
        "token",
        "access_token",
        "authorization",
        "Authorization",
        "onebot_access_token",
    }
)
QQ_ID_PATTERN = re.compile(r"^\d+$")


@dataclass(frozen=True)
class NjuTableColMapping:
    status: str = "考生状态"
    notice_no: str = "通知书编号"
    exam_no: str = "考生号"
    name: str = "姓名"
    gender: str = "性别"
    origin: str = "生源地"
    subject: str = "科类名称"
    batch: str = "批次名称"
    major: str = "通知书专业"
    score: str = "成绩"
    middle_school: str = "中学名称"
    student_id: str = "学号"
    academy: str = "书院"
    qq: str = "QQ"


@dataclass
class PluginSettings:
    mode: str = DEFAULT_MODE
    student_source: str = "mock"
    target_group_ids: frozenset[str] = frozenset()
    admin_qq_ids: frozenset[str] = frozenset()
    admin_notify: bool = True
    onebot_action_backend: str = DEFAULT_ACTION_BACKEND
    onebot_http_url: str = ""
    onebot_access_token: str = ""
    http_timeout_ms: int = 10000
    http_retries: int = 2
    http_retry_delay_ms: int = 500
    njutable_server_url: str = "https://table.nju.edu.cn"
    njutable_api_token: str = ""
    njutable_table_name: str = "考生信息-校对表"
    njutable_view_name: str = ""
    njutable_allowed_statuses: tuple[str, ...] = ("对外公布",)
    njutable_ignore_status_filter: bool = False
    njutable_page_size: int = 1000
    njutable_timeout_ms: int = 10000
    njutable_cols: NjuTableColMapping = field(default_factory=NjuTableColMapping)
    probe_enabled: bool = True
    log_raw_event: bool = False
    max_recent_events: int = 20
    batch_approve_interval_ms: int = 3000
    batch_approve_max_count: int = 20
    auto_sync_enabled: bool = False
    auto_sync_on_startup: bool = False
    auto_sync_interval_minutes: int = 360
    auto_sync_notify_admin: bool = False
    reapply_debounce_seconds: int = 15
    audit_list_reconcile_timeout_ms: int = 4000
    audit_list_reject_confirm_snapshots: int = 2
    audit_list_reject_wait_seconds: int = 30
    # Graduate audit (fully separated from undergraduate NJUTable / groups / cache)
    grad_enabled: bool = False
    grad_target_group_ids: frozenset[str] = frozenset()
    grad_student_source: str = "nju_table"
    grad_njutable_server_url: str = "https://table.nju.edu.cn"
    grad_njutable_api_token: str = ""
    grad_njutable_table_name: str = ""
    grad_njutable_view_name: str = ""
    grad_njutable_page_size: int = 1000
    grad_njutable_timeout_ms: int = 10000
    grad_col_id: str = "id"
    grad_col_admission_type: str = "录取类型"
    grad_col_college: str = "录取学院"
    grad_col_major_code: str = "录取专业代码"
    grad_col_major_name: str = "录取专业名称"
    grad_col_name: str = "姓名"
    grad_col_short_code_id: str = "_short_code_id"
    grad_col_imported_at: str = "_imported_at"

    def __repr__(self) -> str:
        return (
            f"PluginSettings(mode={self.mode!r}, student_source={self.student_source!r}, "
            f"target_groups={len(self.target_group_ids)}, "
            f"grad_enabled={self.grad_enabled}, grad_groups={len(self.grad_target_group_ids)})"
        )


def mask_secret(value: str, visible: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= visible:
        return "***"
    return value[:visible] + "***"


def redact_tokens_in_string(text: str, settings: PluginSettings | None = None) -> str:
    result = text
    if settings:
        for token in (
            settings.onebot_access_token,
            settings.njutable_api_token,
            settings.grad_njutable_api_token,
        ):
            if token:
                result = result.replace(token, "***")
    for pattern in (r"Bearer\s+\S+", r"access_token['\"]?\s*[:=]\s*\S+"):
        result = re.sub(pattern, "Bearer ***", result, flags=re.IGNORECASE)
    return result


def parse_numeric_ids(value: str, field_name: str) -> frozenset[str]:
    if not value:
        return frozenset()
    ids: set[str] = set()
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        if not QQ_ID_PATTERN.match(item):
            logger.warning("[%s] ignored invalid id: %s", field_name, item)
            continue
        ids.add(item)
    return frozenset(ids)


def parse_csv(value: str) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _normalize_mode(value: Any) -> str:
    mode = str(value or DEFAULT_MODE).strip()
    if mode not in VALID_MODES:
        logger.warning("Invalid mode %r, fallback to %s", mode, DEFAULT_MODE)
        return DEFAULT_MODE
    return mode


def _normalize_student_source(value: Any) -> str:
    source = str(value or "mock").strip()
    if source not in VALID_STUDENT_SOURCES:
        logger.warning("Invalid student_source %r, fallback to mock", source)
        return "mock"
    return source


def _normalize_action_backend(value: Any) -> str:
    backend = str(value or DEFAULT_ACTION_BACKEND).strip()
    if backend not in VALID_ACTION_BACKENDS:
        logger.warning(
            "Invalid onebot_action_backend %r, fallback to %s",
            backend,
            DEFAULT_ACTION_BACKEND,
        )
        return DEFAULT_ACTION_BACKEND
    return backend


def mask_http_url(url: str) -> str:
    if not url:
        return "(未设置)"
    if "://" in url:
        scheme, rest = url.split("://", 1)
        if "@" in rest:
            _, host_part = rest.rsplit("@", 1)
            return f"{scheme}://***@{host_part.split('/')[0]}"
        return f"{scheme}://{rest.split('/')[0]}"
    return url.split("/")[0]


def _clamp_int(value: Any, default: int, minimum: int = 0, maximum: int | None = None) -> int:
    try:
        num = int(value)
    except (TypeError, ValueError):
        return default
    if num < minimum:
        return minimum
    if maximum is not None and num > maximum:
        return maximum
    return num


def load_settings(config: Mapping[str, Any]) -> PluginSettings:
    cols = NjuTableColMapping(
        status=str(config.get("njutable_col_status", "考生状态")),
        notice_no=str(config.get("njutable_col_notice_no", "通知书编号")),
        exam_no=str(config.get("njutable_col_exam_no", "考生号")),
        name=str(config.get("njutable_col_name", "姓名")),
        gender=str(config.get("njutable_col_gender", "性别")),
        origin=str(config.get("njutable_col_origin", "生源地")),
        subject=str(config.get("njutable_col_subject", "科类名称")),
        batch=str(config.get("njutable_col_batch", "批次名称")),
        major=str(config.get("njutable_col_major", "通知书专业")),
        score=str(config.get("njutable_col_score", "成绩")),
        middle_school=str(config.get("njutable_col_middle_school", "中学名称")),
        student_id=str(config.get("njutable_col_student_id", "学号")),
        academy=str(config.get("njutable_col_academy", "书院")),
        qq=str(config.get("njutable_col_qq", "QQ")),
    )
    return PluginSettings(
        mode=_normalize_mode(config.get("mode")),
        student_source=_normalize_student_source(config.get("student_source")),
        target_group_ids=parse_numeric_ids(
            str(config.get("target_group_ids", "")), "target_group_ids"
        ),
        admin_qq_ids=parse_numeric_ids(str(config.get("admin_qq_ids", "")), "admin_qq_ids"),
        admin_notify=bool(config.get("admin_notify", True)),
        onebot_action_backend=_normalize_action_backend(
            config.get("onebot_action_backend", DEFAULT_ACTION_BACKEND)
        ),
        onebot_http_url=str(config.get("onebot_http_url", "")).strip(),
        onebot_access_token=str(config.get("onebot_access_token", "")).strip(),
        http_timeout_ms=_clamp_int(config.get("http_timeout_ms"), 10000, minimum=1),
        http_retries=_clamp_int(config.get("http_retries"), 2, minimum=0),
        http_retry_delay_ms=_clamp_int(config.get("http_retry_delay_ms"), 500, minimum=0),
        njutable_server_url=str(
            config.get("njutable_server_url", "https://table.nju.edu.cn")
        ).strip(),
        njutable_api_token=str(config.get("njutable_api_token", "")).strip(),
        njutable_table_name=str(config.get("njutable_table_name", "考生信息-校对表")).strip(),
        njutable_view_name=str(config.get("njutable_view_name", "")).strip(),
        njutable_allowed_statuses=parse_csv(
            str(config.get("njutable_allowed_statuses", "对外公布"))
        ) or ("对外公布",),
        njutable_ignore_status_filter=bool(config.get("njutable_ignore_status_filter", False)),
        njutable_page_size=_clamp_int(config.get("njutable_page_size"), 1000, minimum=1, maximum=1000),
        njutable_timeout_ms=_clamp_int(config.get("njutable_timeout_ms"), 10000, minimum=1),
        njutable_cols=cols,
        probe_enabled=bool(config.get("probe_enabled", True)),
        log_raw_event=bool(config.get("log_raw_event", False)),
        max_recent_events=_clamp_int(config.get("max_recent_events"), 20, minimum=1),
        batch_approve_interval_ms=_clamp_int(
            config.get("batch_approve_interval_ms"), 3000, minimum=0
        ),
        batch_approve_max_count=_clamp_int(
            config.get("batch_approve_max_count"), 20, minimum=1, maximum=100
        ),
        auto_sync_enabled=bool(config.get("auto_sync_enabled", False)),
        auto_sync_on_startup=bool(config.get("auto_sync_on_startup", False)),
        auto_sync_interval_minutes=_clamp_int(
            config.get("auto_sync_interval_minutes"), 360, minimum=10
        ),
        auto_sync_notify_admin=bool(config.get("auto_sync_notify_admin", False)),
        reapply_debounce_seconds=_clamp_int(
            config.get("reapply_debounce_seconds"), 15, minimum=0, maximum=86400
        ),
        audit_list_reconcile_timeout_ms=_clamp_int(
            config.get("audit_list_reconcile_timeout_ms"), 4000, minimum=50, maximum=30000
        ),
        audit_list_reject_confirm_snapshots=_clamp_int(
            config.get("audit_list_reject_confirm_snapshots"), 2, minimum=2, maximum=10
        ),
        audit_list_reject_wait_seconds=_clamp_int(
            config.get("audit_list_reject_wait_seconds"), 30, minimum=0, maximum=3600
        ),
        grad_enabled=bool(config.get("grad_enabled", False)),
        grad_target_group_ids=parse_numeric_ids(
            str(config.get("grad_target_group_ids", "")), "grad_target_group_ids"
        ),
        grad_student_source="nju_table",
        grad_njutable_server_url=str(
            config.get("grad_njutable_server_url", "https://table.nju.edu.cn")
        ).strip(),
        grad_njutable_api_token=str(config.get("grad_njutable_api_token", "")).strip(),
        grad_njutable_table_name=str(config.get("grad_njutable_table_name", "")).strip(),
        grad_njutable_view_name=str(config.get("grad_njutable_view_name", "")).strip(),
        grad_njutable_page_size=_clamp_int(
            config.get("grad_njutable_page_size"), 1000, minimum=1, maximum=1000
        ),
        grad_njutable_timeout_ms=_clamp_int(
            config.get("grad_njutable_timeout_ms"), 10000, minimum=1
        ),
        grad_col_id=str(config.get("grad_col_id", "id")),
        grad_col_admission_type=str(config.get("grad_col_admission_type", "录取类型")),
        grad_col_college=str(config.get("grad_col_college", "录取学院")),
        grad_col_major_code=str(config.get("grad_col_major_code", "录取专业代码")),
        grad_col_major_name=str(config.get("grad_col_major_name", "录取专业名称")),
        grad_col_name=str(config.get("grad_col_name", "姓名")),
        grad_col_short_code_id=str(config.get("grad_col_short_code_id", "_short_code_id")),
        grad_col_imported_at=str(config.get("grad_col_imported_at", "_imported_at")),
    )


def validate_settings(settings: PluginSettings) -> list[str]:
    warnings: list[str] = []
    if settings.onebot_action_backend == "http" and not settings.onebot_http_url:
        warnings.append(
            "onebot_action_backend=http 但未配置 onebot_http_url，HTTP action 不可用"
        )
    if settings.grad_enabled:
        overlap = settings.target_group_ids & settings.grad_target_group_ids
        if overlap:
            warnings.append(
                "target_group_ids 与 grad_target_group_ids 重叠："
                + ",".join(sorted(overlap))
                + "（重叠群将不处理，避免误审）"
            )
    if settings.grad_enabled and not settings.grad_target_group_ids:
        warnings.append("grad_enabled=true 但未配置 grad_target_group_ids")
    if settings.grad_enabled and not settings.grad_njutable_api_token:
        warnings.append("grad_enabled=true 但未配置 grad_njutable_api_token")
    if settings.grad_enabled and not settings.grad_njutable_table_name:
        warnings.append("grad_enabled=true 但未配置 grad_njutable_table_name")
    return warnings


def get_effective_mode(settings: PluginSettings, runtime_mode: str | None) -> tuple[str, str]:
    if runtime_mode and runtime_mode in VALID_MODES:
        return runtime_mode, "runtime"
    return settings.mode, "plugin_config"


def sanitize_config_for_display(settings: PluginSettings) -> dict[str, Any]:
    result: dict[str, Any] = {
        "mode": settings.mode,
        "student_source": settings.student_source,
        "target_group_ids": sorted(settings.target_group_ids),
        "admin_qq_ids": sorted(settings.admin_qq_ids),
        "admin_notify": settings.admin_notify,
        "onebot_action_backend": settings.onebot_action_backend,
        "njutable_server_url": settings.njutable_server_url,
        "njutable_api_token": mask_secret(settings.njutable_api_token),
        "njutable_table_name": settings.njutable_table_name,
        "njutable_view_name": settings.njutable_view_name or "(未设置)",
        "grad_enabled": settings.grad_enabled,
        "grad_target_group_ids": sorted(settings.grad_target_group_ids),
        "grad_njutable_server_url": settings.grad_njutable_server_url,
        "grad_njutable_api_token": mask_secret(settings.grad_njutable_api_token),
        "grad_njutable_table_name": settings.grad_njutable_table_name or "(未设置)",
        "probe_enabled": settings.probe_enabled,
        "log_raw_event": settings.log_raw_event,
    }
    if settings.onebot_action_backend == "http":
        result["onebot_http_url"] = mask_http_url(settings.onebot_http_url)
        result["onebot_access_token"] = mask_secret(settings.onebot_access_token) or "(未设置)"
    return result
