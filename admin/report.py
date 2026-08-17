from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import re

from admin.release import list_releasable
from config import PluginSettings
from data_source.student_cache import SyncState
from data_source.students import PendingRequest
from graduate.decision import apply_graduate_auto_approve_flag, make_graduate_decision
from graduate.matcher import match_graduate
from graduate.parser import parse_graduate_comment
from graduate.roster_parser import complete_graduate_parse_from_roster


REASON_LABELS = (
    "信息不足",
    "仅姓名",
    "专业弱匹配",
    "非26级",
    "学长家长关键词",
    "凭证冲突",
    "无法解析",
    "QQ辅助",
    "其他",
)


def classify_manual_reason(req: PendingRequest) -> str:
    reason = (req.reason or "").lower()
    text = req.comment or ""
    parsed = req.parsed or {}
    strength = req.match_strength or (req.match or {}).get("strength", "")

    if "关键词" in (req.reason or "") or "学长" in text or "学姐" in text:
        return "学长家长关键词"
    if "非26" in (req.reason or "") or "26级" in (req.reason or ""):
        return "非26级"
    if "冲突" in (req.reason or ""):
        return "凭证冲突"
    if strength == "weak" or "弱匹配" in (req.reason or ""):
        return "专业弱匹配"
    if strength == "auxiliary" or "qq" in reason or "QQ" in (req.reason or ""):
        return "QQ辅助"
    if "无法解析" in (req.reason or "") or "empty" in reason:
        return "无法解析"
    if parsed.get("name") and not parsed.get("student_id") and not parsed.get("notice_no"):
        if not parsed.get("major") and not parsed.get("academy"):
            return "仅姓名"
    if not parsed.get("name") and not parsed.get("student_id") and not parsed.get("notice_no"):
        return "无法解析"
    if "信息不足" in (req.reason or "") or "仅姓名" in (req.reason or ""):
        return "信息不足"
    return "其他"


def _suggestion_for_reason(label: str) -> str:
    mapping = {
        "信息不足": "可提示学生填写姓名+学号或姓名+通知书编号",
        "仅姓名": "请核实学号或通知书编号",
        "专业弱匹配": "可完善专业别名或提示学生填写学号",
        "非26级": "请人工确认是否为26级新生",
        "学长家长关键词": "请人工确认身份",
        "凭证冲突": "请人工核对学号与通知书编号",
        "无法解析": "拒绝并提示重新填写",
        "QQ辅助": "结合 QQ 与姓名人工核实",
        "其他": "请人工审核",
    }
    return mapping.get(label, "请人工审核")


@dataclass
class ReportData:
    days: int
    total: int
    today: int
    pending: int
    processed: int
    auto_approved: int
    admin_approved: int
    rejected: int
    manual_review: int
    ignored: int
    failed: int
    external: int
    releasable: int
    reason_counts: dict[str, int] = field(default_factory=dict)
    samples: list[PendingRequest] = field(default_factory=list)


GRAD_REVIEW_LABELS = {
    "would_auto_now": "现在可自动通过",
    "release_only_needs_admin_notice": "现在可进 release（需管理员确认）",
    "missing_name": "缺姓名",
    "missing_admission_type": "缺录取类型",
    "ambiguous_admission_type": "录取类型占位/歧义",
    "name_not_found": "姓名未命中名单",
    "name_type_not_unique": "姓名+录取类型不唯一",
    "not_grad_profile_or_group": "非研究生通道",
    "other_blocked": "其他阻塞",
}


@dataclass
class GradReviewItem:
    request: PendingRequest
    category: str
    parsed: dict
    match: dict
    decision: str
    should_auto_approve: bool
    table_major: str = ""
    reason: str = ""


@dataclass
class GradReviewData:
    total_reviewed: int
    would_release_now: int
    would_auto_now: int
    release_only_needs_admin_notice: int
    counts: dict[str, int] = field(default_factory=dict)
    samples: list[GradReviewItem] = field(default_factory=list)


