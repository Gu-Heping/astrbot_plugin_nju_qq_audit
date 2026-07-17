from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from astrbot.api import logger

from config import PluginSettings, get_effective_mode
from admin.action_error import classify_action_failure
from admin.labels import applicant_summary
from core.decision import apply_auto_approve_flag, make_decision, should_auto_approve
from core.matcher import MatchResult, match_student
from core.parser import parse_application_comment
from core.event_fingerprint import (
    compute_event_fingerprint,
    extract_event_time_iso,
    parse_iso_datetime,
)
from core.reconcile import ReconcileResult
from core.pending_reconcile import (
    GroupSnapshotFetch,
    PendingReconcileSummary,
    build_group_snapshot_fetch,
    classify_disappearance,
    next_absence_state,
)
from onebot.group_system_msg import (
    filter_entries_for_group,
    match_pending_to_entries,
    pending_seen_in_snapshot,
    snapshot_index,
)
from onebot.member_info import is_user_in_group
from core.version import (
    RECONCILE_LOGIC_VERSION,
    is_permanent_terminal,
    is_reapply_eligible_terminal,
)
from data_source.njutable_provider import load_students_for_audit
from data_source.students import ActionResult, PendingRequest
from graduate.cache import GraduateStudentCache
from graduate.decision import apply_graduate_auto_approve_flag, make_graduate_decision
from graduate.matcher import GraduateMatchResult, match_graduate
from graduate.models import GraduateParsedApplication
from graduate.njutable_provider import load_graduates_for_audit
from graduate.parser import parse_graduate_comment
from profiles.router import (
    AuditProfile,
    configured_audit_group_ids,
    overlapping_group_ids,
    resolve_profile,
)
from onebot.event_extract import GroupJoinRequest, GroupMemberDecrease, GroupMemberIncrease
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


def _parsed_to_dict(parsed) -> dict:
    if isinstance(parsed, GraduateParsedApplication):
        return parsed.to_dict()
    return {
        "name": parsed.name,
        "student_id": parsed.student_id,
        "notice_no": parsed.notice_no,
        "major": parsed.major,
        "academy": parsed.academy,
        "notice_no_candidates": parsed.notice_no_candidates,
    }


def _match_to_dict(match) -> dict:
    if isinstance(match, GraduateMatchResult):
        student = match.matched_student
        return {
            "strength": match.strength,
            "confidence": match.confidence,
            "reason": match.reason,
            "matched_by": match.matched_by,
            "matched_student_key": match.matched_student_key,
            "matched_student_id": None,
            "candidate_count": match.candidate_count,
            "admission_type": student.admission_type if student else None,
            "major_name": student.major_name if student else None,
            "college": student.college if student else None,
            "qq_match": False,
        }
    return {
        "strength": match.strength,
        "confidence": match.confidence,
        "reason": match.reason,
        "matched_by": match.matched_by,
        "matched_student_key": match.matched_student_key,
        "matched_student_id": (
            match.matched_student.student_id if match.matched_student else None
        ),
        "qq_match": match.qq_match,
    }


_MAX_PREVIOUS_COMMENTS = 5


@dataclass
class RematchSummary:
    scanned: int = 0
    changed: int = 0
    upgraded_to_strong: int = 0
    newly_releasable: int = 0
    sync_failed: bool = False


@dataclass
class AuditEvaluation:
    profile: AuditProfile
    mode: str
    parsed: Any
    match: Any
    decision: Any


