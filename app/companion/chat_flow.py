"""대화 칸의 흐름 (설계 B4.1, B4.2, B3의 2.1 일들). 두뇌는 기본 도우미(RuleBrain, AI 아님) 하나.

적은 말 → 두뇌 → (시간을 풀려면 타임라인이 필요: 창이 먼저 ping + timeline_info) → 두뇌 다시 →
- 답(도움말, 상태, 못 하는 부탁)·되묻기·못 알아들음: 글 한 줄 + 예문 칩 (누르면 입력 칸만 채운다).
- 할 일 목록: 하나씩 차례로.
  · 표시 / 권하는 표시(자르기 → 보라, 소리 → 노랑): 확인 카드 (리졸브에 묻지 않고 만든다).
  · 쉬는 곳·튀는 소리: 자동화 버튼과 같은 계산 (말한 범위 안쪽만) → 확인 카드.
  · 도우미 표시 지우기: 지울 표시를 읽어서(읽기만) 확인 카드.
  · 재생 위치 옮기기: 바로 (편집이 아니라서 카드가 없다).
  · 방금 거 취소: 무엇이 빠지는지 보여 주는 확인 카드 → [빼기].
  · 자동화 버튼에 저장: 확인 카드 → [저장] (구간은 저장하지 않는다).
  · 모두 빼기: 자동화 쪽과 같이 먼저 묻는다.
- 리졸브를 바꾸는 요청은 카드의 단추를 누른 뒤에만 나간다. 두뇌는 리졸브에 아무것도 묻지 않는다.
- 값마다 출처, 범위 지킴이(막음·경고), 못 알아들은 부분은 카드에 적는다. 숫자는 카드의 [−][+]로 고친다.
- 일하는 중에 보낸 말은 끝난 뒤 차례로 한다.
- 결과 파일의 [대화]에 부탁마다 알아들은 일, 카드, 영수증, 재생 위치 읽기 결과를 남긴다.
"""

from __future__ import annotations

import copy
import functools
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QObject

from engine.automation import kinds as K
from engine.chat.actions import MAIN_PARAM, clear_args, find_request, mark_items, mark_provenance
from engine.chat.brain import (OPS_2_1, SAID, Chip, NeedTimeline, NotUnderstood, ProposalDraft, Question, Reply,
                               validate_draft)
from engine.chat.context import AssistContext
from engine.chat.rules import GAIN_MAX_DB, GAIN_MIN_DB, RuleBrain, audio_mark_name
from engine.chat.session import ChatSession
from engine.chat.timeparse import context_from_info
from engine.edits.apply import last_active
from engine.edits.journal import Journal, entry_op
from engine.edits.marks import apply_clear, mark_proposal, moved_mark, plan_clear, remade_mark
from engine.edits.scope import check_proposal, requested_dict
from engine.resolve_link.ops import ResolveOps, TimelineInfo
from engine.resolve_link.transport import LuaTransport

from . import connection as conn
from . import fmt
from . import steps
from . import strings_ko as S
from .automation_view import slot_summary
from .cards import ClearCard, ProposalCard, QuestionCard, _fill
from .runs import RunState, _jsonable

TIME_STEP_S = 0.1  # 표시 시각 [−][+]
DB_STEP = 1.0  # dB [−][+]