async def build_report_data(
    requests_store,
    settings: PluginSettings,
    *,
    days: int = 7,
    sample_limit: int = 5,
) -> ReportData:
    records = await requests_store.list_since(days)
    stats = await requests_store.get_stats()
    reason_counter: Counter[str] = Counter()
    manual = [r for r in records if r.decision == "manual_review"]
    for req in manual:
        reason_counter[classify_manual_reason(req)] += 1

    samples = await requests_store.list_unknown_samples(days, sample_limit)
    releasable = await list_releasable(requests_store, settings)

    return ReportData(
        days=days,
        total=len(records),
        today=await requests_store.count_today(),
        pending=stats.get("pending", 0),
        processed=await requests_store.count_processed(),
        auto_approved=stats.get("auto_approved", 0),
        admin_approved=stats.get("admin_approved", 0),
        rejected=stats.get("reject", 0),
        manual_review=stats.get("manual_review", 0),
        ignored=stats.get("ignored", 0),
        failed=stats.get("failed", 0),
        external=stats.get("external", 0),
        releasable=len(releasable),
        reason_counts=dict(reason_counter),
        samples=samples,
    )


def _audit_approved_request_ids(audit_log) -> set[str]:
    if audit_log is None:
        return set()
    ids: set[str] = set()
    try:
        records = audit_log.read_all()
    except Exception:
        return ids
    for record in records:
        action = str(record.get("action") or "")
        record_type = str(record.get("type") or "")
        if action == "approve" and record.get("ok") is True:
            req_id = record.get("request_id")
            if req_id:
                ids.add(str(req_id))
        if record_type in {"external_approved", "external_join", "already_approved"}:
            req_id = record.get("request_id")
            if req_id:
                ids.add(str(req_id))
    return ids


def _is_grad_review_target(req: PendingRequest, audit_approved_ids: set[str]) -> bool:
    if getattr(req, "profile", "undergraduate") != "graduate":
        return False
    if req.status == "processed" and req.decision == "approve":
        return True
    if req.status == "external":
        return True
    return req.id in audit_approved_ids


def _graduate_category(parsed, match, decision, *, raw_name: str | None = None) -> str:
    raw_type = getattr(parsed, "admission_type_raw", None) or ""
    if not getattr(parsed, "name", None):
        if raw_name:
            return "name_not_found"
        return "missing_name"
    if not getattr(parsed, "admission_type", None):
        if any(token in raw_type for token in ("硕/博", "硕博", "硕or博", "硕或博")):
            return "ambiguous_admission_type"
        return "missing_admission_type"
    if getattr(match, "strength", None) == "strong" and decision.decision == "approve":
        if decision.should_auto_approve:
            return "would_auto_now"
        return "release_only_needs_admin_notice"
    reason = getattr(match, "reason", "") or ""
    if "姓名未命中" in reason:
        return "name_not_found"
    if getattr(match, "candidate_count", 0) > 1 or "多候选" in reason:
        return "name_type_not_unique"
    return "other_blocked"


def _grad_match_dict(match) -> dict:
    student = getattr(match, "matched_student", None)
    return {
        "strength": getattr(match, "strength", ""),
        "candidate_count": getattr(match, "candidate_count", 0),
        "matched_by": list(getattr(match, "matched_by", []) or []),
        "major_name": getattr(student, "major_name", "") if student else "",
        "college": getattr(student, "college", "") if student else "",
    }


