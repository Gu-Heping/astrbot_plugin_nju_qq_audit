from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING

from astrbot.api import logger

from config import PluginSettings, get_effective_mode
from admin.action_error import classify_action_failure
from core.decision import apply_auto_approve_flag, make_decision, should_auto_approve
from core.matcher import MatchResult, match_student
from core.parser import parse_application_comment
from core.reconcile import ReconcileResult
from core.version import (
    DUPLICATE_POLICY_VERSION,
    RECONCILE_LOGIC_VERSION,
    REAPPLY_CHECK_TERMINAL_STATUSES,
)
from data_source.njutable_provider import load_students_for_audit
from data_source.students import ActionResult, PendingRequest
from onebot.event_extract import GroupJoinRequest
from onebot.member_info import is_user_in_group
from storage.audit_log import utc_now_iso
from storage.requests_store import RequestsStore, new_request_id

if TYPE_CHECKING:
    from admin.notify import AdminNotifier
    from data_source.student_cache import StudentCache
    from onebot.actions import ActionClient
    from storage.audit_log import AuditLog
    from storage.list_cache import AdminListCacheStore
    from storage.runtime_store import RuntimeStore


def _action_payload(result: ActionResult) -> dict:
    return {
        "ok": result.ok,
        "retcode": result.retcode,
        "message": result.message,
    }


def _external_join_message(
    notice_sub_type: str | None, operator_id: str | None
) -> str:
    inner = "非 bot 审批"
    if notice_sub_type:
        inner += f"，notice_sub_type={notice_sub_type}"
    if operator_id:
        inner += f"，操作者 QQ：{operator_id}"
    return f"QQ 侧已入群（{inner}）"