class ChatController(QObject):
    def __init__(self, window) -> None:
        super().__init__(window)
        self.w = window
        self.brain = RuleBrain()
        self.session = ChatSession(Path(window.state_root), brain=self.brain.name)
        self.queue: List[str] = []  # 일하는 중에 보낸 말
        self.steps: List[Callable[[], None]] = []  # 이번 부탁에서 남은 일
        self.checking = False  # 타임라인을 읽는 중 (보내기 전 ping)
        self.leftovers: List[str] = []  # 첫 카드에 붙일 "못 알아들은 부분"
        self.last_find: Optional[ProposalCard] = None  # "이대로 자동화 버튼에 저장"이 가리키는 카드
        self._stepping = False

    # ── 기록 ──────────────────────────────────────────────────────────

    def _record(self, row: Dict[str, Any]) -> None:
        row = dict(row)
        row.setdefault("at", time.strftime("%H:%M:%S"))
        self.w.session.chat.append(_jsonable(row))

    def _say(self, text: str, chips: Optional[List[Tuple[str, str]]] = None, **extra: Any) -> None:
        self.w.chat.add_helper(text, chips)
        self.w.show_message(text)
        self.session.add("helper", text, tl_key=self.w.journal_key, **extra)

    @property
    def active(self) -> bool:
        return self.checking or bool(self.steps)

    # ── 보내기 ────────────────────────────────────────────────────────

    def send(self, text: str) -> None:
        text = text.strip()
        if not text or self.w.closing:
            return
        self.session.add("me", text, tl_key=self.w.journal_key)
        if self.active or self.w.busy:
            self.queue.append(text)
            self.w.chat.add_helper(S.CHAT_BUSY)
            return
        self._handle(text, None)

    def idle(self) -> None:
        """창 상태가 바뀔 때마다 (일이 끝났는지): 남은 일, 기다리던 말을 이어서 한다."""
        if self.w.closing or self.checking or self._stepping or self.w.busy:
            return
        if self.steps:
            self._run_steps()
            return
        if self.queue:
            self._handle(self.queue.pop(0), None)

    def _context(self, info: Optional[TimelineInfo]) -> AssistContext:
        chat = self.w.settings.data.get("chat") or {}
        journal: List[Dict[str, Any]] = []
        if info is not None:
            try:
                journal = Journal.for_timeline(info, self.w.state_root).summaries(10)
            except OSError:
                journal = []
        last = None
        if self.last_find is not None:
            try:
                p = self.last_find.proposal
                last = {"kind": p.kind, "params": p.params, "proposal_id": p.id, "state": self.last_find.state}
            except RuntimeError:
                last = None
        return AssistContext(time=context_from_info(info) if info is not None else None, info=info,
                             caps=self.w.caps, default_color=chat.get("default_marker_color") or "Green",
                             slots=list(self.w.settings.slots), last_card=last, journal=journal,
                             turns=self.session.recent())

    def _handle(self, text: str, info: Optional[TimelineInfo]) -> None:
        ctx = self._context(info)
        result = self.brain.handle(text, ctx)
        if isinstance(result, NeedTimeline):
            if info is not None:
                self._say(S.CHAT_NO_TIMELINE_INFO)
                return
            self._check_then(text)
            return
        self._dispatch(text, result, ctx)

    def _check_then(self, text: str) -> None:
        """시간을 풀려면 타임라인을 알아야 한다: 먼저 ping + timeline_info (보낸 말 한 번에 한 번)."""
        self.checking = True
        self.w.action = "chat"
        self.w.show_message(S.CHAT_CHECKING)
        self.w._refresh()
        if not self.w.controller.before_action("chat", lambda out: self._checked(text, out)):
            self._checked(text, None)

    def _checked(self, text: str, out: Optional[Dict[str, Any]]) -> None:
        self.checking = False
        if self.w.action == "chat":
            self.w.action = None
        try:
            self._after_check(text, out)
        finally:
            self.w._refresh()  # 여기서 idle()이 남은 일과 기다리던 말을 잇는다

    def _after_check(self, text: str, out: Optional[Dict[str, Any]]) -> None:
        if self.w.closing:
            return
        c = self.w.controller
        if out is None or not c.connected:
            self._say(S.CHAT_NOT_CONNECTED)
            self._record({"text": text, "result": "not_connected"})
            return
        if c.status == conn.OLD_SCRIPT or out.get("state_kind") != "timeline_info":
            self._say(S.CHAT_OLD_SCRIPT)
            self._record({"text": text, "result": "old_script"})
            return
        if c.probe_copy_name:
            self._say(S.CHAT_PROBE_COPY)
            return
        state = out.get("state") if isinstance(out.get("state"), dict) else {}
        info = TimelineInfo.from_result(state)
        if not info.has_timeline:
            self._say(S.CHAT_NO_TIMELINE)
            return
        if context_from_info(info) is None:
            self._say(S.CHAT_NO_TIMELINE_INFO)
            return
        self._handle(text, info)

    # ── 두뇌의 답 ─────────────────────────────────────────────────────

    def _dispatch(self, text: str, result, ctx: AssistContext) -> None:
        row: Dict[str, Any] = {"text": text, "brain": self.brain.name, "result": type(result).__name__}
        if isinstance(result, Reply):
            row["code"] = result.code
            left = [str(x) for x in (result.data or {}).get("leftovers") or []]
            if left:
                row["leftovers"] = left
            self._record(row)
            self._say(self.reply_text(result.code, result.data), self.chip_list(result.chips), code=result.code)
            if left:
                # 답만 하는 부탁에 섞인 다른 부탁 ("도움말 3분에 표시"): 한 것처럼 보이지 않게 알린다
                self._say(S.GUARD["leftover"].format(text=" · ".join(left)), [(S.CHIP_LEFTOVER, " ".join(left))])
            return
        if isinstance(result, Question):
            row.update({"code": result.code, "data": result.data})
            self._record(row)
            if result.code == "over_gain" and result.draft is not None:
                self._over_gain(text, result, ctx)
                return
            self._say(self.question_text(result.code, result.data), self.chip_list(result.chips), code=result.code)
            return
        if isinstance(result, NotUnderstood):
            self._record(row)
            self._say(S.CHAT_NO_MATCH, self.chip_list(result.chips), code="no_match")
            return
        if isinstance(result, ProposalDraft):
            row.update({"commands": [c.to_dict() for c in result.commands], "leftovers": result.leftovers,
                        "notes": result.notes})
            self._record(row)
            self._run_draft(text, result, ctx)

    def _over_gain(self, text: str, q: Question, ctx: AssistContext) -> None:
        card = QuestionCard(self.question_text(q.code, q.data), [],
                            [("yes", S.BTN_OVER_GAIN_YES, True), ("cancel", S.BTN_CANCEL, False)])

        def answer(key: str) -> None:
            if key == "yes":
                card.answered(S.BTN_OVER_GAIN_YES)
                self._run_draft(text, q.draft, ctx)
            else:
                card.answered(S.CHAT_OFFER_DECLINED)

        card.clicked.connect(answer)
        self.w.runs._add_card(card)

    def reply_text(self, code: str, data: Dict[str, Any]) -> str:
        if code == "help":
            return S.CHAT_HELP
        if code == "status":
            return self.status_text(data)
        d = dict(data or {})
        if d.get("length_s") is not None:
            d["length"] = fmt.length(d["length_s"])
        text = _fill(S.CHAT_REPLIES.get(code, S.CHAT_NO_MATCH), d)
        if code == "studio" and d.get("subtitles"):
            text = f"{text} {S.CHAT_STUDIO_SUBTITLES}"
        return text

    def status_text(self, d: Dict[str, Any]) -> str:
        values = {"timeline": d.get("timeline") or "", "length": fmt.length(d.get("length_s")),
                  "fps": d.get("fps") or ""}
        if d.get("playhead_s") is None:
            return S.CHAT_STATUS_NO_PLAYHEAD.format(**values)
        return S.CHAT_STATUS.format(playhead=fmt.clock(d["playhead_s"]), tc=d.get("playhead_tc") or "", **values)

    def question_text(self, code: str, data: Dict[str, Any]) -> str:
        d = dict(data or {})
        if code == "tc_ambiguous":
            d["elapsed"] = fmt.spoken(d.get("elapsed_s"))
        return _fill(S.CHAT_QUESTIONS.get(code, S.CHAT_NO_MATCH), d)

    def chip_list(self, chips: List[Chip]) -> List[Tuple[str, str]]:
        """두뇌의 칩 → (보이는 글, 입력 칸에 채울 글)."""
        out: List[Tuple[str, str]] = []
        for c in chips or []:
            d = dict(c.data or {})
            if c.key == "text":
                t = str(d.get("text") or "")
                if t:
                    out.append((t, t))
            elif c.key == "tc_elapsed":
                said = fmt.spoken(d.get("seconds"))
                out.append((S.CHIP_TC_ELAPSED.format(time=said), str(d.get("text", "")).replace(d.get("raw", ""), said)))
            elif c.key == "tc_resolve":
                tc = str(d.get("tc") or "")
                out.append((S.CHIP_TC_RESOLVE.format(tc=tc), str(d.get("text", "")).replace(d.get("raw", ""), tc)))
            else:
                tmpl = S.CHIPS.get(c.key)
                if tmpl:
                    t = _fill(tmpl, d)
                    out.append((t, t))
        return out

    # ── 할 일 목록 ────────────────────────────────────────────────────

    def _run_draft(self, text: str, draft: ProposalDraft, ctx: AssistContext) -> None:
        bad = set(validate_draft(draft))
        cmds = [c for c in draft.commands if c.op in OPS_2_1 and c.op not in bad]
        for code, data in draft.notes:
            if code == "refused":
                self._say(self.reply_text(data.get("code", ""), data))
        plain = [c for c in cmds if c.op == "mark" and not c.offer]
        cards = [c for c in cmds if c.op in ("mark", "mark_pauses", "mark_spikes", "clear_marks")]
        self.leftovers = list(draft.leftovers)
        if self.leftovers and not cards:
            # 카드가 없는 부탁(재생 위치 등)이면 못 알아들은 부분을 글로 알린다
            leftover = " · ".join(self.leftovers)
            self._say(S.GUARD["leftover"].format(text=leftover), [(S.CHIP_LEFTOVER, " ".join(self.leftovers))])
            self.leftovers = []
        todo: List[Callable[[], None]] = []
        if plain:
            todo.append(functools.partial(self._mark_card, text, plain, ctx, None))
        for c in cmds:
            if c.op == "mark" and c.offer:
                todo.append(functools.partial(self._mark_card, text, [c], ctx, c.offer))
            elif c.op in MAIN_PARAM:
                todo.append(functools.partial(self._find, text, c, ctx))
            elif c.op == "clear_marks":
                todo.append(functools.partial(self._clear, text, c, ctx))
            elif c.op == "jump_to":
                todo.append(functools.partial(self._jump_cmd, text, c, ctx))
            elif c.op == "undo":
                todo.append(functools.partial(self._undo_last, text, ctx))
            elif c.op == "remove_all_ours":
                todo.append(self.w.runs.remove_all)
            elif c.op == "save_slot":
                todo.append(functools.partial(self._save_slot, text, c))
            elif c.op == "help":
                todo.append(functools.partial(self._say, S.CHAT_HELP))
            elif c.op == "status":
                todo.append(functools.partial(self._say, self.status_text(_status(ctx))))
        self.steps += todo
        self._run_steps()

    def _run_steps(self) -> None:
        if self._stepping:
            return
        self._stepping = True
        try:
            while self.steps and not self.w.busy and not self.checking and not self.w.closing:
                self.steps.pop(0)()
        finally:
            self._stepping = False

    def _take_leftovers(self) -> List[str]:
        out, self.leftovers = self.leftovers, []
        return out

    def _chat_meta(self, text: str, **more: Any) -> Dict[str, Any]:
        return {"text": text, **more}

    def _compact(self) -> bool:
        return self.w.layout_name == "tight"

    # ── 표시 (그리고 못 하는 부탁 대신 권하는 표시) ──────────────────────

    def _mark_card(self, text: str, cmds, ctx: AssistContext, offer: Optional[str]) -> None:
        items, requested, notes = mark_items(cmds)
        prov = mark_provenance(items)
        extra: Dict[str, Any] = {}
        if offer == "audio":
            c = cmds[0]
            extra["offer"] = {"db": c.params.get("db"), "direction": c.params.get("direction")}
            prov["db"] = c.provenance.get("db") or "default"
        point_only = ctx.cap("range_markers") is False
        p = mark_proposal(items, ctx.info, origin="chat:rule", request=text, point_only=point_only, offer=offer,
                          provenance=prov, requested=requested, notes=notes, leftovers=self._take_leftovers(),
                          extra=extra)
        guard = check_proposal(p)
        card = ProposalCard(p, compact=self._compact(), guard=guard)
        state = RunState(None, chat=self._chat_meta(text))
        self.w.runs.show_card(state, card)
        self.w.show_message(card.title.text())
        self._record({"event": "card", "text": text, "kind": "mark", "offer": offer, "proposal_id": p.id,
                      "count": p.count, "items": [{k: i[k] for k in ("at", "end", "color", "name")} for i in items],
                      "requested": requested, "guard": guard.to_list(), "provenance": prov})

    # ── 쉬는 곳·튀는 소리 ───────────────────────────────────────────────

    def _find(self, text: str, cmd, ctx: AssistContext) -> None:
        req, prov, slot_no = find_request(cmd, ctx, text=text)
        slot_name = None
        if slot_no is not None:
            try:
                slot_name = self.w.settings.slot(slot_no).get("name")
            except KeyError:
                slot_name = None
        meta = self._chat_meta(text, provenance=prov, requested=requested_dict(cmd.scope), notes=list(cmd.notes),
                               leftovers=self._take_leftovers(), slot_name=slot_name)
        self._record({"event": "find", "text": text, "kind": cmd.op, "params": req.params, "range": req.range,
                      "range_src": req.range_src, "count": req.count, "provenance": prov, "slot": slot_no})
        if not self.w.runs.start_request(req, meta):
            self.steps.insert(0, functools.partial(self._find, text, cmd, ctx))

    def card_shown(self, state: RunState, card: ProposalCard) -> None:
        if card.proposal.kind in MAIN_PARAM:
            self.last_find = card
        g = card.guard
        self._record({"event": "card", "text": (state.chat or {}).get("text"), "kind": card.proposal.kind,
                      "proposal_id": card.proposal.id, "count": card.proposal.count, "scope": card.proposal.scope,
                      "guard": g.to_list() if g is not None else [],
                      "rows": [[r.start, r.end] for r in card.proposal.rows][:200]})

    # ── 도우미 표시 지우기 ──────────────────────────────────────────────

    def _clear(self, text: str, cmd, ctx: AssistContext) -> None:
        args = clear_args(cmd)
        bridge, root = self.w.bridge, self.w.state_root
        leftovers = self._take_leftovers()

        def job(jctx):
            return plan_clear(ResolveOps(LuaTransport(bridge)), colors=args.get("colors"), kinds=args.get("kinds"),
                              lo=args.get("lo"), hi=args.get("hi"), point=bool(args.get("point")), root=root,
                              request=text, cancel=jctx.cancel)

        def done(plan) -> None:
            if self.w.closing:
                return
            plan.provenance = dict(cmd.provenance)
            plan.requested = requested_dict(cmd.scope)
            plan.notes = list(cmd.notes)
            plan.leftovers = leftovers
            guard = check_proposal(plan)
            card = ClearCard(plan, guard=guard, compact=self._compact())
            self.w.runs.show_card(RunState(None, chat=self._chat_meta(text)), card)
            self.w.show_message(card.title.text())
            self._record({"event": "card", "text": text, "kind": "clear_marks", "proposal_id": plan.id,
                          "count": plan.count, "params": plan.params, "straddling": plan.straddling,
                          "total_ours": plan.total_ours, "guard": guard.to_list()})

        def failed(exc: BaseException) -> None:
            if self.w.closing:
                return
            self._say(S.PLAN_FAILED.format(reason=self.w.runs._explain(exc)))
            self._record({"event": "clear_plan", "text": text, "status": "error", "error": f"{exc}"})

        if self.w.runs.jobs.start("clear_plan", job, done, failed, None, cancellable=True):
            self.w.runs._progress(None, S.SCANNING, None, False)
        else:
            self.steps.insert(0, functools.partial(self._clear, text, cmd, ctx))

    def _apply_clear(self, card: ClearCard) -> None:
        if self.w.runs.busy or self.w.closing:
            return
        plan = card.proposal
        root = self.w.state_root
        t0 = time.monotonic()

        def work(ops, jctx):
            return apply_clear(ops, plan, root=root)

        def done(out) -> None:
            if self.w.closing:
                return
            self.w.session.timing("apply", time.monotonic() - t0)
            if out.status == "other_timeline":
                text = S.OTHER_TIMELINE_APPLY.format(name=out.timeline_name or "")
                card.show_message(text)
                self.w.show_message(text)
            elif out.status == "unknown":
                # 답이 끊겨 지워졌는지 모름: 일지는 "지우는 중". 곧바로 한 번 맞춰 본다 (설계 B6.2)
                card.show_unknown(out)
                self.w.show_message(S.CLEAR_RECEIPT_UNKNOWN)
                self.w.runs.recheck()
            else:
                card.show_receipt(out)
                self.w.show_message(card.title.text())
                at = time.strftime("%H:%M")
                for pid in out.closed:
                    other = self.w.runs.by_pid.get(pid)
                    if other is not None and other is not card and getattr(other, "state", "") == "receipt":
                        other.state = "undone"
                        other.close_card(S.CARD_CLEARED)
                        self.w.runs._receipt(getattr(other.proposal, "slot", None),
                                             S.SLOT_RECEIPT_UNDONE.format(at=at))
            self.w.refresh_undo(force=True)
            self._record({"event": "clear", "proposal_id": plan.id, "status": out.status, "expected": out.expected,
                          "deleted": out.deleted, "left": out.left, "closed": out.closed, "error": out.error,
                          "readback": out.receipt.get("readback"), "calls": out.calls})
            self.w.runs._record({"kind": "clear_marks", "slot": None, "stage": "apply", "status": out.status,
                                 "chat": True, "proposal_id": plan.id, "expected": out.expected,
                                 "deleted": out.deleted, "left": out.left, "closed": out.closed})

        def failed(exc: BaseException) -> None:
            if self.w.closing:
                return
            text = S.APPLY_FAILED.format(reason=self.w.runs._explain(exc))
            card.show_message(text)
            self.w.show_message(text)
            self._record({"event": "clear", "proposal_id": plan.id, "status": "error", "error": f"{exc}"})
            self.w.refresh_undo(force=True)

        if self.w.runs.start_write("clear", work, done, failed):
            card.lock()
            self.w.runs._progress(None, S.CLEARING, None, False)

    # ── 재생 위치 ─────────────────────────────────────────────────────

    def _jump_cmd(self, text: str, cmd, ctx: AssistContext) -> None:
        frame = int(cmd.params["frame"])
        self._record({"event": "jump_request", "text": text, "frame": frame})
        for line in _note_texts(cmd.notes):
            self.w.chat.add_helper(line)
        self.jump(frame, ctx.info)

    def jump(self, frame: int, timeline: Any) -> bool:
        """재생 위치만 옮긴다 (편집이 아니라서 확인 카드 없이). timeline: TimelineInfo 또는 카드의 타임라인 기록."""
        if self.w.closing:
            return False
        tctx = context_from_info(timeline)
        if tctx is None:
            self._say(S.CHAT_NO_TIMELINE_INFO)
            return False
        tc = tctx.tc_of(frame)
        at = fmt.clock(tctx.seconds_of(frame))
        if not tc:
            self._say(S.CHAT_JUMP_FAILED.format(reason="tc"))
            return False

        want_uid, want_key, want_name = _timeline_ident(timeline)

        def step(bridge, out):
            ops = ResolveOps(LuaTransport(bridge))
            # 카드를 만든 타임라인이 지금 열려 있을 때만 옮긴다 (다른 타임라인의 같은 시각으로 옮기지 않게)
            now = ops.timeline_info()
            out["timeline_now"] = {"timeline": now.timeline, "timeline_uid": now.timeline_uid}
            if not _same_timeline(now, want_uid, want_key):
                out["jump"] = {"ok": False, "reason": "other_timeline", "calls": {}}
                return
            out["jump"] = ops.jump_to(int(frame), tc)

        def done(out) -> None:
            if self.w.closing:
                return
            r = out.get("jump") or {}
            self._record({"event": "jump", "frame": frame, "tc": tc, "ok": r.get("ok"), "reason": r.get("reason"),
                          "readback_tc": r.get("readback_tc"), "page": r.get("page"), "set_result": r.get("set_result"),
                          "calls": r.get("calls")})
            if r.get("ok") is True:
                self.w.show_message(S.CHAT_JUMP_DONE.format(at=at, tc=tc))
                self.w.chat.add_helper(S.CHAT_JUMP_DONE.format(at=at, tc=tc))
            elif r.get("reason") == "other_timeline":
                self._say(S.CHAT_JUMP_OTHER_TIMELINE.format(name=want_name or ""))
            elif r.get("reason") == "page":
                self._say(S.CHAT_JUMP_PAGE)
            elif r.get("reason") == "outside":
                self._say(S.CHAT_JUMP_OUTSIDE)
            else:
                self._say(S.CHAT_JUMP_UNCONFIRMED.format(readback=r.get("readback_tc") or "?", tc=tc))

        def failed(exc) -> None:
            if self.w.closing:
                return
            cause = getattr(exc, "cause", exc)
            self._say(S.CHAT_JUMP_FAILED.format(reason=steps.explain(cause, answered=True)))
            self._record({"event": "jump", "frame": frame, "tc": tc, "ok": False, "error": f"{cause}"})

        self.w.show_message(S.JUMPING)
        self.w.controller.submit("jump", step, done, failed)
        return True

    # ── 방금 거 취소 ──────────────────────────────────────────────────

    def _undo_last(self, text: str, ctx: AssistContext) -> None:
        try:
            journal = Journal.for_timeline(ctx.info, self.w.state_root)
        except OSError:
            journal = None
        e = last_active(journal) if journal is not None else None
        if e is None:
            self._say(S.CHAT_UNDO_NONE)
            self._record({"event": "undo_last", "text": text, "status": "nothing"})
            return
        pid = str(e.get("proposal_id"))
        request = e.get("request") or pid
        if entry_op(e) == "clear_marks":
            what = S.UNDO_WHAT_CLEAR.format(request=request, n=len(e.get("deleted") or []))
        else:
            what = S.UNDO_WHAT_MARKS.format(request=request, n=len((e.get("created") or {}).get("markers") or []))
        card = QuestionCard(S.CARD_TITLE_UNDO, [what], [("remove", S.BTN_REMOVE, True), ("cancel", S.BTN_CANCEL, False)],
                            blocked_while_busy=("remove",))
        card.clicked.connect(lambda key, c=card: self._undo_answer(c, key, pid, request))
        self.w.runs._add_card(card)
        self.w.show_message(S.CARD_TITLE_UNDO)
        self._record({"event": "undo_last", "text": text, "proposal_id": pid, "op": entry_op(e),
                      "request": request})

    def _undo_answer(self, card: QuestionCard, key: str, pid: str, request: str) -> None:
        if key != "remove":
            card.answered(S.CARD_CANCELLED)
            return
        target = self.w.runs.by_pid.get(pid)
        try:
            if target is not None and target.state not in ("receipt",):
                target = None
        except RuntimeError:
            target = None
        if self.w.runs.undo(pid, card=target, request=request):
            card.answered(S.UNDO_STARTED)

    def undone(self, pid: str, out) -> None:
        self._record({"event": "undo", "proposal_id": pid, "status": out.status, "count": out.deleted,
                      "restored": out.restored, "reopened": out.reopened, "remaining": out.remaining})

    # ── 자동화 버튼에 저장 (구간은 저장하지 않는다) ─────────────────────

    def _save_slot(self, text: str, cmd) -> None:
        card = self.last_find
        try:
            p = card.proposal if card is not None else None
        except RuntimeError:
            p = None
        if p is None:
            self._say(S.SAVE_NOTHING)
            self._record({"event": "save_slot", "text": text, "status": "nothing"})
            return
        slot = cmd.params.get("slot")
        what = slot_summary({"kind": p.kind, "params": p.params})
        lines = []
        if slot is not None:
            try:
                now = slot_summary(self.w.settings.slot(int(slot)))
            except KeyError:
                slot = None
                now = ""
            if slot is not None:
                lines.append(S.SAVE_CONFIRM.format(n=slot, what=what, now=now))
        if (p.scope or {}).get("kind") == "range":
            lines.append(self._range_dropped(p))
        if slot is not None:
            answers = [(f"save:{slot}", S.BTN_SAVE, True), ("cancel", S.BTN_CANCEL, False)]
            title = S.CARD_TITLE_SAVE
        else:
            answers = [(f"save:{s['slot']}", S.SAVE_SLOT_BUTTON.format(n=s["slot"]), False)
                       for s in self.w.settings.slots] + [("cancel", S.BTN_CANCEL, False)]
            title = S.SAVE_CHOOSE
            lines.insert(0, what)
        q = QuestionCard(title, lines, answers)

        def answer(key: str) -> None:
            if key.startswith("save:"):
                n = int(key.split(":", 1)[1])
                if self.save(p, n):
                    q.answered(S.SAVE_DONE.format(n=n))
            else:
                q.answered(S.CARD_CANCELLED)

        q.clicked.connect(answer)
        self.w.runs._add_card(q)

    def _range_dropped(self, p) -> str:
        scope = p.scope or {}
        a = fmt.clock((scope["lo"] - p.tl_start) / (p.fps or 1.0))
        b = fmt.clock((scope["hi"] - p.tl_start) / (p.fps or 1.0))
        return S.SAVE_RANGE_DROPPED.format(a=a, b=b)

    def save(self, p, number: int) -> bool:
        """카드의 설정을 자동화 버튼에 저장한다 (구간은 버린다). 이전 설정은 [이전 설정으로 되돌리기]로."""
        kind = p.kind
        params = copy.deepcopy({k: v for k, v in p.params.items() if not isinstance(v, (list, dict))})
        params = K.normalize_params(kind, params)
        try:
            self.w.settings.save_slot(int(number), kind, S.KIND_NAMES.get(kind, kind), params)
        except (OSError, ValueError, KeyError) as exc:
            self._say(S.SETTINGS_SAVE_FAILED.format(error=exc))
            return False
        self.w._slots_changed()
        dropped = (p.scope or {}).get("kind") == "range"
        self.w.show_message(S.SAVE_DONE.format(n=number))
        self.w.chat.add_helper(S.SAVE_DONE.format(n=number))
        if dropped:
            self.w.chat.add_helper(self._range_dropped(p))
        self._record({"event": "save_slot", "slot": int(number), "kind": kind, "params": params,
                      "range_dropped": [p.scope.get("lo"), p.scope.get("hi")] if dropped else None,
                      "from_proposal": p.id})
        return True

    # ── 카드의 단추 (runs.py가 넘겨준다) ────────────────────────────────

    def card_key(self, state: RunState, card, key: str) -> None:
        p = card.proposal
        if key.startswith("view:"):
            i = int(key.split(":", 1)[1])
            frame = self._frame_of(card, i)
            if frame is not None:
                self.jump(frame, p.timeline)
            return
        if key == "leftover":
            self.w.chat.fill(" ".join(getattr(p, "leftovers", None) or []))
            return
        if key.startswith("save:"):
            try:
                n = int(key.split(":", 1)[1])
            except ValueError:
                return
            self.save(p, n)
            return
        if key == "apply" and isinstance(card, ClearCard):
            self._apply_clear(card)
            return
        if key.startswith(("dec:", "inc:")):
            self._edit(state, card, key)

    def _frame_of(self, card, i: int) -> Optional[int]:
        p = card.proposal
        if isinstance(card, ClearCard):
            return p.targets[i]["frame"] if 0 <= i < len(p.targets) else None
        rows = p.rows
        return rows[i].start if 0 <= i < len(rows) else None

    def _edit(self, state: RunState, card: ProposalCard, key: str) -> None:
        """[−][+]: 두뇌를 다시 부르지 않고 고친다. 표시 시각·이름의 dB는 여기서, 쉰 길이·튀는 정도는 다시 계산."""
        if not card.editable or self.w.closing:
            return
        sign = -1 if key.startswith("dec:") else 1
        what = key.split(":", 1)[1]
        p = card.proposal
        if what.startswith("at:"):
            i = int(what.split(":", 1)[1])
            new = moved_mark(p, i, sign * max(1, int(round(TIME_STEP_S * (p.fps or 1.0)))))
            card.set_proposal(new, check_proposal(new))
            self._record({"event": "edit", "proposal_id": p.id, "what": "at", "index": i, "delta_s": sign * TIME_STEP_S})
            return
        if what == "db":
            offer = dict(p.params.get("offer") or {})
            direction = offer.get("direction")
            top = GAIN_MAX_DB if direction == "up" else -GAIN_MIN_DB
            db = max(1.0, min(top, float(offer.get("db") or 0) + sign * DB_STEP))
            offer["db"] = db
            items = [dict(i, name=audio_mark_name(db, direction), src=dict(i.get("src") or {}))
                     for i in p.params.get("items") or []]
            new = remade_mark(p, items, offer=offer)
            new.provenance = dict(p.provenance, db=SAID)
            card.set_proposal(new, check_proposal(new))
            self._record({"event": "edit", "proposal_id": p.id, "what": "db", "value": db})
            return
        if what in ("min_s", "above_lu") and state.req is not None:
            kind = K.kind(state.req.kind)
            spec = kind.param(what) if kind is not None else None
            step = 1.0 if what == "above_lu" else (spec.step if spec is not None and spec.step else TIME_STEP_S)
            params = dict(state.req.params)
            params[what] = float(params.get(what) or spec.default) + sign * step
            params = kind.normalize(params)
            if params[what] == state.req.params.get(what):
                return  # 범위 끝
            state.req.params = params
            if state.chat is not None:
                prov = dict(state.chat.get("provenance") or {})
                prov[what] = SAID
                state.chat["provenance"] = prov
            self._record({"event": "edit", "proposal_id": p.id, "what": what, "value": params[what]})
            if self.w.runs.replan(state, card):
                card.lock()

    def applied(self, card, out) -> None:
        p = card.proposal
        self._record({"event": "apply", "proposal_id": p.id, "kind": p.kind, "status": out.status,
                      "expected": out.expected, "placed": out.placed, "readback": out.receipt.get("readback"),
                      "created": [c.get("frame") for c in out.created][:200]})


def _timeline_ident(timeline: Any) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """TimelineInfo 또는 카드의 타임라인 기록(사전) → (uid, 일지 열쇠, 이름)."""
    from engine.edits.journal import key_for

    if isinstance(timeline, dict):
        return timeline.get("timeline_uid"), timeline.get("key"), timeline.get("timeline") or timeline.get("name")
    if timeline is None:
        return None, None, None
    return getattr(timeline, "timeline_uid", None), key_for(timeline), getattr(timeline, "timeline", None)


def _same_timeline(now: TimelineInfo, uid: Optional[str], key: Optional[str]) -> bool:
    """지금 열린 타임라인이 카드의 것인지. 알 수 없으면(기록이 없음) 막지 않는다 (재생 위치만 옮기는 일)."""
    from engine.edits.journal import key_for

    if not now.has_timeline:
        return False
    if uid and now.timeline_uid:
        return uid == now.timeline_uid
    if key:
        return key == key_for(now)
    return True


def _status(ctx: AssistContext) -> Dict[str, Any]:
    from engine.chat.rules import _status as status

    return status(ctx)


def _note_texts(notes) -> List[str]:
    from .cards import note_lines

    return note_lines(notes)