async def build_grad_review_data(
    requests_store,
    settings: PluginSettings,
    grad_cache,
    *,
    audit_log=None,
    detail_limit: int = 10,
) -> GradReviewData:
    students = grad_cache.load_students() if grad_cache is not None else []
    audit_approved_ids = _audit_approved_request_ids(audit_log)
    targets = [
        req
        for req in await requests_store.list_all()
        if _is_grad_review_target(req, audit_approved_ids)
    ]
    targets.sort(key=lambda r: r.created_at, reverse=True)

    counts: Counter[str] = Counter()
    samples: list[GradReviewItem] = []
    detail_limit = max(1, min(int(detail_limit or 10), 30))

    for req in targets:
        parsed = parse_graduate_comment(req.comment or "")
        raw_name = parsed.name
        if getattr(settings, "grad_roster_parse_enabled", True):
            parsed = complete_graduate_parse_from_roster(parsed, students)
        match = match_graduate(parsed, students)
        decision = make_graduate_decision(parsed, match, is_target_group=True)
        decision = apply_graduate_auto_approve_flag(decision, "auto", match)
        category = _graduate_category(parsed, match, decision, raw_name=raw_name)
        counts[category] += 1

        if len(samples) < detail_limit:
            match_dict = _grad_match_dict(match)
            item = GradReviewItem(
                request=req,
                category=category,
                parsed=parsed.to_dict(),
                match=match_dict,
                decision=decision.decision,
                should_auto_approve=decision.should_auto_approve,
                table_major=str(match_dict.get("major_name") or ""),
                reason=decision.reason or getattr(match, "reason", ""),
            )
            samples.append(item)

    would_auto = counts.get("would_auto_now", 0)
    release_only = counts.get("release_only_needs_admin_notice", 0)
    return GradReviewData(
        total_reviewed=len(targets),
        would_release_now=would_auto + release_only,
        would_auto_now=would_auto,
        release_only_needs_admin_notice=release_only,
        counts=dict(counts),
        samples=samples,
    )


def format_unknown(data: ReportData, *, sample_limit: int = 5) -> str:
    lines = [
        "未识别/需复核汇总",
        "",
        f"时间范围：最近 {data.days} 天",
        f"总申请：{data.total}",
        f"需人工：{sum(data.reason_counts.values())}",
        "",
        "原因分布：",
    ]
    if not data.reason_counts:
        lines.append("- （无）")
    else:
        for label in REASON_LABELS:
            count = data.reason_counts.get(label, 0)
            if count:
                lines.append(f"- {label}：{count}")
        for label, count in data.reason_counts.items():
            if label not in REASON_LABELS:
                lines.append(f"- {label}：{count}")

    lines.extend(["", "最近样例："])
    if not data.samples:
        lines.append("（无）")
    else:
        for idx, req in enumerate(data.samples[:sample_limit], start=1):
            comment = (req.comment or "")[:80]
            label = classify_manual_reason(req)
            lines.extend(
                [
                    f"[{idx}] comment: “{comment}”",
                    f"原因：{req.reason or label}",
                    f"建议：{_suggestion_for_reason(label)}",
                    "",
                ]
            )
    return "\n".join(lines).rstrip()


def format_report(
    data: ReportData,
    sync_state: SyncState,
    *,
    release_running: bool = False,
) -> str:
    lines = [
        "审核运营报告",
        "",
        f"时间范围：最近 {data.days} 天",
        "",
        "概览：",
        f"- 今日申请：{data.today}",
        f"- 范围内总申请：{data.total}",
        f"- 待处理：{data.pending}",
        f"- 已处理：{data.processed}",
        f"- 自动通过：{data.auto_approved}",
        f"- 管理员通过：{data.admin_approved}",
        f"- 已拒绝：{data.rejected}",
        f"- 需人工：{data.manual_review}",
        f"- 已忽略：{data.ignored}",
        f"- 失败：{data.failed}",
        f"- 外部处理：{data.external}",
        f"- 可分批通过（强匹配）：{data.releasable}",
        f"- 分批任务进行中：{'是' if release_running else '否'}",
        "",
        "需人工原因 Top：",
    ]
    if not data.reason_counts:
        lines.append("- （无）")
    else:
        for label, count in sorted(data.reason_counts.items(), key=lambda x: -x[1])[:8]:
            lines.append(f"- {label}：{count}")

    sync_time = sync_state.last_sync_at or "(无)"
    lines.extend(
        [
            "",
            "同步状态：",
            f"- 最近同步：{sync_time}",
            f"- 结果：{sync_state.last_sync_result or '(无)'}",
            f"- 缓存人数：{sync_state.filtered_count}",
            f"- 来源：{sync_state.last_sync_source or sync_state.source}",
        ]
    )
    if data.releasable:
        lines.extend(
            [
                "",
                "建议：",
                f"有 {data.releasable} 条强匹配待处理，可先 /audit release preview",
            ]
        )
    return "\n".join(lines)