class AuditPipeline:
    def __init__(
        self,
        settings: PluginSettings,
        requests: RequestsStore,
        audit: AuditLog,
        runtime: RuntimeStore,
        cache: StudentCache,
        actions: ActionClient,
        notifier: AdminNotifier,
    ) -> None:
        self.settings = settings
        self.requests = requests
        self.audit = audit
        self.runtime = runtime
        self.cache = cache
        self.actions = actions
        self.notifier = notifier

    def reload_settings(
        self,
        settings: PluginSettings,
        actions: ActionClient | None = None,
        notifier: AdminNotifier | None = None,
    ) -> None:
        self.settings = settings
        if actions is not None:
            self.actions = actions
        if notifier is not None:
            self.notifier = notifier

    def _effective_mode(self) -> tuple[str, str]:
        return get_effective_mode(self.settings, self.runtime.get_mode_override())

    async def _is_user_in_group(self, group_id: str, user_id: str) -> bool | None:
        try:
            if not hasattr(self.actions, "get_group_member_info"):
                return None
            result = await self.actions.get_group_member_info(group_id, user_id)
            return is_user_in_group(result)
        except Exception:
            logger.warning(
                "[audit] get_group_member_info failed group=%s user=%s",
                group_id,
                user_id,
                exc_info=True,
            )
            return None

    async def _ignore_duplicate_terminal(
        self,
        existing: PendingRequest,
        event: GroupJoinRequest,
        *,
        reason: str,
    ) -> None:
        logger.info(
            "[audit] duplicate request ignored request=%s status=%s reason=%s",
            existing.id,
            existing.status,
            reason,
        )
        await self.audit.append(
            {
                "type": "duplicate_request_ignored",
                "request_id": existing.id,
                "group_id": event.group_id,
                "user_id": event.user_id,
                "status": existing.status,
                "reason": reason,
            }
        )

    async def handle_group_request(self, event: GroupJoinRequest) -> None:
        if not self.settings.target_group_ids:
            logger.debug("[audit] target_group_ids empty, skip request")
            return
        if event.group_id not in self.settings.target_group_ids:
            logger.debug("[audit] non-target group ignored: %s", event.group_id)
            await self.audit.append(
                {
                    "type": "request_received",
                    "group_id": event.group_id,
                    "user_id": event.user_id,
                    "decision": "ignored",
                    "reason": "非目标群，忽略",
                }
            )
            return

        mode, _ = self._effective_mode()
        if mode == "off":
            logger.debug("[audit] mode off, skip processing")
            return

        existing = await self.requests.get_by_flag(event.flag)
        comment_text = event.comment or ""
        if existing:
            if existing.status == "processed":
                await self._ignore_duplicate_terminal(
                    existing, event, reason="same flag already processed"
                )
                return

            if existing.status == "external":
                previous_id = await self.requests.release_flag(event.flag)
                await self.audit.append(
                    {
                        "type": "reapplication_after_terminal",
                        "previous_request_id": previous_id or existing.id,
                        "previous_status": existing.status,
                        "group_id": event.group_id,
                        "user_id": event.user_id,
                        "flag": event.flag,
                        "reason": "external group_request reopened",
                    }
                )
                logger.info(
                    "[audit] reapplication after external previous=%s flag=%s policy=%s",
                    existing.id,
                    event.flag,
                    DUPLICATE_POLICY_VERSION,
                )
                await self._audit_and_act(event)
                return

            if existing.status in REAPPLY_CHECK_TERMINAL_STATUSES:
                in_group = await self._is_user_in_group(event.group_id, event.user_id)
                if in_group is True:
                    await self._ignore_duplicate_terminal(
                        existing, event, reason="same flag terminal and user still in group"
                    )
                    return
                if in_group is None:
                    logger.warning(
                        "[audit] cannot confirm membership for reapply request=%s; "
                        "allowing new application",
                        existing.id,
                    )
                previous_id = await self.requests.release_flag(event.flag)
                await self.audit.append(
                    {
                        "type": "reapplication_after_terminal",
                        "previous_request_id": previous_id or existing.id,
                        "previous_status": existing.status,
                        "group_id": event.group_id,
                        "user_id": event.user_id,
                        "flag": event.flag,
                        "in_group": in_group,
                    }
                )
                logger.info(
                    "[audit] reapplication after terminal previous=%s status=%s flag=%s",
                    existing.id,
                    existing.status,
                    event.flag,
                )
                await self._audit_and_act(event)
                return

            if existing.status == "pending" and not existing.processed_at:
                if existing.comment == comment_text:
                    return
                logger.info(
                    "[audit] duplicate pending comment changed request=%s",
                    existing.id,
                )
                await self.audit.append(
                    {
                        "type": "duplicate_pending_comment_changed",
                        "request_id": existing.id,
                        "group_id": event.group_id,
                        "user_id": event.user_id,
                        "old_comment": (existing.comment or "")[:200],
                        "new_comment": comment_text[:200],
                    }
                )
                return

            if existing.status == "failed":
                retryable = await self.requests.ensure_retryable(existing.id)
                if retryable is None:
                    return
                await self._audit_and_act(
                    event, resubmit=True, reapply=False, request_id=existing.id
                )
                return

            logger.info(
                "[audit] duplicate request ignored request=%s status=%s",
                existing.id,
                existing.status,
            )
            await self.audit.append(
                {
                    "type": "duplicate_request_ignored",
                    "request_id": existing.id,
                    "group_id": event.group_id,
                    "user_id": event.user_id,
                    "status": existing.status,
                    "reason": "same flag not actionable",
                }
            )
            return

        active_pending = await self.requests.find_active_pending_by_user_group(
            event.group_id, event.user_id
        )
        if active_pending and active_pending.flag != event.flag:
            await self.requests.supersede_pending(active_pending.flag, event.flag)

        await self._audit_and_act(event)

    async def _audit_and_act(
        self,
        event: GroupJoinRequest,
        *,
        resubmit: bool = False,
        reapply: bool = False,
        request_id: str | None = None,
    ) -> None:
        mode, _ = self._effective_mode()
        students = load_students_for_audit(self.settings, self.cache)
        parsed = parse_application_comment(event.comment or "")
        match = match_student(parsed, students, applicant_user_id=event.user_id)
        decision = make_decision(parsed, match, is_target_group=True)
        decision = apply_auto_approve_flag(decision, mode, match)

        req_id = request_id or new_request_id()
        pending = PendingRequest(
            id=req_id,
            group_id=event.group_id,
            user_id=event.user_id,
            comment=event.comment or "",
            flag=event.flag,
            sub_type=event.sub_type,
            parsed={
                "name": parsed.name,
                "student_id": parsed.student_id,
                "notice_no": parsed.notice_no,
                "major": parsed.major,
                "academy": parsed.academy,
                "notice_no_candidates": parsed.notice_no_candidates,
            },
            match={
                "strength": match.strength,
                "confidence": match.confidence,
                "reason": match.reason,
                "matched_by": match.matched_by,
                "matched_student_key": match.matched_student_key,
                "matched_student_id": (
                    match.matched_student.student_id if match.matched_student else None
                ),
                "qq_match": match.qq_match,
            },
            decision=decision.decision,
            confidence=decision.confidence,
            reason=decision.reason,
            mode=mode,
            status="pending",
            created_at=utc_now_iso(),
            match_strength=match.strength,
            matched_student_key=decision.matched_student_key,
        )
        if reapply or resubmit:
            update_dict = RequestsStore._request_to_dict(pending)
            update_dict["action_result"] = None
            update_dict["last_action_result"] = None
            update_dict["last_action_at"] = None
            update_dict["retry_count"] = 0
            update_dict["processed_at"] = None
            update_dict["status"] = "pending"
            await self.requests.update_by_id(req_id, update_dict)
        else:
            await self.requests.upsert(pending)

        await self.audit.append(
            {
                "type": "decision_made" if not resubmit else "request_received",
                "request_id": req_id,
                "group_id": event.group_id,
                "user_id": event.user_id,
                "comment": event.comment,
                "decision": decision.decision,
                "confidence": decision.confidence,
                "reason": decision.reason,
                "mode": mode,
                "match_strength": match.strength,
            }
        )

        logger.info(
            "[audit] request=%s decision=%s mode=%s reason=%s",
            req_id,
            decision.decision,
            mode,
            decision.reason,
        )

        if mode in {"manual", "record-only"} or decision.decision == "manual_review":
            if self.settings.admin_notify and decision.decision == "manual_review":
                await self.notifier.notify_manual_review(
                    request_id=req_id,
                    group_id=event.group_id,
                    user_id=event.user_id,
                    comment=event.comment,
                    parsed=pending.parsed,
                    reason=decision.reason,
                )
            return

        if should_auto_approve(decision.decision, mode, match) and event.sub_type == "add":
            action_result = await self.actions.set_group_add_request(
                event.flag, event.sub_type, True, "自动审核通过"
            )
            await self._record_action_outcome(
                pending,
                action_result,
                admin_user_id=None,
                admin_command="auto_approve",
                reject_decision=None,
            )
            await self.audit.append(
                {
                    "type": "action_called",
                    "request_id": req_id,
                    "action": "approve",
                    "ok": action_result.ok,
                    "message": action_result.message,
                }
            )
            if self.settings.admin_notify:
                await self.notifier.notify_auto_result(
                    request_id=req_id,
                    group_id=event.group_id,
                    user_id=event.user_id,
                    ok=action_result.ok,
                    reason=decision.reason,
                )

    async def _record_action_outcome(
        self,
        req: PendingRequest,
        result: ActionResult,
        *,
        admin_user_id: str | None,
        admin_command: str,
        reject_decision: str | None,
        list_cache: AdminListCacheStore | None = None,
    ) -> str:
        now = utc_now_iso()
        payload = _action_payload(result)
        if result.ok:
            update: dict = {
                "processed_at": now,
                "status": "processed",
                "action_result": payload,
                "last_action_result": payload,
                "last_action_at": now,
                "admin_override": admin_user_id is not None,
                "admin_user_id": admin_user_id,
                "admin_command": admin_command,
            }
            if reject_decision:
                update["decision"] = reject_decision
            await self.requests.update_by_id(req.id, update)
            return "processed"

        classified = classify_action_failure(result.message, result.retcode)
        if classified.kind == "STALE":
            return await self._resolve_stale_failure(
                req,
                classified.message or (result.message or "QQ 侧申请已失效"),
                list_cache=list_cache,
                admin_user_id=admin_user_id,
                admin_command=admin_command,
            )

        await self.requests.update_by_id(
            req.id,
            {
                "status": "pending",
                "processed_at": None,
                "last_action_result": payload,
                "last_action_at": now,
                "retry_count": req.retry_count + 1,
            },
        )
        return "pending"

    async def _resolve_stale_failure(
        self,
        req: PendingRequest,
        reason: str,
        *,
        list_cache: AdminListCacheStore | None,
        admin_user_id: str | None,
        admin_command: str,
    ) -> str:
        in_group: bool | None = None
        try:
            if hasattr(self.actions, "get_group_member_info"):
                member = await self.actions.get_group_member_info(req.group_id, req.user_id)
                in_group = is_user_in_group(member)
        except Exception:
            logger.warning(
                "[audit] get_group_member_info failed request=%s",
                req.id,
                exc_info=True,
            )

        if in_group is True:
            message = "QQ 侧申请已不可操作，但用户已在群内"
            await self._apply_external_status(
                req,
                message,
                source="stale_member_check",
                list_cache=list_cache,
                admin_user_id=admin_user_id,
                admin_command=admin_command,
            )
            return "external"

        await self._apply_stale_status(req, reason, list_cache=list_cache)
        await self.audit.append(
            {
                "type": "request_stale",
                "request_id": req.id,
                "group_id": req.group_id,
                "user_id": req.user_id,
                "admin_user_id": admin_user_id,
                "admin_command": admin_command,
                "reason": reason[:200],
            }
        )
        return "stale"

    async def _apply_external_status(
        self,
        req: PendingRequest,
        message: str,
        *,
        source: str,
        list_cache: AdminListCacheStore | None = None,
        operator_id: str | None = None,
        notice_sub_type: str | None = None,
        admin_user_id: str | None = None,
        admin_command: str | None = None,
        notifier: AdminNotifier | None = None,
    ) -> None:
        now = utc_now_iso()
        await self.requests.update_by_id(
            req.id,
            {
                "processed_at": now,
                "status": "external",
                "action_result": {"ok": True, "message": message},
                "last_action_result": {"ok": True, "message": message},
                "last_action_at": now,
                "admin_override": admin_user_id is not None,
                "admin_user_id": admin_user_id,
                "admin_command": admin_command,
            },
        )
        await self.audit.append(
            {
                "type": "external_handled",
                "request_id": req.id,
                "group_id": req.group_id,
                "user_id": req.user_id,
                "operator_id": operator_id,
                "notice_sub_type": notice_sub_type,
                "source": source,
                "message": message,
            }
        )
        if list_cache is not None:
            try:
                await list_cache.remove_request_id(req.id)
            except Exception:
                logger.warning(
                    "[audit] list_cache cleanup failed for request=%s",
                    req.id,
                    exc_info=True,
                )
        notify = notifier if notifier is not None else self.notifier
        if self.settings.admin_notify and notify is not None:
            try:
                parsed = req.parsed or {}
                summary = parsed.get("name") or parsed.get("student_id")
                await notify.notify_external_handled(
                    request_id=req.id,
                    group_id=req.group_id,
                    user_id=req.user_id,
                    summary=str(summary) if summary else None,
                    comment=(req.comment or "")[:120],
                    operator_id=operator_id,
                    notice_sub_type=notice_sub_type,
                )
            except Exception:
                logger.warning(
                    "[audit] external notify failed request=%s",
                    req.id,
                    exc_info=True,
                )

    async def _apply_stale_status(
        self,
        req: PendingRequest,
        reason: str,
        *,
        list_cache: AdminListCacheStore | None = None,
        notifier: AdminNotifier | None = None,
    ) -> None:
        now = utc_now_iso()
        stale_message = reason or "QQ 侧申请已不可操作"
        await self.requests.update_by_id(
            req.id,
            {
                "processed_at": now,
                "status": "stale",
                "action_result": {"ok": False, "message": stale_message},
                "last_action_result": {"ok": False, "message": stale_message},
                "last_action_at": now,
            },
        )
        if list_cache is not None:
            try:
                await list_cache.remove_request_id(req.id)
            except Exception:
                logger.warning(
                    "[audit] list_cache cleanup failed for request=%s",
                    req.id,
                    exc_info=True,
                )
        notify = notifier if notifier is not None else self.notifier
        if self.settings.admin_notify and notify is not None:
            try:
                parsed = req.parsed or {}
                summary = parsed.get("name") or parsed.get("student_id")
                await notify.notify_stale_request(
                    request_id=req.id,
                    group_id=req.group_id,
                    user_id=req.user_id,
                    reason=stale_message,
                    summary=str(summary) if summary else None,
                    comment=(req.comment or "")[:120],
                )
            except Exception:
                logger.warning(
                    "[audit] stale notify failed request=%s",
                    req.id,
                    exc_info=True,
                )

    async def admin_approve(
        self,
        req: PendingRequest,
        admin_user_id: str,
        *,
        list_cache: AdminListCacheStore | None = None,
    ) -> ActionResult:
        result = await self.actions.set_group_add_request(
            req.flag, req.sub_type, True, "管理员人工通过"
        )
        await self._record_action_outcome(
            req,
            result,
            admin_user_id=admin_user_id,
            admin_command="approve",
            reject_decision=None,
            list_cache=list_cache,
        )
        await self.audit.append(
            {
                "type": "admin_command",
                "command": "approve",
                "admin_user_id": admin_user_id,
                "affected_request_id": req.id,
                "result": "ok" if result.ok else "failed",
            }
        )
        return result

    async def admin_reject(
        self,
        req: PendingRequest,
        admin_user_id: str,
        reason: str = "管理员人工拒绝",
        *,
        list_cache: AdminListCacheStore | None = None,
    ) -> ActionResult:
        result = await self.actions.set_group_add_request(
            req.flag, req.sub_type, False, reason
        )
        await self._record_action_outcome(
            req,
            result,
            admin_user_id=admin_user_id,
            admin_command="reject",
            reject_decision="reject",
            list_cache=list_cache,
        )
        await self.audit.append(
            {
                "type": "admin_command",
                "command": "reject",
                "admin_user_id": admin_user_id,
                "affected_request_id": req.id,
                "result": "ok" if result.ok else "failed",
            }
        )
        return result

    async def restore_stale(self, req: PendingRequest, admin_user_id: str) -> None:
        await self.requests.update_by_id(
            req.id,
            {
                "status": "pending",
                "processed_at": None,
                "action_result": None,
                "retry_count": 0,
            },
        )
        await self.audit.append(
            {
                "type": "admin_command",
                "command": "restore",
                "admin_user_id": admin_user_id,
                "affected_request_id": req.id,
                "result": "ok",
            }
        )

    async def mark_external(
        self,
        req: PendingRequest,
        admin_user_id: str,
        *,
        list_cache: AdminListCacheStore | None = None,
    ) -> None:
        message = "管理员手动标记为 QQ 侧已处理"
        await self._apply_external_status(
            req,
            message,
            source="mark_external",
            list_cache=list_cache,
            admin_user_id=admin_user_id,
            admin_command="mark_external",
        )
        await self.audit.append(
            {
                "type": "external_handled",
                "request_id": req.id,
                "group_id": req.group_id,
                "user_id": req.user_id,
                "admin_user_id": admin_user_id,
                "source": "mark_external",
                "message": message,
            }
        )

    async def process_strong_pending(self, admin_user_id: str) -> list[str]:
        from admin.release import ReleaseService

        service = ReleaseService()
        result = await service.run_batch(
            requests_store=self.requests,
            pipeline=self,
            settings=self.settings,
            admin_user_id=admin_user_id,
            count=None,
            audit_log=self.audit,
        )
        if result is None:
            return ["已有分批任务进行中，请稍后再试。"]
        if not result.lines:
            return ["没有可处理的 strong pending 请求。"]
        return [
            f"{line.request_id}: {'ok' if line.ok else line.message}" for line in result.lines
        ]

    async def reconcile_external_join(
        self,
        group_id: str,
        user_id: str,
        *,
        notice_sub_type: str | None = None,
        operator_id: str | None = None,
        list_cache: AdminListCacheStore | None = None,
        notifier: AdminNotifier | None = None,
    ) -> ReconcileResult:
        if not self.settings.target_group_ids:
            return ReconcileResult.not_handled(
                "non_target_group", "target_group_ids empty"
            )
        if group_id not in self.settings.target_group_ids:
            return ReconcileResult.not_handled(
                "non_target_group", f"group {group_id} not in target_group_ids"
            )

        pending = await self.requests.find_active_pending_by_user_group(group_id, user_id)
        if not pending:
            if notice_sub_type == "invite":
                return ReconcileResult.not_handled(
                    "invite_notice_no_pending",
                    f"invite notice without pending for group={group_id} user={user_id}",
                )
            return ReconcileResult.not_handled(
                "no_matching_pending",
                f"no pending for group={group_id} user={user_id}",
            )
        if pending.sub_type != "add":
            return ReconcileResult.not_handled(
                "pending_sub_type_not_add",
                f"pending sub_type={pending.sub_type}",
            )

        message = _external_join_message(notice_sub_type, operator_id)

        await self._apply_external_status(
            pending,
            message,
            source="group_increase",
            list_cache=list_cache,
            operator_id=operator_id,
            notice_sub_type=notice_sub_type,
            notifier=notifier,
        )

        logger.info(
            "[audit] external join reconciled request=%s group=%s user=%s "
            "notice_sub_type=%s logic=%s",
            pending.id,
            group_id,
            user_id,
            notice_sub_type,
            RECONCILE_LOGIC_VERSION,
        )
        return ReconcileResult.success(pending.id, message)
