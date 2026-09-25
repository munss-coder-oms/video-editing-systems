"""자동화 버튼을 누른 뒤의 흐름 (설계 B2.5, B2.3, B6.3, B7.3 M2/M3, B10).

누름 → (창이 먼저 ping) → 계산(JobRunner, [멈추기]) → 목소리를 모르면 목소리 고르기 카드 →
같은 종류의 표시가 이미 있으면 [바꾸기] [더하기] → 확인 카드 → [리졸브에 넣기](멈출 수 없음) → 영수증.
영수증의 [되돌리기], 아래쪽 되돌리기 목록, "도우미가 넣은 것 모두 빼기"도 여기서 한다.

- 리졸브를 바꾸는 요청은 [리졸브에 넣기]·되돌리기·모두 빼기를 누른 뒤에만 나간다. 계산은 읽기뿐이다.
  3초 듣기는 이 PC에서 소리 파일만 읽는다.
- 한 번에 한 가지 일만 (JobRunner). 일하는 동안: [연결 확인]·[결과 저장]·대화 입력·⚙는 그대로,
  다른 버튼·[리졸브에 넣기]·되돌리기는 꺼 둔다.
- 결과 파일용으로 실행마다 한 줄(session.runs), 목소리 고르기(session.voice), 확인 질문(session.manual)을 남긴다.
"""

from __future__ import annotations

import copy
import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QObject

from engine import ffmpeg
from engine.analysis_cache import AnalysisCache
from engine.automation.kinds import is_ready
from engine.automation.plan import (PlanEnv, PlanRefused, SlotRequest, VoiceMemory, VoiceOverride, VoiceQuestion,
                                    plan_slot)
from engine.edits.apply import (OUR_PREFIX, active_entries, apply_markers, remove_all_ours, resume_markers,
                                scan_ours, undo_proposal)
from engine.edits.journal import Journal
from engine.edits.proposal import Proposal
from engine.edits.scope import check_proposal
from engine.probe import probe as probe_media
from engine.resolve_link.ops import MAX_ADD_MARKERS, ResolveOps
from engine.resolve_link.transport import LuaTransport

from . import fmt
from . import listen
from . import steps
from . import strings_ko as S
from .cards import Card, ClearCard, ProposalCard, QuestionCard
from .jobs import JobRunner
from .voice_picker import VoicePickerCard

DEFAULT_SEC_PER_MIN = 6.0  # 처음 잴 때 1분에 몇 초 걸릴지 (첫 측정 전 어림)
MIN_PERF_MEDIA_S = 30.0  # 이보다 짧은 파일은 걸린 시간을 기록하지 않는다 (어림이 흔들리므로)


@dataclass
class RunState:
    """버튼 한 번 누른 일 (목소리를 고른 뒤 다시 계산해도 같은 것). 대화에서 온 일도 같은 모양.

    chat: 대화에서 온 일이면 {text, provenance, requested, notes, leftovers, slot_name} (카드의 출처·범위 지킴이).
    card: [−][+]로 고쳐 다시 계산할 때 같은 자리에서 바꿀 카드.
    """

    req: Optional[SlotRequest]
    probe_reads: Dict[str, Any] = field(default_factory=dict)
    replace: List[str] = field(default_factory=list)
    started: float = field(default_factory=time.monotonic)
    chat: Optional[Dict[str, Any]] = None
    card: Optional[Any] = None

    @property
    def number(self) -> Optional[int]:
        return self.req.slot if self.req is not None else None

    @property
    def kind(self) -> Optional[str]:
        return self.req.kind if self.req is not None else None