def _mask_qq(value: str) -> str:
    text = re.sub(r"\D", "", value or "")
    if len(text) <= 6:
        return "***" if text else "（未知）"
    return f"{text[:3]}****{text[-3:]}"


def _safe_comment(text: str, limit: int = 80) -> str:
    value = re.sub(r"\d{9,}", lambda m: f"{m.group(0)[:3]}****{m.group(0)[-3:]}", text or "")
    value = re.sub(r"(答案[:：]\s*)([\u4e00-\u9fa5·]{2,4})", r"\1某同学", value)
    value = re.sub(r"(姓名[:：]\s*)([\u4e00-\u9fa5·]{2,4})", r"\1某同学", value)
    value = re.sub(r"^([\u4e00-\u9fa5·]{2,4})(?=\s)", "某同学", value)
    value = value.replace("\r", " ").replace("\n", " ").strip()
    if len(value) > limit:
        return value[:limit] + "…"
    return value or "（空）"


def _format_grad_review_counts(counts: dict[str, int]) -> list[str]:
    lines: list[str] = []
    for key, label in GRAD_REVIEW_LABELS.items():
        count = counts.get(key, 0)
        if count:
            lines.append(f"- {label}：{count}")
    if not lines:
        lines.append("- （无）")
    return lines


def format_grad_review_report(data: GradReviewData) -> str:
    rate = (
        f"{data.would_release_now / data.total_reviewed:.0%}"
        if data.total_reviewed
        else "0%"
    )
    lines = [
        "研究生审核历史复盘",
        "",
        "范围：研究生已通过相关申请（管理员通过 / QQ 侧已同意 / external）",
        "",
        "概览：",
        f"- 复盘样本：{data.total_reviewed}",
        f"- 按当前规则可进 release：{data.would_release_now}（{rate}）",
        f"- 其中可 auto 自动通过：{data.would_auto_now}",
        f"- 其中需管理员确认：{data.release_only_needs_admin_notice}",
        "",
        "分类：",
    ]
    lines.extend(_format_grad_review_counts(data.counts))
    lines.extend(
        [
            "",
            "查看脱敏明细：/audit report grad detail 10",
        ]
    )
    return "\n".join(lines)


def format_grad_review_detail(data: GradReviewData, *, limit: int = 10) -> str:
    limit = max(1, min(int(limit or 10), 30))
    lines = [
        "研究生审核历史复盘明细（脱敏）",
        "",
        f"展示：{min(limit, len(data.samples))}/{data.total_reviewed}",
        "",
    ]
    if not data.samples:
        lines.append("（无样例）")
        return "\n".join(lines)
    for idx, item in enumerate(data.samples[:limit], start=1):
        req = item.request
        label = GRAD_REVIEW_LABELS.get(item.category, item.category)
        lines.extend(
            [
                f"[{idx}] {req.id[:8]}",
                f"时间：{req.created_at}",
                f"状态：{req.status} / {req.decision}",
                f"QQ：{_mask_qq(req.user_id)}",
                f"验证：{_safe_comment(req.comment)}",
                f"原判断：{req.reason or req.match_strength or '（无）'}",
                f"当前分类：{label}",
                f"名单专业：{item.table_major or '（未命中）'}",
                f"当前判断：{item.reason or '（无）'}",
                "",
            ]
        )
    return "\n".join(lines).rstrip()
