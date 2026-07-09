from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING

from astrbot.api import logger

from config import PluginSettings, get_effective_mode
from core.decision import apply_auto_approve_flag, make_decision, should_auto_approve
from core.matcher import MatchResult, match_student
from core.parser import parse_application_comment
from data_source.njutable_provider import load_students_for_audit
from data_source.students import PendingRequest
from onebot.event_extract import GroupJoinRequest
from storage.audit_log import utc_now_iso
from storage.requests_store import RequestsStore, new_request_id

if TYPE_CHECKING:
    from admin.notify import AdminNotifier
    from data_source.student_cache import StudentCache
    from onebot.actions import ActionClient
    from storage.audit_log import AuditLog
    from storage.runtime_store import RuntimeStore


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
            if existing.status != "pending" or existing.processed_at:
                if existing.status == "ignored":
                    return
                active = await self.requests.find_active_pending_by_user_group(
                    event.group_id, event.user_id
                )
                if active and active.flag != event.flag:
                    return
                if (
                    existing.status == "processed"
                    and existing.comment == comment_text
                    and existing.action_result
                    and existing.action_result.ok
                ):
                    return
                await self._audit_and_act(
                    event, resubmit=False, reapply=True, request_id=existing.id
                )
                return
            if existing.comment == comment_text:
                return
            await self._audit_and_act(
                event, resubmit=True, reapply=False, request_id=existing.id
            )
            return

        stale = await self.requests.find_active_pending_by_user_group(
            event.group_id, event.user_id
        )
        if stale and stale.flag != event.flag:
            await self.requests.supersede_pending(stale.flag, event.flag)

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
            },
            match={
                "strength": match.strength,
                "confidence": match.confidence,
                "reason": match.reason,
                "matched_by": match.matched_by,
                "matched_student_key": match.matched_student_key,
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
            await self.requests.update_by_flag(
                event.flag,
                {
                    "processed_at": utc_now_iso(),
                    "action_result": {
                        "ok": action_result.ok,
                        "retcode": action_result.retcode,
                        "message": action_result.message,
                    },
                    "status": "processed" if action_result.ok else "failed",
                },
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

    async def admin_approve(self, req: PendingRequest, admin_user_id: str) -> ActionResult:
        result = await self.actions.set_group_add_request(
            req.flag, req.sub_type, True, "管理员人工通过"
        )
        await self.requests.update_by_id(
            req.id,
            {
                "processed_at": utc_now_iso(),
                "status": "processed" if result.ok else "failed",
                "action_result": {
                    "ok": result.ok,
                    "retcode": result.retcode,
                    "message": result.message,
                },
                "admin_override": True,
                "admin_user_id": admin_user_id,
                "admin_command": "approve",
            },
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
    ) -> ActionResult:
        result = await self.actions.set_group_add_request(
            req.flag, req.sub_type, False, reason
        )
        await self.requests.update_by_id(
            req.id,
            {
                "processed_at": utc_now_iso(),
                "status": "processed" if result.ok else "failed",
                "decision": "reject",
                "action_result": {
                    "ok": result.ok,
                    "retcode": result.retcode,
                    "message": result.message,
                },
                "admin_override": True,
                "admin_user_id": admin_user_id,
                "admin_command": "reject",
            },
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

    async def process_strong_pending(self, admin_user_id: str) -> list[str]:
        results: list[str] = []
        pending_list = await self.requests.list_pending(limit=50)
        for req in pending_list:
            if req.decision != "approve" or req.match_strength != "strong":
                continue
            if req.processed_at:
                continue
            result = await self.admin_approve(req, admin_user_id)
            results.append(f"{req.id}: {'ok' if result.ok else result.message}")
        return results
