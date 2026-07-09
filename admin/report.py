from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from admin.release import list_releasable
from config import PluginSettings
from data_source.student_cache import SyncState
from data_source.students import PendingRequest


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
    releasable: int
    reason_counts: dict[str, int] = field(default_factory=dict)
    samples: list[PendingRequest] = field(default_factory=list)


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
        releasable=len(releasable),
        reason_counts=dict(reason_counter),
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
        f"- 可分批通过（strong）：{data.releasable}",
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
                f"有 {data.releasable} 条 strong pending，可先 /audit release preview",
            ]
        )
    return "\n".join(lines)