class RunController(QObject):
    def __init__(self, window) -> None:
        super().__init__(window)
        self.w = window
        self.jobs = JobRunner(self)
        self.jobs.changed.connect(self._jobs_changed)
        self.cache = AnalysisCache(Path(window.state_root) / "cache" / "analysis")
        self.listen_folder = Path(window.state_root) / "cache" / "listen"
        self.cards: List[Card] = []
        self.pending: Dict[int, ProposalCard] = {}  # 버튼 → 답을 기다리는 확인 카드
        self.by_pid: Dict[str, ProposalCard] = {}
        self.running_slot: Optional[int] = None
        self.manual_cards: Dict[str, QuestionCard] = {}
        self.probe_file: Callable[[str], Any] = probe_media  # 시험에서 바꾼다
        self.exists: Callable[[str], bool] = os.path.isfile

    # ── 공통 ──────────────────────────────────────────────────────────

    def make_ops(self, bridge=None) -> ResolveOps:
        return ResolveOps(LuaTransport(bridge if bridge is not None else self.w.bridge))

    @property
    def busy(self) -> bool:
        return self.jobs.busy

    def _jobs_changed(self) -> None:
        busy = self.jobs.busy
        if not busy:
            self.running_slot = None
            self.w.automation.hide_progress()
        for card in list(self.cards):
            try:
                card.set_busy(busy)
            except RuntimeError:
                self.cards.remove(card)  # 오래돼서 지운 카드
        self.w._refresh()

    def _add_card(self, card: Card) -> Card:
        self.cards.append(card)
        card.set_busy(self.jobs.busy)
        self.w.chat.add_card(card)
        return card

    def _say(self, text: str) -> None:
        self.w.chat.add_helper(text)
        self.w.show_message(text)

    def _receipt(self, number: Optional[int], text: Optional[str]) -> None:
        if number is not None:
            self.w.automation.set_receipt(number, text)

    def _progress(self, number: Optional[int], text: str, frac: Optional[float], can_stop: bool) -> None:
        self.running_slot = number
        self.w.automation.show_progress(number, text, frac, can_stop)
        self.w._refresh()

    def _explain(self, exc: BaseException) -> str:
        if isinstance(exc, ffmpeg.FFmpegError):
            return str(exc)
        return steps.explain(exc, answered=True)

    def _record(self, row: Dict[str, Any]) -> None:
        row = dict(row)
        row.setdefault("at", time.strftime("%H:%M:%S"))
        self.w.session.runs.append(row)

    def stop(self) -> None:
        """[멈추기]: 계산만 멈출 수 있다."""
        self.jobs.cancel()

    def shutdown(self) -> None:
        self.jobs.cancel()

    # ── 버튼 → 계산 ───────────────────────────────────────────────────

    def start_slot(self, number: int) -> bool:
        if self.jobs.busy or self.w.closing:
            return False
        try:
            slot = self.w.settings.slot(number)
        except KeyError:
            return False
        kind = slot.get("kind")
        if not is_ready(kind):
            self._say(S.PLAN_REFUSED["not_ready"])
            return False
        req = SlotRequest(slot=number, kind=kind, name=slot.get("name") or S.SLOT_DEFAULT_NAMES.get(number, ""),
                          params=copy.deepcopy(slot.get("params") or {}), origin=f"button:{number}")
        return self._plan(RunState(req))

    def start_request(self, req: SlotRequest, chat: Dict[str, Any], *, card: Optional[ProposalCard] = None) -> bool:
        """대화의 "5분~6분에서 쉬는 곳 표시" (버튼과 같은 계산, 말한 범위 안쪽만). 창이 먼저 ping을 했다."""
        if self.jobs.busy or self.w.closing:
            return False
        return self._plan(RunState(req, chat=chat, card=card))

    def replan(self, state: RunState, card: ProposalCard) -> bool:
        """카드의 [−][+]: 고친 값으로 다시 계산해 같은 카드를 바꾼다 (AI를 부르지 않는다)."""
        state.card = card
        return self._plan(state)

    def _plan(self, state: RunState, *, override: Optional[VoiceOverride] = None, ask_voice: bool = False) -> bool:
        voice = VoiceMemory.from_settings(self.w.settings.voice)
        caps = self.w.caps
        bridge = self.w.bridge

        def job(ctx):
            env = PlanEnv(ops=self.make_ops(bridge), cache=self.cache, caps=caps, probe_file=self.probe_file,
                          probe_reads=state.probe_reads, cancel=ctx.cancel, progress=ctx.progress,
                          exists=self.exists)
            return plan_slot(state.req, env, voice, override=override, ask_voice=ask_voice)

        started = self.jobs.start(
            "plan", job, lambda r: self._planned(state, r), lambda e: self._plan_failed(state, e),
            lambda stage, frac, info: self._plan_progress(state, stage, frac, info), cancellable=True)
        if started:
            state.started = time.monotonic()
            self._plan_progress(state, 1, 0.0, {})
        return started

    def _plan_progress(self, state: RunState, stage: int, frac: float, info: Dict[str, Any]) -> None:
        stage = max(1, min(len(S.STAGE_NAMES), int(stage)))
        parts = [S.PROGRESS_LINE.format(i=stage, n=len(S.STAGE_NAMES), stage=S.STAGE_NAMES[stage - 1])]
        media_s = info.get("media_s")
        if stage == 2 and isinstance(media_s, (int, float)) and media_s > 0 and frac < 1.0:
            spm = self.w.settings.sec_per_min(state.req.kind) or DEFAULT_SEC_PER_MIN
            left = media_s / 60.0 * spm * (1.0 - frac)
            if left >= 1.0:
                parts.append(S.PROGRESS_ETA_M.format(m=int(math.ceil(left / 60.0))) if left >= 90 else
                             S.PROGRESS_ETA_S.format(s=int(math.ceil(left))))
        parts.append(S.PROGRESS_FREE)
        overall = (stage - 1 + max(0.0, min(1.0, frac))) / len(S.STAGE_NAMES)
        self._progress(state.number, S.PROGRESS_SEP.join(parts), overall, True)

    def _plan_failed(self, state: RunState, exc: BaseException) -> None:
        if self.w.closing:
            return
        elapsed = round(time.monotonic() - state.started, 3)
        row = {"kind": state.req.kind, "slot": state.number, "stage": "plan", "elapsed_s": elapsed,
               "chat": state.chat is not None}
        if state.card is not None:
            try:
                state.card.unlock()
            except RuntimeError:
                pass
        if isinstance(exc, ffmpeg.Cancelled):
            self._say(S.STOPPED)
            self._receipt(state.number, S.SLOT_RECEIPT_STOPPED)
            self._record({**row, "status": "stopped"})
            return
        if isinstance(exc, PlanRefused):
            text = S.PLAN_REFUSED.get(exc.code, exc.code)
            if exc.code == "no_voice_items":
                text = text.format(n=int(exc.detail.get("stream") or 0) + 1)
                card = QuestionCard(text, [], [("again", S.BTN_PICK_AGAIN, True), ("cancel", S.BTN_CANCEL, False)],
                                    blocked_while_busy=("again",))
                card.clicked.connect(lambda key, c=card: self._refused_answer(state, c, key))
                self._add_card(card)
                self.w.show_message(text)
            else:
                self._say(text)
            self._receipt(state.number, None)
            self._record({**row, "status": "refused", "code": exc.code, "detail": _jsonable(exc.detail)})
            return
        text = S.PLAN_FAILED.format(reason=self._explain(exc))
        self._say(text)
        self._receipt(state.number, S.SLOT_RECEIPT_FAILED)
        self._record({**row, "status": "failed", "error": f"{type(exc).__name__}: {exc}"})

    def _refused_answer(self, state: RunState, card: QuestionCard, key: str) -> None:
        if key == "again":
            if self._plan(state, ask_voice=True):
                card.answered(S.BTN_PICK_AGAIN)
        else:
            card.answered(S.CARD_CANCELLED)

    def _planned(self, state: RunState, result) -> None:
        if self.w.closing:
            return
        if isinstance(result, VoiceQuestion):
            self._ask_voice(state, result)
            return
        p: Proposal = result
        elapsed = round(time.monotonic() - state.started, 3)
        perf = p.debug.get("perf") or {}
        analyzed = perf.get("analyzed_s") or 0.0
        if analyzed >= MIN_PERF_MEDIA_S and perf.get("elapsed_s"):
            self.w.settings.record_perf(p.kind, float(perf["elapsed_s"]) / (analyzed / 60.0))
        self.w.session.timing("plan", elapsed)
        if state.chat is not None:
            chat = state.chat
            p.origin = "chat:rule"
            p.provenance = dict(chat.get("provenance") or {})
            p.requested = chat.get("requested")
            p.notes = list(chat.get("notes") or [])
            p.leftovers = list(chat.get("leftovers") or [])
        self._record({
            "kind": p.kind, "slot": p.slot, "stage": "plan", "status": "planned", "proposal_id": p.id,
            "chat": state.chat is not None, "requested": p.requested,
            "count": p.count, "found": p.found, "elapsed_s": elapsed, "params": p.params, "scope": p.scope,
            "voice": None if p.voice is None else {"stream": p.voice.stream, "signature": p.voice.signature,
                                                   "remembered": p.voice.remembered, "mix": p.voice.mix,
                                                   "doubled": p.voice.doubled, "profile": p.voice.profile},
            "warnings": p.warnings, "point_only": p.point_only, "tracks": p.tracks,
            "timeline": {k: p.timeline.get(k) for k in ("timeline", "start_frame", "end_frame", "fps", "key")},
            "debug": _jsonable(p.debug),
        })
        if state.chat is not None:
            # 대화의 찾기는 이전 표시를 바꾸지 않고 더한다 (지우려면 "…표시 지워줘"). 묻지 않는다.
            state.replace = []
            if state.card is not None:
                self._update_card(state, p)
            else:
                self._show_proposal(state, p, 0)
            return
        prev = []
        if p.count:
            try:
                prev = active_entries(Journal(self.w.state_root, p.timeline.get("key") or ""), p.kind)
            except OSError:
                prev = []
        if prev:
            n = sum(len((e.get("created") or {}).get("markers") or []) for e in prev)
            word = fmt.color_word(p.color)
            card = QuestionCard(S.RERUN_QUESTION.format(color_word=word, n=n), [],
                                [("replace", S.BTN_REPLACE, True), ("add", S.BTN_ADD_MORE, False)])
            ids = [e.get("proposal_id") for e in prev if e.get("proposal_id")]
            card.clicked.connect(lambda key, c=card: self._rerun_answer(state, p, c, key, ids, n))
            self._add_card(card)
            self._receipt(state.number, S.SLOT_RECEIPT_WAITING)
            return
        state.replace = []
        self._show_proposal(state, p, 0)

    def _rerun_answer(self, state: RunState, p: Proposal, card: QuestionCard, key: str, ids: List[str],
                      n: int) -> None:
        if key == "replace":
            state.replace = list(ids)
            card.answered(S.BTN_REPLACE)
            self._show_proposal(state, p, n)
        else:
            state.replace = []
            card.answered(S.BTN_ADD_MORE)
            self._show_proposal(state, p, 0)

    def guard_for(self, p) -> Any:
        """대화에서 온 제안만 범위 지킴이로 본다 (버튼은 부탁한 범위가 없다)."""
        return check_proposal(p) if getattr(p, "from_chat", False) else None

    def save_slots(self, p) -> Optional[List]:
        if not getattr(p, "from_chat", False) or p.kind not in ("mark_pauses", "mark_spikes"):
            return None
        out = []
        for s in self.w.settings.slots:
            name = s.get("name") or S.SLOT_DEFAULT_NAMES.get(s.get("slot"), "")
            out.append((s.get("slot"), S.SAVE_SLOT_CHOICE.format(n=s.get("slot"), name=name)))
        return out

    def _update_card(self, state: RunState, p: Proposal) -> None:
        card = state.card
        state.card = None
        try:
            old_pid = card.proposal.id
            card.unlock()
            card.set_proposal(p, self.guard_for(p))
        except RuntimeError:
            self._show_proposal(state, p, 0)
            return
        self.by_pid.pop(old_pid, None)
        self.by_pid[p.id] = card
        self.w.show_message(card.title.text())

    def _show_proposal(self, state: RunState, p: Proposal, replace_count: int) -> None:
        old = self.pending.pop(state.number, None) if state.number is not None else None
        if old is not None:
            old.supersede()
        slot_name = (state.chat or {}).get("slot_name")
        card = ProposalCard(p, replace_count=replace_count, compact=self.w.layout_name == "tight",
                            guard=self.guard_for(p), save_slots=self.save_slots(p), slot_name=slot_name)
        card.clicked.connect(lambda key, c=card: self._card_action(state, c, key))
        self._add_card(card)
        self.by_pid[p.id] = card
        if state.chat is not None:
            self.w.chat_flow.card_shown(state, card)
        if p.count:
            if state.number is not None:
                self.pending[state.number] = card
            self._receipt(state.number, S.SLOT_RECEIPT_WAITING)
            self.w.show_message(S.CARD_TITLE_FOUND)
        else:
            self._receipt(state.number, S.SLOT_RECEIPT_NONE.format(at=time.strftime("%H:%M")))
            self.w.show_message(S.CARD_TITLE_NONE)

    # ── 목소리 고르기 ──────────────────────────────────────────────────

    def _ask_voice(self, state: RunState, q: VoiceQuestion) -> None:
        card = VoicePickerCard(q)
        card.listen.connect(lambda i, c=card: self.listen(q, i, c))
        card.picked.connect(lambda i, c=card: self._voice_picked(state, q, i, c))
        card.clicked.connect(lambda key, c=card: self._voice_cancel(state, c, key))
        self._add_card(card)
        self._receipt(state.number, S.SLOT_RECEIPT_WAITING)
        self.w.show_message(S.VOICE_TITLE)
        self.w.session.voice.append({"at": time.strftime("%H:%M:%S"), "event": "asked", "reason": q.reason,
                                     "signature": q.signature, "streams": len(q.streams), "mix": q.mix,
                                     "doubled": q.doubled, "previous": q.previous, "change_reason": q.change_reason,
                                     "profiles": {c.index: c.profile for c in q.streams},
                                     "enabled": [c.index for c in q.streams if c.enabled_on_timeline],
                                     "debug": _jsonable(q.debug)})

    def listen(self, q: VoiceQuestion, stream: int, card: Optional[VoicePickerCard] = None) -> None:
        """[▶ 3초 듣기]: 파일에서 3초를 꺼내 이 PC에서 튼다 (리졸브는 건드리지 않는다)."""
        choice = next((c for c in q.streams if c.index == stream), None)
        if choice is None:
            return
        if card is not None:
            card.set_listening(stream)
        folder, path, probe_file = self.listen_folder, q.path, self.probe_file

        def work():
            return listen.make_excerpt(path, stream, choice.best_start, folder, media=probe_file(path))

        def done(wav, error) -> None:
            if card is not None:
                try:
                    card.set_listening(None)
                except RuntimeError:
                    pass
            if error is not None:
                self.w.show_message(S.LISTEN_FAILED.format(reason=self._explain(error)))
                return
            if listen.play(wav):
                self.w.show_message(S.LISTENING.format(n=stream + 1))
            else:
                self.w.show_message(S.LISTEN_NO_PLAYER)

        self.w.shorts.run(work, done, name="listen")

    def _voice_picked(self, state: RunState, q: VoiceQuestion, stream: int, card: VoicePickerCard) -> None:
        if self.jobs.busy:
            return
        choice = next((c for c in q.streams if c.index == stream), None)
        try:
            self.w.settings.set_voice(q.signature, stream, mix=q.mix, profile=choice.profile if choice else None)
        except OSError:
            pass  # 저장을 못 해도 이번 계산은 고른 소리로 한다
        card.done(stream)
        self.w.session.voice.append({"at": time.strftime("%H:%M:%S"), "event": "picked", "signature": q.signature,
                                     "stream": stream, "mix": q.mix, "reason": q.reason,
                                     "profile": choice.profile if choice else None})
        self._plan(state, override=VoiceOverride(q.signature, stream))

    def _voice_cancel(self, state: RunState, card: VoicePickerCard, key: str) -> None:
        card.done(None, S.CARD_CANCELLED)
        self._receipt(state.number, None)

    # ── 카드 단추 ─────────────────────────────────────────────────────

    def show_card(self, state: RunState, card: Card) -> Card:
        """대화가 만든 카드(표시, 지우기)를 버튼 카드와 같은 곳에서 다룬다."""
        card.clicked.connect(lambda key, c=card: self._card_action(state, c, key))
        self._add_card(card)
        self.by_pid[card.proposal.id] = card
        return card

    def _card_action(self, state: RunState, card: ProposalCard, key: str) -> None:
        pid = card.proposal.id
        if ":" in key or key == "leftover" or isinstance(card, ClearCard) and key == "apply":
            self.w.chat_flow.card_key(state, card, key)
            return
        if key == "apply":
            self.apply(state, card)
        elif key == "force":
            self.apply(state, card, force=True)
        elif key == "cancel":
            card.cancel(S.CHAT_OFFER_DECLINED if getattr(card.proposal, "offer", None) else None)
            if state.number is not None and self.pending.get(state.number) is card:
                self.pending.pop(state.number, None)
                self._receipt(state.number, None)
            self._record({"kind": card.proposal.kind, "slot": state.number, "stage": "card", "status": "cancelled",
                          "proposal_id": pid})
        elif key == "voice":
            self._plan(state, ask_voice=True)
        elif key == "replan":
            if self._plan(state):
                card.supersede()
        elif key in ("undo", "remove_placed"):
            self.undo(pid, card=card)
        elif key == "resume":
            self.apply(state, card, resume=True)

    def apply(self, state: RunState, card: ProposalCard, *, force: bool = False, resume: bool = False) -> bool:
        if self.jobs.busy or self.w.closing:
            return False
        p = card.proposal
        bridge, root = self.w.bridge, self.w.state_root
        replace = [] if resume else list(state.replace)

        def job(ctx):
            ops = self.make_ops(bridge)
            prog = (lambda i, n: ctx.progress(0, i / max(1, n), {"i": i, "n": n}))
            if resume:
                return resume_markers(ops, p, root=root, progress=prog)
            return apply_markers(ops, p, root=root, replace=replace, force=force, progress=prog)

        chunks = max(1, math.ceil(p.count / MAX_ADD_MARKERS))
        t0 = time.monotonic()
        started = self.jobs.start(
            "apply", job, lambda out: self._applied(state, card, out, time.monotonic() - t0, force, resume),
            lambda exc: self._apply_failed(state, card, exc),
            lambda _s, frac, info: self._progress(state.number, S.APPLYING.format(i=info.get("i", 1),
                                                                                  n=info.get("n", chunks)),
                                                  frac, False),
            cancellable=False)
        if started:
            card.lock()
            self._progress(state.number, S.APPLYING.format(i=1, n=chunks), 0.0, False)
        return started

    def _applied(self, state: RunState, card: ProposalCard, out, elapsed: float, force: bool, resume: bool) -> None:
        if self.w.closing:
            return
        p = card.proposal
        self.w.session.timing("apply", elapsed)
        outside = None
        scope = p.scope or {}
        if scope.get("kind") in ("range", "in_out") and scope.get("lo") is not None:
            # 말한 범위 밖에 새 표시가 생기지 않았는지 (다시 읽은 표시로, 시험 T2 15번)
            outside = sum(1 for c in out.created if not (scope["lo"] <= int(c.get("frame") or 0) < scope["hi"]))
        self._record({
            "kind": p.kind, "slot": state.number, "stage": "resume" if resume else "apply", "status": out.status,
            "chat": state.chat is not None, "scope": scope, "outside_range": outside,
            "created_frames": [c.get("frame") for c in out.created][:200],
            "proposal_id": p.id, "expected": out.expected, "placed": out.placed, "failed": out.failed,
            "skipped_existing": out.skipped_existing, "point_fallback": out.point_fallback, "dur_ok": out.dur_ok,
            "replaced": out.replaced, "forced": force, "error": out.error, "readback": out.receipt.get("readback"),
            "calls": out.calls, "elapsed_s": round(elapsed, 3),
        })
        if out.status == "changed":
            card.unlock()
            card.show_changed()
            self.w.show_message(S.TIMELINE_CHANGED)
            return
        if out.status in ("other_timeline", "missing"):
            text = S.OTHER_TIMELINE_APPLY.format(name=out.timeline_name or "")
            card.show_message(text)
            self.w.show_message(text)
            return
        if state.number is not None and self.pending.get(state.number) is card:
            self.pending.pop(state.number, None)
        card.show_receipt(out)
        for old_pid in out.replaced:
            old_card = self.by_pid.get(old_pid)
            if old_card is not None and old_card is not card:
                old_card.replaced()
        word = fmt.color_word(p.color)
        if out.status == "applied":
            self._receipt(state.number, S.SLOT_RECEIPT.format(at=out.at, color_word=word, n=out.placed))
        elif out.status == "partial":
            self._receipt(state.number, S.SLOT_RECEIPT_PARTIAL.format(at=out.at, placed=out.placed,
                                                                      expected=out.expected))
        else:
            self._receipt(state.number, S.SLOT_RECEIPT_FAILED)
        self.w.show_message(card.title.text())
        self.w.refresh_undo(force=True)
        if state.chat is not None:
            self.w.chat_flow.applied(card, out)
        if out.placed:
            self.ask_manual("M3")
            if p.kind == "mark_spikes":
                self.ask_manual("M2")

    def _apply_failed(self, state: RunState, card: ProposalCard, exc: BaseException) -> None:
        if self.w.closing:
            return
        text = S.APPLY_FAILED.format(reason=self._explain(exc))
        card.show_message(text)
        self.w.show_message(text)
        self._receipt(state.number, S.SLOT_RECEIPT_FAILED)
        self._record({"kind": card.proposal.kind, "slot": state.number, "stage": "apply", "status": "error",
                      "proposal_id": card.proposal.id, "error": f"{type(exc).__name__}: {exc}"})
        self.w.refresh_undo(force=True)

    # ── 되돌리기 ──────────────────────────────────────────────────────

    def undo(self, pid: str, *, card: Optional[ProposalCard] = None, request: Optional[str] = None) -> bool:
        """카드 하나를 뺀다 (delete_markers prefix). 그 일지의 타임라인이 열려 있을 때만."""
        if self.jobs.busy or self.w.closing:
            return False
        card = card or self.by_pid.get(pid)
        key = card.proposal.timeline.get("key") if card is not None else self.w.journal_key
        number = card.proposal.slot if card is not None else None
        bridge, root = self.w.bridge, self.w.state_root

        def job(ctx):
            return undo_proposal(self.make_ops(bridge), pid, root=root, journal_key=key)

        started = self.jobs.start("undo", job, lambda out: self._undone(pid, card, number, request, out),
                                  lambda exc: self._undo_failed(pid, card, exc), None, cancellable=False)
        if started:
            if card is not None:
                card.lock()
            self._progress(number, S.UNDOING, None, False)
        return started

    def _undone(self, pid: str, card: Optional[ProposalCard], number: Optional[int], request: Optional[str],
                out) -> None:
        if self.w.closing:
            return
        self._record({"stage": "undo", "proposal_id": pid, "status": out.status, "deleted": out.deleted,
                      "already_gone": out.already_gone, "remaining": out.remaining, "calls": out.calls,
                      "message": out.message, "restored": out.restored, "reopened": out.reopened})
        if out.status == "other_timeline":
            text = out.message or S.UNDO_FAILED.format(reason=out.status)
            if card is not None:
                card.show_message(text)
            self._say(text)
            return
        if out.status == "missing":
            if card is not None:
                card.show_message(S.UNDO_MISSING)
            self._say(S.UNDO_MISSING)
            return
        at = time.strftime("%H:%M")
        if card is not None:
            card.show_undone(out, at)
            self.w.show_message(card.title.text())
        elif out.restored:
            self._say(S.CLEAR_UNDONE.format(at=at, n=out.deleted))
            if out.already_gone:
                self.w.chat.add_helper(S.CLEAR_UNDO_SKIPPED.format(n=out.already_gone))
        else:
            self._say(S.UNDO_DONE_LINE.format(request=request or pid, n=out.deleted))
            if out.already_gone:
                self.w.chat.add_helper(S.UNDO_SKIPPED.format(n=out.already_gone))
        for other in out.reopened:
            # 지우기를 되돌려 표시가 다시 들어온 카드: 영수증과 [되돌리기]를 다시 보인다
            back = self.by_pid.get(other)
            if back is not None and getattr(back, "outcome", None) is not None and hasattr(back, "show_receipt"):
                try:
                    back.show_receipt(back.outcome)
                except RuntimeError:
                    pass
        self.w.chat_flow.undone(pid, out)
        if out.remaining:
            self.w.chat.add_helper(S.UNDO_LEFT.format(n=out.remaining))
        self._receipt(number, S.SLOT_RECEIPT_UNDONE.format(at=at))
        self.w.refresh_undo(force=True)

    def _undo_failed(self, pid: str, card: Optional[ProposalCard], exc: BaseException) -> None:
        if self.w.closing:
            return
        text = S.UNDO_FAILED.format(reason=self._explain(exc))
        if card is not None:
            card.show_message(text)
        self._say(text)
        self._record({"stage": "undo", "proposal_id": pid, "status": "error", "error": f"{type(exc).__name__}: {exc}"})

    def undo_from_list(self, pid: str) -> bool:
        """아래쪽 되돌리기 목록에서 한 줄: 먼저 묻는다."""
        if self.jobs.busy or self.w.closing:
            return False
        entry = next((e for e in self.w.footer.entries if e.get("proposal_id") == pid), None)
        request = (entry or {}).get("request") or pid
        n = int((entry or {}).get("markers") or 0)
        what = S.UNDO_WHAT.format(request=request, n=n)
        if (entry or {}).get("op") == "clear_marks":
            what = S.UNDO_WHAT_CLEAR.format(request=request, n=int((entry or {}).get("deleted") or 0))
        if self.w.choose(S.UNDO_CONFIRM_TITLE, S.UNDO_CONFIRM.format(what=what), [S.BTN_REMOVE, S.BTN_CANCEL]) != 0:
            return False
        return self.undo(pid, request=request)

    # ── 도우미가 넣은 것 모두 빼기 ─────────────────────────────────────

    def remove_all(self) -> bool:
        if self.jobs.busy or self.w.closing:
            return False
        bridge = self.w.bridge
        started = self.jobs.start("scan", lambda ctx: scan_ours(self.make_ops(bridge)), self._scanned,
                                  lambda exc: self._remove_failed("scan", exc), None, cancellable=False)
        if started:
            self._progress(None, S.SCANNING, None, False)
        return started

    def _scanned(self, scan) -> None:
        if self.w.closing:
            return
        if scan.total == 0:
            self._say(S.REMOVE_ALL_NOTHING)
            self._record({"stage": "remove_all", "status": "nothing", "page": scan.page})
            return
        text = S.REMOVE_ALL_CONFIRM.format(n=scan.total) + "\n" + S.REMOVE_ALL_DETAIL.format(
            markers=scan.markers, legacy=scan.legacy_markers, tracks=len(scan.legacy_tracks))
        switch_page, remove_track = False, True
        if scan.needs_edit_page:
            answer = self.w.choose(S.EDIT_PAGE_QUESTION, text + "\n" + S.EDIT_PAGE_DETAIL,
                                   [S.BTN_SWITCH_REMOVE, S.BTN_MARKERS_ONLY, S.BTN_CANCEL])
            if answer == 0:
                switch_page = True
            elif answer == 1:
                remove_track = False
            else:
                self._record({"stage": "remove_all", "status": "cancelled", "page": scan.page, "total": scan.total})
                return
        elif self.w.choose(S.REMOVE_ALL_TITLE, text, [S.BTN_REMOVE, S.BTN_CANCEL]) != 0:
            self._record({"stage": "remove_all", "status": "cancelled", "page": scan.page, "total": scan.total})
            return
        bridge, root = self.w.bridge, self.w.state_root

        def job(ctx):
            return remove_all_ours(self.make_ops(bridge), scan, root=root, remove_track=remove_track,
                                   switch_page=switch_page)

        if self.jobs.start("remove_all", job, lambda out: self._removed(scan, out, switch_page, remove_track),
                           lambda exc: self._remove_failed("remove_all", exc), None, cancellable=False):
            self._progress(None, S.UNDOING, None, False)

    def _removed(self, scan, out, switch_page: bool, remove_track: bool) -> None:
        if self.w.closing:
            return
        self._record({"stage": "remove_all", "status": "other_timeline" if out.other_timeline else "done",
                      "page": scan.page, "found": {"markers": scan.markers, "legacy": scan.legacy_markers,
                                                   "tracks": scan.legacy_tracks},
                      "markers_deleted": out.markers_deleted, "markers_left": out.markers_left,
                      "legacy_deleted": out.legacy_deleted, "track": _jsonable(out.track),
                      "track_skipped": out.track_skipped, "switch_page": switch_page, "remove_track": remove_track,
                      "journal_undone": out.journal_undone})
        if out.other_timeline:
            self._say(S.REMOVE_ALL_OTHER)
            return
        lines = [S.REMOVE_ALL_DONE.format(markers=out.markers_deleted, legacy=out.legacy_deleted)]
        if out.markers_left:
            lines.append(S.REMOVE_ALL_LEFT.format(n=out.markers_left))
        if scan.legacy_tracks:
            lines.append(_track_line(out))
        for line in lines:
            self.w.chat.add_helper(line)
        self.w.show_message(lines[0])
        at = time.strftime("%H:%M")
        for pid in out.journal_undone:
            card = self.by_pid.get(pid)
            if card is not None and card.state == "receipt":
                card.state = "undone"
                card.close_card(S.SLOT_RECEIPT_UNDONE.format(at=at))
                self._receipt(card.proposal.slot, S.SLOT_RECEIPT_UNDONE.format(at=at))
        self.w.refresh_undo(force=True)

    def _remove_failed(self, stage: str, exc: BaseException) -> None:
        if self.w.closing:
            return
        self._say(S.UNDO_FAILED.format(reason=self._explain(exc)))
        self._record({"stage": stage, "status": "error", "error": f"{type(exc).__name__}: {exc}"})

    # ── 확인 질문 M2, M3 (설계 B7.3) ────────────────────────────────────

    def _manual_answered(self, key: str) -> bool:
        if key in self.w.session.manual:
            return True
        caps = self.w.caps
        manual = getattr(caps, "manual", None)
        return isinstance(manual, dict) and key in manual

    def ask_manual(self, key: str, *, again: bool = False) -> Optional[QuestionCard]:
        """한 번만 자동으로 띄운다 (again=True면 연결 점검의 [확인 질문 다시 보기])."""
        old = self.manual_cards.get(key)
        if not again and (old is not None or self._manual_answered(key)):
            return None
        if old is not None:
            try:
                if old.buttons:
                    old.answered(S.CARD_SUPERSEDED)
            except RuntimeError:
                pass
        if key == "M3":
            answers = [(k, label, False) for k, label in S.M3_ANSWERS.items()]
            card = QuestionCard(S.M3_QUESTION, [S.M3_STEP], answers + [("later", S.BTN_LATER, False)])
        else:
            answers = [(k, label, False) for k, label in S.M2_ANSWERS.items()]
            card = QuestionCard(S.M2_QUESTION, [S.M2_NOTE], answers + [("later", S.BTN_LATER, False)])
        card.clicked.connect(lambda answer, c=card: self._manual_answer(key, c, answer))
        self.manual_cards[key] = card
        self._add_card(card)
        return card

    def _manual_answer(self, key: str, card: QuestionCard, answer: str) -> None:
        if answer == "later":
            card.answered(S.MANUAL_LATER)
            return
        labels = S.M3_ANSWERS if key == "M3" else S.M2_ANSWERS
        card.answered(S.MANUAL_ANSWERED.format(answer=labels.get(answer, answer)))
        record: Dict[str, Any] = {"answer": answer, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        self.w.session.manual[key] = record
        self._save_manual(key, record)
        if key == "M3":
            if answer == "other":
                self.w.footer.set_entries(self.w.footer.entries, warn_ctrl_z=True)
            self._recount(record)

    def _save_manual(self, key: str, record: Dict[str, Any]) -> None:
        ping = self.w.controller.info.get("ping")
        if not isinstance(ping, dict):
            return
        try:
            self.w.caps = self.w.caps_store.record_manual(
                ping.get("product"), ping.get("product_version") or ping.get("resolve_version"),
                ping.get("script_version"), key, dict(record))
        except OSError:
            pass

    def _recount(self, record: Dict[str, Any]) -> None:
        """M3 답 뒤: 도우미 표시를 다시 읽어 넣은 수와 찾은 수를 적는다 (누른 뒤의 읽기 한 번)."""
        expected = 0
        key = self.w.journal_key
        if key:
            try:
                for e in Journal(self.w.state_root, key).entries:
                    if e.get("status") in ("applied", "partial"):
                        expected += len((e.get("created") or {}).get("markers") or [])
            except OSError:
                pass
        record["expected"] = expected

        def step(bridge, out):
            out["found"] = self.make_ops(bridge).marker_total(OUR_PREFIX)

        def done(out):
            found = out.get("found")
            record["found"] = found
            self._save_manual("M3", record)
            if isinstance(found, int):
                self.w.chat.add_helper(S.MANUAL_COUNTS.format(found=found, expected=expected))

        def failed(exc):
            record["found_error"] = steps.explain(getattr(exc, "cause", exc), answered=True)
            self._save_manual("M3", record)

        self.w.controller.submit("manual", step, done, failed)


def _track_line(out) -> str:
    if out.track_skipped == "markers_only":
        return S.REMOVE_ALL_TRACK_KEPT
    if out.track_skipped == "need_edit_page":
        return S.REMOVE_ALL_TRACK_PAGE
    if out.track_skipped:
        return S.REMOVE_ALL_TRACK_FAILED.format(reason=out.track_skipped)
    t = out.track or {}
    if int(t.get("removed_tracks") or 0) > 0 and not int(t.get("skipped") or 0):
        return S.REMOVE_ALL_TRACK_DONE
    results = t.get("delete_track") or []
    if any(isinstance(r, dict) and not _ok(r.get("result")) for r in results):
        return S.REMOVE_ALL_TRACK_LEFT
    if int(t.get("removed_tracks") or 0) > 0:
        return S.REMOVE_ALL_TRACK_DONE
    return S.REMOVE_ALL_TRACK_NOT_OURS


def _ok(result: Any) -> bool:
    """Lua call_result: true / false / "error" / null. 확실히 된 것만 됨으로 센다."""
    return result is True


def _jsonable(value: Any) -> Any:
    """결과 파일에 적을 수 있는 모양으로 (dataclass·튜플·숫자 열쇠)."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "__dict__"):
        return _jsonable(vars(value))
    return str(value)