def _event_context(event: GroupJoinRequest) -> tuple[str | None, str]:
    event_time = extract_event_time_iso(event.raw_event)
    fingerprint = compute_event_fingerprint(
        group_id=event.group_id,
        user_id=event.user_id,
        flag=event.flag,
        event_time=event_time,
        comment=event.comment or "",
        sub_type=event.sub_type,
    )
    return event_time, fingerprint


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
        grad_cache: GraduateStudentCache | None = None,
    ) -> None:
        self.settings = settings
        self.requests = requests
        self.audit = audit
        self.runtime = runtime
        self.cache = cache
        self.grad_cache = grad_cache
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

    async def _audit_event_replayed(
        self,
        event: GroupJoinRequest,
        *,
        fingerprint: str,
        reason: str,
        request_id: str | None = None,
        fallback: str | None = None,
    ) -> None:
        logger.info(
            "[audit] duplicate event replayed flag=%s fingerprint=%s reason=%s",
            event.flag[:8] if event.flag else "",
            fingerprint[:12],
            reason,
        )
        record: dict = {
            "type": "duplicate_event_replayed",
            "group_id": event.group_id,
            "user_id": event.user_id,
            "reason": reason,
            "fingerprint_prefix": fingerprint[:12],
        }
        if request_id:
            record["request_id"] = request_id
        if fallback:
            record["fallback"] = fallback
        await self.audit.append(record)

    def _seconds_since_processed(self, existing: PendingRequest) -> float:
        pt = parse_iso_datetime(existing.processed_at)
        now = parse_iso_datetime(utc_now_iso())
        if not pt or not now:
            return float("inf")
        return (now - pt).total_seconds()

    async def _resolve_reapply_storage_fingerprint(
        self, base_fingerprint: str, existing: PendingRequest
    ) -> str:
        next_attempt = int(existing.attempt_no or 1) + 1
        if not await self.requests.has_fingerprint(base_fingerprint):
            return base_fingerprint
        return f"{base_fingerprint}#a{next_attempt}"

    async def _handle_reapply_after_terminal(
        self,
        event: GroupJoinRequest,
        existing: PendingRequest,
        *,
        event_time: str | None,
        fingerprint: str,
    ) -> None:
        membership = await self.requests.get_membership_state(
            event.group_id, event.user_id
        )
        if membership.get("reapply_eligible"):
            storage_fp = fingerprint
            if await self.requests.has_fingerprint(storage_fp):
                linked_id = await self.requests.get_fingerprint_request_id(storage_fp)
                linked = await self.requests.get_by_id(linked_id) if linked_id else None
                if linked and linked.id != existing.id and linked.reapply_of == existing.id:
                    await self._audit_event_replayed(
                        event,
                        fingerprint=storage_fp,
                        reason="seen_fingerprint",
                        request_id=linked.id,
                    )
                    return

            await self._audit_and_act_reapply(
                event,
                existing,
                event_time=event_time,
                fingerprint=storage_fp,
                fallback="strong_signal_group_decrease",
            )
            await self.requests.update_membership_state(
                event.group_id,
                event.user_id,
                {"reapply_eligible": False},
            )
            return

        storage_fp = await self._resolve_reapply_storage_fingerprint(
            fingerprint, existing
        )
        if await self.requests.has_fingerprint(storage_fp):
            await self._audit_event_replayed(
                event,
                fingerprint=storage_fp,
                reason="seen_fingerprint",
                request_id=existing.id,
            )
            return

        elapsed = self._seconds_since_processed(existing)
        fallback = "no_event_time" if not event_time else None
        # Burst window only suppresses platform double-fire / replay of the *same*
        # answer. A corrected comment after reject must proceed immediately —
        # QQ will not redeliver the request once we drop it.
        comment_changed = (event.comment or "") != (existing.comment or "")

        if elapsed < self.settings.reapply_debounce_seconds and not comment_changed:
            if event_time and existing.processed_at:
                et = parse_iso_datetime(event_time)
                pt = parse_iso_datetime(existing.processed_at)
                if et and pt and et <= pt:
                    logger.info(
                        "[audit] reapply burst blocked request=%s elapsed=%.1fs reason=recycled_event_time",
                        existing.id,
                        elapsed,
                    )
                    await self._audit_event_replayed(
                        event,
                        fingerprint=storage_fp,
                        reason="reapply_burst_recycled_event_time",
                        request_id=existing.id,
                        fallback="recycled_event_time",
                    )
                    return
            logger.info(
                "[audit] reapply burst blocked request=%s elapsed=%.1fs storage_fp=%s",
                existing.id,
                elapsed,
                storage_fp[:12],
            )
            await self._audit_event_replayed(
                event,
                fingerprint=storage_fp,
                reason="reapply_burst_after_terminal",
                request_id=existing.id,
                fallback=fallback,
            )
            return

        reapply_fallback = fallback
        if event_time and existing.processed_at:
            et = parse_iso_datetime(event_time)
            pt = parse_iso_datetime(existing.processed_at)
            if et and pt and et <= pt:
                reapply_fallback = "recycled_event_time"
        if comment_changed and elapsed < self.settings.reapply_debounce_seconds:
            reapply_fallback = reapply_fallback or "comment_changed_bypass_burst"

        await self._audit_and_act_reapply(
            event,
            existing,
            event_time=event_time,
            fingerprint=storage_fp,
            fallback=reapply_fallback,
        )

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
        has_undergrad = bool(self.settings.target_group_ids)
        has_grad = self.settings.grad_enabled and bool(self.settings.grad_target_group_ids)
        if not has_undergrad and not has_grad:
            logger.debug("[audit] no target groups configured, skip request")
            return

        profile = resolve_profile(event.group_id, self.settings)
        if profile is None:
            overlap = overlapping_group_ids(self.settings)
            if event.group_id in overlap:
                logger.warning(
                    "[audit] overlap group ignored: %s", event.group_id
                )
                await self.audit.append(
                    {
                        "type": "request_received",
                        "group_id": event.group_id,
                        "user_id": event.user_id,
                        "decision": "ignored",
                        "reason": "本科/研究生目标群重叠，拒绝处理",
                    }
                )
                return
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

        event_time, fingerprint = _event_context(event)

        existing = await self.requests.get_by_flag(event.flag)
        if existing and is_reapply_eligible_terminal(existing):
            await self._handle_reapply_after_terminal(
                event,
                existing,
                event_time=event_time,
                fingerprint=fingerprint,
            )
            return

        if await self.requests.has_fingerprint(fingerprint):
            linked_id = await self.requests.get_fingerprint_request_id(fingerprint)
            linked = await self.requests.get_by_id(linked_id) if linked_id else None
            await self._audit_event_replayed(
                event,
                fingerprint=fingerprint,
                reason="seen_fingerprint",
                request_id=linked.id if linked else None,
            )
            return

        comment_text = event.comment or ""
        if existing:
            if is_permanent_terminal(existing):
                await self._ignore_duplicate_terminal(
                    existing,
                    event,
                    reason=f"same flag permanent terminal status={existing.status} decision={existing.decision}",
                )
                return

            if existing.status == "pending" and not existing.processed_at:
                if existing.comment == comment_text:
                    await self.requests.register_fingerprint(fingerprint, existing.id)
                    return
                await self._audit_and_update_pending(event, existing)
                await self.requests.register_fingerprint(fingerprint, existing.id)
                return

            if existing.status == "failed":
                retryable = await self.requests.ensure_retryable(existing.id)
                if retryable is None:
                    return
                await self._audit_and_act(
                    event,
                    resubmit=True,
                    request_id=existing.id,
                    event_time=event_time,
                    fingerprint=fingerprint,
                    profile=profile,
                )
                return

            await self._ignore_duplicate_terminal(
                existing, event, reason="same flag not actionable"
            )
            return

        active_pending = await self.requests.find_active_pending_by_user_group(
            event.group_id, event.user_id
        )
        if active_pending and active_pending.flag != event.flag:
            await self.requests.supersede_pending(active_pending.flag, event.flag)

        await self._audit_and_act(
            event,
            event_time=event_time,
            fingerprint=fingerprint,
            profile=profile,
        )

    def _evaluate_undergraduate_request(self, event: GroupJoinRequest):
        mode, _ = self._effective_mode()
        students = load_students_for_audit(self.settings, self.cache)
        parsed = parse_application_comment(event.comment or "")
        match = match_student(parsed, students, applicant_user_id=event.user_id)
        decision = make_decision(parsed, match, is_target_group=True)
        decision = apply_auto_approve_flag(decision, mode, match)
        return AuditEvaluation(
            profile="undergraduate",
            mode=mode,
            parsed=parsed,
            match=match,
            decision=decision,
        )

    def _evaluate_graduate_request(self, event: GroupJoinRequest):
        mode, _ = self._effective_mode()
        cache = self.grad_cache
        students = load_graduates_for_audit(self.settings, cache) if cache else []
        parsed = parse_graduate_comment(event.comment or "")
        match = match_graduate(parsed, students)
        decision = make_graduate_decision(parsed, match, is_target_group=True)
        decision = apply_graduate_auto_approve_flag(decision, mode, match)
        return AuditEvaluation(
            profile="graduate",
            mode=mode,
            parsed=parsed,
            match=match,
            decision=decision,
        )

    def _evaluate_request(
        self, event: GroupJoinRequest, *, profile: AuditProfile | None = None
    ):
        resolved = profile or resolve_profile(event.group_id, self.settings) or "undergraduate"
        if resolved == "graduate":
            return self._evaluate_graduate_request(event)
        return self._evaluate_undergraduate_request(event)

    def _evaluate_pending_fields(self, req: PendingRequest):
        """Re-parse and rematch a stored pending against the current student cache."""
        profile = getattr(req, "profile", None) or "undergraduate"
        # Synthetic event for shared evaluators
        event = GroupJoinRequest(
            group_id=req.group_id,
            user_id=req.user_id,
            comment=req.comment or "",
            flag=req.flag,
            sub_type=req.sub_type or "add",
        )
        if profile == "graduate":
            ev = self._evaluate_graduate_request(event)
        else:
            ev = self._evaluate_undergraduate_request(event)
        return ev.mode, ev.parsed, ev.match, ev.decision

    async def rematch_active_pending(
        self,
        *,
        source: str = "manual",
        profiles: frozenset[str] | None = None,
    ) -> RematchSummary:
        """Re-evaluate active pending against the current student cache.

        Does not call QQ APIs or send admin notifications.
        When ``profiles`` is set, only those request profiles are rematched
        (e.g. undergraduate-only for release/sweep).
        """
        summary = RematchSummary()
        pending = await self.requests.list_pending(limit=1000)
        summary.scanned = len(pending)
        now = utc_now_iso()

        for req in pending:
            if req.status != "pending" or req.processed_at:
                continue
            req_profile = getattr(req, "profile", None) or "undergraduate"
            if profiles is not None and req_profile not in profiles:
                continue
            old_strength = req.match_strength or (req.match or {}).get("strength") or "none"
            old_decision = req.decision
            old_reason = req.reason or ""
            old_parsed = req.parsed or {}
            old_match = req.match or {}

            mode, parsed, match, decision = self._evaluate_pending_fields(req)
            new_parsed = _parsed_to_dict(parsed)
            new_match = _match_to_dict(match)

            changed = (
                decision.decision != old_decision
                or match.strength != old_strength
                or decision.reason != old_reason
                or new_parsed != old_parsed
                or new_match.get("strength") != old_match.get("strength")
                or new_match.get("matched_student_key") != old_match.get("matched_student_key")
                or new_match.get("matched_student_id") != old_match.get("matched_student_id")
                or new_match.get("matched_by") != old_match.get("matched_by")
            )
            if not changed:
                continue

            summary.changed += 1
            if old_strength != "strong" and match.strength == "strong":
                summary.upgraded_to_strong += 1

            await self.requests.update_by_id(
                req.id,
                {
                    "parsed": new_parsed,
                    "match": new_match,
                    "decision": decision.decision,
                    "confidence": decision.confidence,
                    "reason": decision.reason,
                    "mode": mode,
                    "match_strength": match.strength,
                    "matched_student_key": decision.matched_student_key,
                    "updated_at": now,
                },
            )
            await self.audit.append(
                {
                    "type": "pending_rematched",
                    "source": source,
                    "request_id": req.id,
                    "group_id": req.group_id,
                    "user_id": req.user_id,
                    "old_decision": old_decision,
                    "new_decision": decision.decision,
                    "old_match_strength": old_strength,
                    "new_match_strength": match.strength,
                    "reason": decision.reason,
                }
            )
            logger.info(
                "[audit] pending rematched request=%s source=%s %s/%s -> %s/%s",
                req.id,
                source,
                old_decision,
                old_strength,
                decision.decision,
                match.strength,
            )

        return summary

    async def _audit_and_update_pending(
        self, event: GroupJoinRequest, existing: PendingRequest
    ) -> None:
        old_comment = existing.comment or ""
        new_comment = event.comment or ""
        profile = (
            getattr(existing, "profile", None)
            or resolve_profile(event.group_id, self.settings)
            or "undergraduate"
        )
        evaluation = self._evaluate_request(event, profile=profile)
        mode, parsed, match, decision = (
            evaluation.mode,
            evaluation.parsed,
            evaluation.match,
            evaluation.decision,
        )
        now = utc_now_iso()

        previous = list(existing.previous_comments or [])
        if old_comment and old_comment != new_comment:
            previous.append(old_comment[:200])
            previous = previous[-_MAX_PREVIOUS_COMMENTS:]

        update = {
            "comment": new_comment,
            "sub_type": event.sub_type,
            "parsed": _parsed_to_dict(parsed),
            "match": _match_to_dict(match),
            "decision": decision.decision,
            "confidence": decision.confidence,
            "reason": decision.reason,
            "mode": mode,
            "status": "pending",
            "processed_at": None,
            "match_strength": match.strength,
            "matched_student_key": decision.matched_student_key,
            "updated_at": now,
            "comment_revision": int(existing.comment_revision or 0) + 1,
            "previous_comments": previous,
            "profile": profile,
        }
        pending = await self.requests.update_by_id(existing.id, update)
        if pending is None:
            logger.warning("[audit] pending comment update failed request=%s", existing.id)
            return

        await self.audit.append(
            {
                "type": "duplicate_pending_comment_updated",
                "request_id": existing.id,
                "group_id": event.group_id,
                "user_id": event.user_id,
                "old_comment": old_comment[:200],
                "new_comment": new_comment[:200],
                "comment_revision": pending.comment_revision,
                "decision": decision.decision,
                "reason": decision.reason,
                "match_strength": match.strength,
                "profile": profile,
            }
        )
        logger.info(
            "[audit] pending comment updated request=%s revision=%s decision=%s",
            existing.id,
            pending.comment_revision,
            decision.decision,
        )
        await self._finish_after_decision(
            pending,
            event,
            decision,
            match,
            mode,
            notify_update=True,
        )

    async def _audit_and_act_reapply(
        self,
        event: GroupJoinRequest,
        existing: PendingRequest,
        *,
        event_time: str | None,
        fingerprint: str,
        fallback: str | None = None,
    ) -> None:
        req_id = await self._audit_and_act(
            event,
            reapply_of=existing.id,
            attempt_no=int(existing.attempt_no or 1) + 1,
            event_time=event_time,
            fingerprint=fingerprint,
        )
        record: dict = {
            "type": "reapplication_created",
            "request_id": req_id,
            "reapply_of": existing.id,
            "group_id": event.group_id,
            "user_id": event.user_id,
            "attempt_no": int(existing.attempt_no or 1) + 1,
            "received_event_time": event_time,
            "fingerprint_prefix": fingerprint[:12],
        }
        if fallback:
            record["fallback"] = fallback
        await self.audit.append(record)
        logger.info(
            "[audit] reapplication created request=%s reapply_of=%s attempt=%s",
            req_id,
            existing.id,
            int(existing.attempt_no or 1) + 1,
        )

    async def _audit_and_act(
        self,
        event: GroupJoinRequest,
        *,
        resubmit: bool = False,
        request_id: str | None = None,
        reapply_of: str | None = None,
        attempt_no: int = 1,
        event_time: str | None = None,
        fingerprint: str | None = None,
        profile: AuditProfile | None = None,
    ) -> str:
        resolved_profile = (
            profile
            or resolve_profile(event.group_id, self.settings)
            or "undergraduate"
        )
        evaluation = self._evaluate_request(event, profile=resolved_profile)
        mode, parsed, match, decision = (
            evaluation.mode,
            evaluation.parsed,
            evaluation.match,
            evaluation.decision,
        )

        req_id = request_id or new_request_id()
        pending = PendingRequest(
            id=req_id,
            group_id=event.group_id,
            user_id=event.user_id,
            comment=event.comment or "",
            flag=event.flag,
            sub_type=event.sub_type,
            parsed=_parsed_to_dict(parsed),
            match=_match_to_dict(match),
            decision=decision.decision,
            confidence=decision.confidence,
            reason=decision.reason,
            mode=mode,
            status="pending",
            created_at=utc_now_iso(),
            match_strength=match.strength,
            matched_student_key=decision.matched_student_key,
            reapply_of=reapply_of,
            attempt_no=attempt_no,
            received_event_time=event_time,
            event_fingerprint=fingerprint,
            profile=resolved_profile,
        )
        if resubmit:
            update_dict = RequestsStore._request_to_dict(pending)
            update_dict["action_result"] = None
            update_dict["last_action_result"] = None
            update_dict["last_action_at"] = None
            update_dict["retry_count"] = 0
            update_dict["processed_at"] = None
            update_dict["status"] = "pending"
            update_dict["profile"] = resolved_profile
            if fingerprint:
                update_dict["event_fingerprint"] = fingerprint
            if event_time:
                update_dict["received_event_time"] = event_time
            await self.requests.update_by_id(req_id, update_dict)
            if fingerprint:
                await self.requests.register_fingerprint(fingerprint, req_id)
        else:
            await self.requests.insert_attempt(pending)

        audit_type = "decision_made"
        if resubmit:
            audit_type = "request_received"
        elif reapply_of:
            audit_type = "decision_made"

        await self.audit.append(
            {
                "type": audit_type,
                "request_id": req_id,
                "group_id": event.group_id,
                "user_id": event.user_id,
                "comment": event.comment,
                "decision": decision.decision,
                "confidence": decision.confidence,
                "reason": decision.reason,
                "mode": mode,
                "match_strength": match.strength,
                "reapply_of": reapply_of,
                "attempt_no": attempt_no,
                "profile": resolved_profile,
            }
        )

        logger.info(
            "[audit] request=%s profile=%s decision=%s mode=%s reason=%s",
            req_id,
            resolved_profile,
            decision.decision,
            mode,
            decision.reason,
        )

        await self._finish_after_decision(
            pending, event, decision, match, mode, notify_update=False
        )
        return req_id

    async def _finish_after_decision(
        self,
        pending: PendingRequest,
        event: GroupJoinRequest,
        decision,
        match: MatchResult,
        mode: str,
        *,
        notify_update: bool,
    ) -> None:
        req_id = pending.id
        if notify_update and self.settings.admin_notify:
            try:
                await self.notifier.notify_pending_comment_updated(
                    request_id=req_id,
                    group_id=event.group_id,
                    user_id=event.user_id,
                    comment=event.comment or "",
                    reason=decision.reason,
                )
            except Exception:
                logger.exception(
                    "[audit] pending update notify failed request=%s",
                    req_id,
                )

        if mode in {"manual", "record-only"} or decision.decision == "manual_review":
            if (
                not notify_update
                and self.settings.admin_notify
                and decision.decision == "manual_review"
            ):
                try:
                    notify_parsed = dict(pending.parsed or {})
                    notify_parsed["_profile"] = getattr(pending, "profile", None) or "undergraduate"
                    match_dict = pending.match or {}
                    if match_dict.get("college") and not notify_parsed.get("college"):
                        notify_parsed["college"] = match_dict.get("college")
                    await self.notifier.notify_manual_review(
                        request_id=req_id,
                        group_id=event.group_id,
                        user_id=event.user_id,
                        comment=event.comment,
                        parsed=notify_parsed,
                        reason=decision.reason,
                    )
                except Exception:
                    logger.exception(
                        "[audit] manual_review notify failed request=%s",
                        req_id,
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
                    summary=applicant_summary(pending),
                    comment=pending.comment or event.comment or "",
                    match_strength=(
                        getattr(match, "strength", None)
                        or pending.match_strength
                        or (pending.match or {}).get("strength")
                    ),
                    action_message=action_result.message,
                    parsed=pending.parsed or {},
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
            elif admin_command == "approve":
                update["decision"] = "approve"
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
        notify: bool = True,
    ) -> None:
        now = utc_now_iso()
        update = {
                "processed_at": now,
                "status": "external",
                "action_result": {"ok": True, "message": message},
                "last_action_result": {"ok": True, "message": message},
                "last_action_at": now,
                "admin_override": admin_user_id is not None,
                "admin_user_id": admin_user_id,
                "admin_command": admin_command,
            }
        if source == "audit_list":
            update["reconcile_outcome"] = "external_approved"
            update["reconcile_source"] = source
        await self.requests.update_by_id(req.id, update)
        audit_type = "external_approved" if source == "audit_list" else "external_handled"
        await self.audit.append(
            {
                "type": audit_type,
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
        notify_client = notifier if notifier is not None else self.notifier
        if notify and self.settings.admin_notify and notify_client is not None:
            try:
                parsed = req.parsed or {}
                summary = parsed.get("name") or parsed.get("student_id")
                await notify_client.notify_external_handled(
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

    async def dismiss_pending(
        self,
        req: PendingRequest,
        admin_user_id: str,
        reason: str,
        *,
        list_cache: AdminListCacheStore | None = None,
    ) -> dict[str, Any]:
        """Locally close an invalid pending request without calling QQ APIs.

        Returns a result dict:
          - ok / idempotent / already_terminal
          - request (latest)
        """
        reason = (reason or "").strip()
        if not reason:
            return {"ok": False, "error": "empty_reason", "request": req}

        latest = await self.requests.get_by_id(req.id) or req
        if latest.status == "dismissed":
            return {"ok": True, "idempotent": True, "request": latest}
        if latest.status != "pending" or latest.processed_at:
            return {
                "ok": False,
                "already_terminal": True,
                "request": latest,
            }

        now = utc_now_iso()
        message = f"本地关闭：{reason}"
        updated = await self.requests.update_by_id(
            latest.id,
            {
                "status": "dismissed",
                "processed_at": now,
                "dismissed_at": now,
                "dismissed_by": admin_user_id,
                "dismiss_reason": reason,
                "admin_user_id": admin_user_id,
                "admin_command": "dismiss",
                "admin_override": True,
                "action_result": {"ok": True, "message": message},
                "last_action_result": {"ok": True, "message": message},
                "last_action_at": now,
            },
        )
        await self.runtime.set_pending_absence_state(latest.id, None)
        if list_cache is not None:
            try:
                await list_cache.remove_request_id(latest.id)
            except Exception:
                logger.warning(
                    "[audit] list_cache cleanup failed for dismiss request=%s",
                    latest.id,
                    exc_info=True,
                )
        await self.audit.append(
            {
                "type": "admin_command",
                "command": "dismiss",
                "admin_user_id": admin_user_id,
                "affected_request_id": latest.id,
                "result": "ok",
                "reason": reason,
            }
        )
        return {"ok": True, "idempotent": False, "request": updated or latest}

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
        self_id: str | None = None,
        list_cache: AdminListCacheStore | None = None,
        notifier: AdminNotifier | None = None,
    ) -> ReconcileResult:
        active_groups = configured_audit_group_ids(self.settings)
        if not active_groups:
            return ReconcileResult.not_handled(
                "non_target_group", "no configured audit groups"
            )
        if group_id not in active_groups:
            return ReconcileResult.not_handled(
                "non_target_group", f"group {group_id} not in audit groups"
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

        if self_id and operator_id and str(operator_id) == str(self_id):
            latest = await self.requests.get_by_id(pending.id) or pending
            # Bot 自己审批后的 group_increase 回传：不标 external、不发通知。
            if list_cache is not None:
                try:
                    await list_cache.remove_request_id(pending.id)
                except Exception:
                    logger.warning(
                        "[audit] list_cache cleanup failed for request=%s",
                        pending.id,
                        exc_info=True,
                    )
            await self.audit.append(
                {
                    "type": "own_approve_join_notice_suppressed",
                    "request_id": pending.id,
                    "group_id": group_id,
                    "user_id": user_id,
                    "operator_id": operator_id,
                    "notice_sub_type": notice_sub_type,
                    "status": latest.status,
                }
            )
            return ReconcileResult.success(
                pending.id,
                "bot 自己审批产生的入群通知，已抑制 external 重复通知",
            )

        latest = await self.requests.get_by_id(pending.id) or pending
        if (
            latest.status == "processed"
            and latest.action_result
            and latest.action_result.ok
        ):
            if list_cache is not None:
                try:
                    await list_cache.remove_request_id(latest.id)
                except Exception:
                    logger.warning(
                        "[audit] list_cache cleanup failed for request=%s",
                        latest.id,
                        exc_info=True,
                    )
            await self.audit.append(
                {
                    "type": "join_notice_after_processed_approve_suppressed",
                    "request_id": latest.id,
                    "group_id": group_id,
                    "user_id": user_id,
                    "operator_id": operator_id,
                    "notice_sub_type": notice_sub_type,
                    "status": latest.status,
                    "decision": latest.decision,
                }
            )
            return ReconcileResult.success(
                latest.id,
                "申请已由 bot/管理员命令处理成功，抑制 external 重复通知",
            )

        if latest.status != "pending":
            return ReconcileResult.not_handled(
                "pending_no_longer_active",
                f"request status={latest.status}",
            )

        message = _external_join_message(notice_sub_type, operator_id)

        await self._apply_external_status(
            latest,
            message,
            source="group_increase",
            list_cache=list_cache,
            operator_id=operator_id,
            notice_sub_type=notice_sub_type,
            notifier=notifier,
            notify=True,
        )

        logger.info(
            "[audit] external join reconciled request=%s group=%s user=%s "
            "notice_sub_type=%s logic=%s",
            latest.id,
            group_id,
            user_id,
            notice_sub_type,
            RECONCILE_LOGIC_VERSION,
        )
        return ReconcileResult.success(latest.id, message)

    async def handle_group_increase(self, increase: GroupMemberIncrease) -> None:
        if increase.group_id not in configured_audit_group_ids(self.settings):
            return

        await self.requests.update_membership_state(
            increase.group_id,
            increase.user_id,
            {
                "membership": "joined",
                "reapply_eligible": False,
            },
        )
        await self.audit.append(
            {
                "type": "member_joined",
                "group_id": increase.group_id,
                "user_id": increase.user_id,
                "notice_sub_type": increase.sub_type,
                "operator_id": increase.operator_id,
            }
        )

    async def handle_group_decrease(self, decrease: GroupMemberDecrease) -> None:
        if decrease.group_id not in configured_audit_group_ids(self.settings):
            return

        if decrease.sub_type == "kick_me" or (
            decrease.self_id and decrease.user_id == decrease.self_id
        ):
            await self.audit.append(
                {
                    "type": "bot_kicked_from_group",
                    "group_id": decrease.group_id,
                    "user_id": decrease.user_id,
                    "notice_sub_type": decrease.sub_type,
                    "operator_id": decrease.operator_id,
                }
            )
            return

        sub_type = decrease.sub_type or "leave"
        if sub_type not in {"leave", "kick"}:
            sub_type = "leave"

        membership_status = "left" if sub_type == "leave" else "kicked"
        await self.requests.update_membership_state(
            decrease.group_id,
            decrease.user_id,
            {
                "membership": membership_status,
                "reapply_eligible": True,
                "left_sub_type": sub_type,
                "left_at": utc_now_iso(),
            },
        )
        audit_type = "member_left" if sub_type == "leave" else "member_kicked"
        await self.audit.append(
            {
                "type": audit_type,
                "group_id": decrease.group_id,
                "user_id": decrease.user_id,
                "notice_sub_type": sub_type,
                "operator_id": decrease.operator_id,
            }
        )

    async def reconcile_active_pending(
        self,
        *,
        source: str,
        list_cache: AdminListCacheStore | None = None,
        profiles: frozenset[str] | None = None,
    ) -> PendingReconcileSummary:
        summary = PendingReconcileSummary()
        try:
            return await asyncio.wait_for(
                self._reconcile_active_pending_inner(
                    source=source,
                    list_cache=list_cache,
                    summary=summary,
                    profiles=profiles,
                ),
                timeout=self.settings.audit_list_reconcile_timeout_ms / 1000,
            )
        except asyncio.TimeoutError:
            summary.failed = True
            summary.failure_message = "timeout"
            await self.audit.append(
                {
                    "type": "reconcile_failed",
                    "source": source,
                    "reason": "timeout",
                }
            )
            return summary
        except Exception as exc:
            logger.exception("[audit] reconcile_active_pending failed source=%s", source)
            summary.failed = True
            summary.failure_message = str(exc)
            await self.audit.append(
                {
                    "type": "reconcile_failed",
                    "source": source,
                    "reason": "exception",
                }
            )
            return summary

    async def _reconcile_active_pending_inner(
        self,
        *,
        source: str,
        list_cache: AdminListCacheStore | None,
        summary: PendingReconcileSummary,
        profiles: frozenset[str] | None = None,
    ) -> PendingReconcileSummary:
        active_groups = configured_audit_group_ids(self.settings)
        pendings = []
        for req in await self.requests.list_pending(limit=1000):
            if req.group_id not in active_groups:
                continue
            req_profile = getattr(req, "profile", None) or "undergraduate"
            if profiles is not None and req_profile not in profiles:
                continue
            pendings.append(req)
        if not pendings:
            return summary

        by_group: dict[str, list[PendingRequest]] = {}
        for pending in pendings:
            by_group.setdefault(pending.group_id, []).append(pending)

        fetches: dict[str, GroupSnapshotFetch] = {}
        for group_id in by_group:
            result = await self.actions.get_group_system_msg(group_id)
            fetch = build_group_snapshot_fetch(result)
            if not fetch.ok or not fetch.reliable:
                # Isolate per-group failure so one profile/group cannot block others.
                summary.failed = True
                if not summary.failure_message:
                    summary.failure_message = fetch.message or "group system msg failed"
                await self.audit.append(
                    {
                        "type": "reconcile_failed",
                        "source": source,
                        "reason": "group_system_msg_unavailable",
                        "group_id": group_id,
                        "message": fetch.message or "group system msg failed",
                    }
                )
                continue
            fetches[group_id] = fetch

        if not fetches:
            return summary

        planned: list[tuple[str, PendingRequest]] = []
        now_iso = utc_now_iso()
        for group_id, items in by_group.items():
            fetch = fetches.get(group_id)
            if fetch is None:
                summary.unchanged += len(items)
                continue
            if fetch.empty_untrusted:
                summary.snowluma_empty_ambiguity = True
            if fetch.snapshot_saturated:
                summary.snapshot_saturated = True
                await self.audit.append(
                    {
                        "type": "reconcile_snapshot_saturated",
                        "source": source,
                        "group_id": group_id,
                        "request_count": fetch.request_count,
                        "snapshot_complete": False,
                        "reason": "snowluma_fetch_limit_20",
                    }
                )
            current_entries = filter_entries_for_group(fetch.entries, group_id)
            previous_index = self.runtime.get_qq_snapshot_index(group_id)
            meta = self.runtime.get_qq_snapshot_meta(group_id) or {}
            history = list(meta.get("history") or [])

            for pending in items:
                fresh = await self.requests.get_by_id(pending.id)
                if fresh is None or fresh.status != "pending":
                    continue

                match = match_pending_to_entries(
                    flag=pending.flag,
                    group_id=pending.group_id,
                    user_id=pending.user_id,
                    comment=pending.comment or "",
                    entries=current_entries,
                )
                if match.kind == "ambiguous":
                    summary.skipped_ambiguous += 1
                    summary.unchanged += 1
                    await self.runtime.set_pending_absence_state(pending.id, None)
                    continue
                if match.kind == "unique":
                    summary.unchanged += 1
                    await self.runtime.set_pending_absence_state(pending.id, None)
                    continue

                seen_before = pending_seen_in_snapshot(
                    flag=pending.flag,
                    group_id=pending.group_id,
                    user_id=pending.user_id,
                    snapshot=previous_index,
                )
                if not seen_before:
                    for hist in history:
                        if pending_seen_in_snapshot(
                            flag=pending.flag,
                            group_id=pending.group_id,
                            user_id=pending.user_id,
                            snapshot=hist.get("index") if isinstance(hist, dict) else None,
                        ):
                            seen_before = True
                            break

                absence_prev = self.runtime.get_pending_absence_state(pending.id)
                absence_next = next_absence_state(
                    currently_present=False,
                    previous=absence_prev,
                    seen_in_history=seen_before,
                    now_iso=now_iso,
                    snapshot_saturated=fetch.snapshot_saturated,
                )
                await self.runtime.set_pending_absence_state(pending.id, absence_next)

                member_present = None
                if seen_before or (absence_next and absence_next.get("seen_before_absent")):
                    member_result = await self.actions.get_group_member_info(
                        pending.group_id, pending.user_id
                    )
                    member_present = is_user_in_group(member_result)

                action = classify_disappearance(
                    pending=pending,
                    current_entries=current_entries,
                    previous_index=previous_index,
                    member_present=member_present,
                    absence_state=absence_next,
                    reject_confirm_snapshots=self.settings.audit_list_reject_confirm_snapshots,
                    reject_wait_seconds=self.settings.audit_list_reject_wait_seconds,
                    snapshot_saturated=fetch.snapshot_saturated,
                )
                if action == "ambiguous":
                    summary.skipped_ambiguous += 1
                    summary.unchanged += 1
                elif action == "unchanged":
                    summary.unchanged += 1
                elif action == "absence_not_trusted":
                    summary.absence_not_trusted += 1
                    summary.unchanged += 1
                    await self.audit.append(
                        {
                            "type": "reconcile_absence_not_trusted",
                            "source": source,
                            "request_id": pending.id,
                            "group_id": pending.group_id,
                            "user_id": pending.user_id,
                            "reason": "snapshot_saturated",
                            "request_count": fetch.request_count,
                        }
                    )
                elif action == "external_approved":
                    planned.append(("external_approved", pending))
                elif action == "external_rejected_inferred":
                    planned.append(("external_rejected_inferred", pending))
                else:
                    summary.external_handled_unknown += 1
                    summary.unchanged += 1
                    await self.audit.append(
                        {
                            "type": "external_handled_unknown",
                            "source": source,
                            "request_id": pending.id,
                            "group_id": pending.group_id,
                            "user_id": pending.user_id,
                            "reason": "awaiting_multi_snapshot_confirm_or_member_unknown",
                        }
                    )

        for action, pending in planned:
            latest = await self.requests.get_by_id(pending.id)
            if latest is None or latest.status != "pending":
                continue
            if action == "external_approved":
                await self._apply_external_status(
                    latest,
                    "QQ 侧已入群（audit list 自动对账）",
                    source=source,
                    list_cache=list_cache,
                    notify=False,
                )
                summary.external_approved += 1
                await self.runtime.set_pending_absence_state(pending.id, None)
            elif action == "external_rejected_inferred":
                await self._apply_external_rejected_inferred(
                    latest,
                    source=source,
                    list_cache=list_cache,
                )
                summary.external_rejected_inferred += 1
                await self.runtime.set_pending_absence_state(pending.id, None)

        for group_id, fetch in fetches.items():
            group_entries = filter_entries_for_group(fetch.entries, group_id)
            await self.runtime.save_qq_snapshot_index(
                group_id, snapshot_index(group_entries)
            )

        return summary

    async def _apply_external_rejected_inferred(
        self,
        req: PendingRequest,
        *,
        source: str,
        list_cache: AdminListCacheStore | None = None,
    ) -> None:
        now = utc_now_iso()
        message = "QQ 侧已拒绝（推断；多次成功空快照 + 成员不存在）"
        await self.requests.update_by_id(
            req.id,
            {
                "processed_at": now,
                "status": "processed",
                "decision": "reject",
                "action_result": {"ok": True, "message": message},
                "last_action_result": {"ok": True, "message": message},
                "last_action_at": now,
                "reconcile_outcome": "external_rejected_inferred",
                "reconcile_source": source,
            },
        )
        await self.audit.append(
            {
                "type": "external_rejected_inferred",
                "source": source,
                "request_id": req.id,
                "group_id": req.group_id,
                "user_id": req.user_id,
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
