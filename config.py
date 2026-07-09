from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

logger = logging.getLogger(__name__)

VALID_MODES = frozenset({"off", "record-only", "manual", "auto"})
VALID_STUDENT_SOURCES = frozenset({"mock", "nju_table"})
DEFAULT_MODE = "record-only"
SECRET_KEYS = frozenset(
    {
        "onebot_access_token",
        "njutable_api_token",
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


@dataclass
class PluginSettings:
    mode: str = DEFAULT_MODE
    student_source: str = "mock"
    target_group_ids: frozenset[str] = frozenset()
    admin_qq_ids: frozenset[str] = frozenset()
    admin_notify: bool = True
    onebot_http_url: str = "http://127.0.0.1:3000"
    onebot_access_token: str = ""
    http_timeout_ms: int = 10000
    http_retries: int = 2
    http_retry_delay_ms: int = 500
    njutable_server_url: str = "https://cloud.seatable.io"
    njutable_api_token: str = ""
    njutable_table_name: str = "考生信息-校对表"
    njutable_view_name: str = ""
    njutable_allowed_statuses: tuple[str, ...] = ("对外公布",)
    njutable_page_size: int = 1000
    njutable_timeout_ms: int = 10000
    njutable_cols: NjuTableColMapping = field(default_factory=NjuTableColMapping)
    probe_enabled: bool = True
    log_raw_event: bool = False
    max_recent_events: int = 20

    def __repr__(self) -> str:
        return f"PluginSettings(mode={self.mode!r}, student_source={self.student_source!r}, target_groups={len(self.target_group_ids)})"


def mask_secret(value: str, visible: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= visible:
        return "***"
    return value[:visible] + "***"


def redact_tokens_in_string(text: str, settings: PluginSettings | None = None) -> str:
    result = text
    if settings:
        for token in (settings.onebot_access_token, settings.njutable_api_token):
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
    )
    return PluginSettings(
        mode=_normalize_mode(config.get("mode")),
        student_source=_normalize_student_source(config.get("student_source")),
        target_group_ids=parse_numeric_ids(
            str(config.get("target_group_ids", "")), "target_group_ids"
        ),
        admin_qq_ids=parse_numeric_ids(str(config.get("admin_qq_ids", "")), "admin_qq_ids"),
        admin_notify=bool(config.get("admin_notify", True)),
        onebot_http_url=str(config.get("onebot_http_url", "http://127.0.0.1:3000")).strip(),
        onebot_access_token=str(config.get("onebot_access_token", "")).strip(),
        http_timeout_ms=_clamp_int(config.get("http_timeout_ms"), 10000, minimum=1),
        http_retries=_clamp_int(config.get("http_retries"), 2, minimum=0),
        http_retry_delay_ms=_clamp_int(config.get("http_retry_delay_ms"), 500, minimum=0),
        njutable_server_url=str(
            config.get("njutable_server_url", "https://cloud.seatable.io")
        ).strip(),
        njutable_api_token=str(config.get("njutable_api_token", "")).strip(),
        njutable_table_name=str(config.get("njutable_table_name", "考生信息-校对表")).strip(),
        njutable_view_name=str(config.get("njutable_view_name", "")).strip(),
        njutable_allowed_statuses=parse_csv(
            str(config.get("njutable_allowed_statuses", "对外公布"))
        ) or ("对外公布",),
        njutable_page_size=_clamp_int(config.get("njutable_page_size"), 1000, minimum=1, maximum=1000),
        njutable_timeout_ms=_clamp_int(config.get("njutable_timeout_ms"), 10000, minimum=1),
        njutable_cols=cols,
        probe_enabled=bool(config.get("probe_enabled", True)),
        log_raw_event=bool(config.get("log_raw_event", False)),
        max_recent_events=_clamp_int(config.get("max_recent_events"), 20, minimum=1),
    )


def get_effective_mode(settings: PluginSettings, runtime_mode: str | None) -> tuple[str, str]:
    if runtime_mode and runtime_mode in VALID_MODES:
        return runtime_mode, "runtime"
    return settings.mode, "plugin_config"


def sanitize_config_for_display(settings: PluginSettings) -> dict[str, Any]:
    return {
        "mode": settings.mode,
        "student_source": settings.student_source,
        "target_group_ids": sorted(settings.target_group_ids),
        "admin_qq_ids": sorted(settings.admin_qq_ids),
        "admin_notify": settings.admin_notify,
        "onebot_http_url": settings.onebot_http_url,
        "onebot_access_token": mask_secret(settings.onebot_access_token),
        "njutable_server_url": settings.njutable_server_url,
        "njutable_api_token": mask_secret(settings.njutable_api_token),
        "njutable_table_name": settings.njutable_table_name,
        "njutable_view_name": settings.njutable_view_name or "(未设置)",
        "probe_enabled": settings.probe_enabled,
        "log_raw_event": settings.log_raw_event,
    }
