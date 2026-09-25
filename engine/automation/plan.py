"""자동화 버튼 계산 (설계 B2.4, B2.5 1~3): 리졸브에서 읽기만 하고 제안(카드)을 만든다.

1. 소리 꺼내는 중: 타임라인 정보·소리 클립 읽기, 파일마다 ffprobe, 목소리 스트림 정하기
   (처음 보는 녹화 모양이면 VoiceQuestion을 돌려주고 끝: 화면이 "어느 소리가 내 목소리인가요?"를 묻는다)
2. 크기 재는 중: 목소리 스트림의 음량 (analysis_cache, 한 번 잰 파일은 바로)
3. 표시할 곳 고르는 중: 원본에서 찾은 구간 → 클립을 거쳐 타임라인 프레임 → 요청 범위로 자르기

리졸브에 바꾸는 요청은 하나도 보내지 않는다 (읽기: timeline_info, timeline_items, 필요할 때 probe_read, scope).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

from .. import ffmpeg
from ..analysis_cache import AnalysisCache, StreamAnalysis
from ..edits.journal import key_for
from ..edits.proposal import (MarkerRow, Proposal, VoiceInfo, build_specs, new_proposal_id, pause_name,
                              spike_name)
from ..loudness import find_spikes, quiet_runs
from ..probe import MediaInfo, probe
from ..resolve_link.bridge import BridgeCancelled, BridgeError, BridgeTimeout
from ..resolve_link.ops import Item, ResolveOps
from ..timeline.audio_map import (StreamAssignment, assign_streams, best_speech_moment, detect_mix,
                                  doubled_voice, layout_signature, profile_change, stream_channels,
                                  track_streams_from_probe)
from ..timeline.map import (Piece, clip_pieces, exact_fps, item_windows, map_regions, merge_pieces,
                            pad_pieces)
from ..timeline.snapshot import TimelineSnapshot
from .kinds import MAX_MARKERS_PER_PROPOSAL, kind as get_kind

STAGES = ("extract", "measure", "pick")  # 소리 꺼내는 중 → 크기 재는 중 → 표시할 곳 고르는 중

Progress = Callable[[int, float, Dict[str, Any]], None]


class PlanRefused(Exception):
    """계산할 수 없음 (code는 화면 글의 열쇠: no_timeline, probe_copy, no_audio_items ...)."""

    def __init__(self, code: str, **detail: Any) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass
class StreamChoice:
    index: int
    channels: int
    title: str
    codec: str
    start: float
    best_start: float  # 3초 듣기에 쓸 곳 (원본 초)
    profile: Dict[str, Any]
    enabled_on_timeline: bool  # 이 스트림이 켜진 트랙에 있는지


@dataclass
class VoiceQuestion:
    """목소리 고르기 카드에 필요한 것."""

    signature: str
    path: str
    streams: List[StreamChoice]
    mix: Optional[int]
    doubled: bool
    reason: str  # first / changed / change
    previous: Optional[int] = None
    change_reason: Optional[str] = None  # profile_change의 이유
    debug: Dict[str, Any] = field(default_factory=dict)


@dataclass
class VoiceOverride:
    signature: str
    stream: int


@dataclass
class VoiceMemory:
    """설정의 voice (작업 스레드에는 복사본을 넘긴다)."""

    by_layout: Dict[str, Any] = field(default_factory=dict)
    profiles: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_settings(cls, voice: Dict[str, Any]) -> "VoiceMemory":
        import copy

        return cls(copy.deepcopy(voice.get("by_layout") or {}), copy.deepcopy(voice.get("profiles") or {}))

    def choice(self, sig: str) -> Optional[Dict[str, Any]]:
        c = self.by_layout.get(sig)
        return c if isinstance(c, dict) and isinstance(c.get("stream"), int) else None


@dataclass
class SlotRequest:
    slot: Optional[int]
    kind: str
    name: str
    params: Dict[str, Any]
    origin: str = ""


@dataclass
class PlanEnv:
    ops: ResolveOps
    cache: AnalysisCache
    caps: Any = None  # Capabilities 또는 None
    probe_file: Callable[[str], MediaInfo] = probe
    probe_reads: Dict[str, Any] = field(default_factory=dict)  # 타임라인 열쇠 → probe_read 답 (이번 실행 동안)
    cancel: Optional[threading.Event] = None
    progress: Optional[Progress] = None
    exists: Callable[[str], bool] = None  # 파일이 이 PC에 있는지 (시험에서 바꾼다)

    def __post_init__(self) -> None:
        if self.exists is None:
            import os

            self.exists = os.path.isfile

    def cancelled(self) -> bool:
        return self.cancel is not None and self.cancel.is_set()

    def check(self) -> None:
        if self.cancelled():
            raise ffmpeg.Cancelled()

    def report(self, stage: int, frac: float, **info: Any) -> None:
        if self.progress is not None:
            self.progress(stage, max(0.0, min(1.0, frac)), info)


def _allowed(caps: Any, cap: str) -> Optional[bool]:
    if caps is None:
        return None
    try:
        return caps.has(cap)
    except (KeyError, AttributeError):
        return None


def _item_key(item: Item) -> str:
    return item.uid or f"{item.track}:{item.start}:{item.path}"


# ── 범위 (전체 / In~Out) ───────────────────────────────────────────────

def _in_out_frames(in_out: Optional[Dict[str, Any]], start: int, end: Optional[int]) -> Optional[Tuple[int, int]]:
    """GetMarkInOut 답 → 절대 프레임 [lo, hi). 오디오 쪽이 있으면 그것, 없으면 영상 쪽.

    리졸브가 주는 값이 타임라인 시작 기준인지 절대인지 확인되지 않아서, 시작 프레임보다 작으면
    시작 기준으로 본다 (결과 파일에 원래 값이 남는다). Out은 들어가는 프레임으로 본다.
    """
    if not isinstance(in_out, dict):
        return None
    for side in ("audio", "video"):
        v = in_out.get(side)
        if not isinstance(v, dict):
            continue
        i, o = v.get("in"), v.get("out")
        if not isinstance(i, (int, float)) or not isinstance(o, (int, float)):
            continue
        lo, hi = int(i), int(o) + 1
        if lo < start:
            lo, hi = lo + start, hi + start
        if end is not None:
            hi = min(hi, end)
        lo = max(lo, start)
        if hi > lo:
            return lo, hi
    return None


# ── 계산 ──────────────────────────────────────────────────────────────

def read_snapshot(env: PlanEnv) -> TimelineSnapshot:
    info = env.ops.timeline_info(cancel=env.cancel)
    if not info.has_project:
        raise PlanRefused("no_project")
    if not info.has_timeline:
        raise PlanRefused("no_timeline")
    if info.is_probe_copy:
        raise PlanRefused("probe_copy")
    if exact_fps(info.fps) is None or info.start_frame is None or info.end_frame is None:
        raise PlanRefused("timeline_unreadable")
    env.check()
    items = env.ops.timeline_items("audio", cancel=env.cancel)
    return TimelineSnapshot(info, items)


def _probe_files(env: PlanEnv, items: Sequence[Item]) -> Tuple[Dict[str, MediaInfo], List[str]]:
    media: Dict[str, MediaInfo] = {}
    missing: List[str] = []
    for path in sorted({it.path for it in items if it.path}):
        env.check()
        if not env.exists(path):
            missing.append(path)
            continue
        try:
            media[path] = env.probe_file(path)
        except (ffmpeg.FFmpegError, OSError, ValueError):
            missing.append(path)
    return media, missing


def _decodable(m: MediaInfo) -> List[int]:
    return [t.index for t in m.audio_tracks if t.decodable]


def _track_mapping(env: PlanEnv, snap: TimelineSnapshot, items: Sequence[Item],
                   media: Dict[str, MediaInfo]) -> Tuple[Dict[int, Tuple[str, int]], Optional[Dict[str, Any]]]:
    """여러 스트림 파일이 있을 때만: 리졸브의 연결 정보(probe_read)로 트랙 → 스트림. 이 실행에서 한 번만 읽는다."""
    if not any(len(m.audio_tracks) >= 2 for m in media.values()):
        return {}, None
    supports = env.ops.supports("probe_read")
    if supports is False:
        return {}, None
    key = snap.key
    if key not in env.probe_reads:
        try:
            env.probe_reads[key] = env.ops.probe_read(cancel=env.cancel)
        except (BridgeError, BridgeTimeout):
            env.probe_reads[key] = None
    result = env.probe_reads.get(key)
    channels = {p: stream_channels(m) for p, m in media.items()}
    return track_streams_from_probe(result, items, channels), result


def _question(env: PlanEnv, req: SlotRequest, path: str, m: MediaInfo, sig: str, reason: str,
              enabled_streams: Sequence[int], previous: Optional[int], change_reason: Optional[str] = None,
              known: Optional[Dict[int, StreamAnalysis]] = None) -> VoiceQuestion:
    """모든 스트림을 재서 (전체 소리 찾기와 3초 듣기 자리) 목소리 고르기 카드를 만든다."""
    streams = _decodable(m)
    analyses: Dict[int, StreamAnalysis] = dict(known or {})
    todo = [s for s in streams if s not in analyses]
    for n, s in enumerate(todo):
        env.check()

        def prog(f: float, n=n) -> None:
            env.report(2, (n + f) / max(1, len(todo)), media_s=m.duration)

        analyses[s], _ = env.cache.get_or_analyze(path, s, m, progress=prog, is_cancelled=env.cancelled)
    mix = detect_mix({s: analyses[s] for s in streams})
    on = set(enabled_streams)
    choices = []
    for t in m.audio_tracks:
        if t.index not in analyses:
            continue
        a = analyses[t.index]
        choices.append(StreamChoice(index=t.index, channels=t.channels, title=t.title, codec=t.codec, start=t.start,
                                    best_start=best_speech_moment(a), profile=a.profile(),
                                    enabled_on_timeline=t.index in on))
    return VoiceQuestion(
        signature=sig, path=path, streams=choices, mix=mix.stream, doubled=doubled_voice(mix.stream, None, on),
        reason=reason, previous=previous, change_reason=change_reason,
        debug={"mix_shares": {str(k): round(v, 3) for k, v in mix.shares.items()}, "mix_window": mix.window},
    )


def plan_slot(req: SlotRequest, env: PlanEnv, voice: VoiceMemory, *, override: Optional[VoiceOverride] = None,
              ask_voice: bool = False, proposal_id: Optional[str] = None) -> Union[Proposal, VoiceQuestion]:
    """[멈추기]로 리졸브의 답을 기다리다 그만둔 것도 ffmpeg.Cancelled로 알린다 (화면은 "멈췄어요")."""
    try:
        return _plan_slot(req, env, voice, override=override, ask_voice=ask_voice, proposal_id=proposal_id)
    except BridgeCancelled:
        if env.cancelled():
            raise ffmpeg.Cancelled() from None
        raise


def _plan_slot(req: SlotRequest, env: PlanEnv, voice: VoiceMemory, *, override: Optional[VoiceOverride],
               ask_voice: bool, proposal_id: Optional[str]) -> Union[Proposal, VoiceQuestion]:
    k = get_kind(req.kind)
    if k is None or not k.ready:
        raise PlanRefused("not_ready")
    params = k.normalize(req.params)
    debug: Dict[str, Any] = {}
    warnings: Dict[str, int] = {}

    # 1. 소리 꺼내는 중
    env.report(1, 0.0)
    snap = read_snapshot(env)
    fps, tl_start, tl_end = float(snap.fps), snap.start, snap.end
    on_tracks = snap.enabled_tracks("audio")
    usable = [it for it in snap.items if it.enabled is not False and it.track in on_tracks and it.path]
    if not usable:
        raise PlanRefused("no_audio_items")

    lo, hi = tl_start, tl_end
    scope = {"kind": "whole", "lo": None, "hi": None}
    if params.get("scope") == "in_out":
        if _allowed(env.caps, "in_out") is not True:
            warnings["in_out_off"] = 1
            params["scope"] = "whole"
        else:
            sc = env.ops.scope(cancel=env.cancel)
            frames = _in_out_frames(sc.in_out, tl_start, tl_end)
            debug["in_out_raw"] = sc.in_out
            if frames is None:
                raise PlanRefused("no_in_out")
            lo, hi = frames
            scope = {"kind": "in_out", "lo": lo, "hi": hi}
    env.check()

    media, missing = _probe_files(env, usable)
    if missing:
        warnings["missing_files"] = len(missing)
        debug["missing_files"] = missing
    if not media:
        raise PlanRefused("files_missing", paths=missing)
    covered: Dict[str, int] = {}
    for it in usable:
        if it.path in media:
            covered[it.path] = covered.get(it.path, 0) + max(0, (it.end or 0) - (it.start or 0))
    primary = max(covered, key=lambda p: (covered[p], p))
    pm = media[primary]
    streams = _decodable(pm)
    if not streams:
        raise PlanRefused("no_streams")
    env.report(1, 0.5)

    # 스트림 순서 규칙은 꺼 둔 트랙의 클립까지 함께 센다 (전체 소리 트랙 A1만 꺼도 A2..A4가 몇 번째인지 안다)
    placed = [it for it in snap.items if it.path in media]
    track_map, probe_result = _track_mapping(env, snap, placed, media)
    if probe_result is not None:
        rows = probe_result.get("source_audio_mapping") if isinstance(probe_result, dict) else None
        debug["mapping_json"] = rows
    counts = {p: len(m.audio_tracks) for p, m in media.items()}
    assignment: StreamAssignment = assign_streams(placed, counts, track_map)
    debug["mapping_methods"] = assignment.counts()
    debug["mapping_conflicts"] = assignment.conflicts
    enabled_streams = sorted({assignment.stream_of[_item_key(it)] for it in usable
                              if it.path == primary and _item_key(it) in assignment.stream_of})

    sig = layout_signature(pm)
    remembered = voice.choice(sig) if len(pm.audio_tracks) >= 2 else None
    chosen: Optional[int] = None
    just_chosen = False
    mix = remembered.get("mix") if remembered else None
    if len(pm.audio_tracks) >= 2:
        if override is not None and override.signature == sig and override.stream in streams:
            chosen, just_chosen = override.stream, True
        elif ask_voice or remembered is None or remembered["stream"] not in streams:
            env.report(1, 1.0)
            return _question(env, req, primary, pm, sig, "change" if ask_voice and remembered else "first",
                             enabled_streams, remembered["stream"] if remembered else None)
        else:
            chosen = int(remembered["stream"])
    else:
        chosen = streams[0]
    env.report(1, 1.0)

    # 목소리 스트림: 파일마다 (녹화 모양이 같으면 같은 스트림, 다르면 그 모양에 고른 것, 없으면 뺀다)
    voice_of: Dict[str, int] = {primary: chosen}
    for p, m in media.items():
        if p == primary:
            continue
        if len(m.audio_tracks) == 1:
            voice_of[p] = 0
            continue
        s2 = layout_signature(m)
        c2 = voice.choice(s2)
        if s2 == sig:
            voice_of[p] = chosen
        elif c2 is not None:
            voice_of[p] = int(c2["stream"])
        else:
            warnings["other_layout"] = warnings.get("other_layout", 0) + 1
    usable_keys = {_item_key(it) for it in usable}
    unknown = [it for it in assignment.unknown if _item_key(it) in usable_keys and it.path in voice_of
               and counts.get(it.path, 0) >= 2]
    if unknown:
        warnings["unmapped"] = len(unknown)
    voice_items = [it for it in usable if it.path in voice_of
                   and assignment.stream_of.get(_item_key(it)) == voice_of[it.path]]
    if not voice_items:
        raise PlanRefused("no_voice_items", stream=chosen)

    # 2. 크기 재는 중
    analyses: Dict[str, StreamAnalysis] = {}
    todo = sorted({it.path for it in voice_items})
    analyzed_s, elapsed_s = 0.0, 0.0
    for n, p in enumerate(todo):
        env.check()

        def prog(f: float, n=n) -> None:
            env.report(2, (n + f) / max(1, len(todo)), media_s=media[p].duration)

        a, hit = env.cache.get_or_analyze(p, voice_of[p], media[p], progress=prog, is_cancelled=env.cancelled)
        analyses[p] = a
        if not hit:
            analyzed_s += a.duration
            elapsed_s += a.elapsed_s
    debug["perf"] = {"analyzed_s": round(analyzed_s, 2), "elapsed_s": round(elapsed_s, 3),
                     "cache_hits": env.cache.hits, "cache_misses": env.cache.misses}
    pa = analyses[primary] if primary in analyses else next(iter(analyses.values()))
    profile = pa.profile()
    if len(pm.audio_tracks) >= 2 and not just_chosen:
        change = profile_change(voice.profiles.get(sig), profile)
        if change is not None:
            return _question(env, req, primary, pm, sig, "changed", enabled_streams, chosen, change_reason=change,
                             known={chosen: pa} if primary in analyses else None)
    env.report(2, 1.0)

    # 3. 표시할 곳 고르는 중
    env.report(3, 0.0)
    check = item_windows(voice_items, fps)
    if check.refused_speed:
        warnings["speed"] = len(check.refused_speed)
    if check.unmappable:
        warnings["unreadable_items"] = len(check.unmappable)
    if check.offset_mismatch:
        warnings["offset_mismatch"] = check.offset_mismatch
    if check.speed_unchecked:
        debug["speed_unchecked"] = check.speed_unchecked
    by_path: Dict[str, List] = {}
    for w in check.windows:
        by_path.setdefault(w.item.path, []).append(w)

    pid = proposal_id or new_proposal_id()
    if req.kind == "mark_pauses":
        rows, found = _pause_rows(params, analyses, by_path, fps, lo, hi)
    else:
        rows, found = _spike_rows(params, analyses, by_path, fps, lo, hi)
    cap = min(int(params.get("max") or MAX_MARKERS_PER_PROPOSAL), MAX_MARKERS_PER_PROPOSAL)
    if len(rows) > cap:
        warnings["too_many"] = len(rows)
        keep = sorted(rows, key=lambda r: -r.value)[:cap]
        rows = sorted(keep, key=lambda r: r.start)
    point = (req.kind == "mark_pauses" and not params.get("as_range", True)) or \
        _allowed(env.caps, "range_markers") is False
    color = params.get("color") or ("Blue" if req.kind == "mark_pauses" else "Red")
    note_for = (lambda r: f"쉰 길이 {r.value:.1f}초") if req.kind == "mark_pauses" else \
        (lambda r: f"평소 말소리보다 +{r.value:.1f}dB")
    specs = build_specs(pid, rows, tl_start, color, point=point, note_for=note_for)
    env.report(3, 1.0)

    voice_info = None
    if len(pm.audio_tracks) >= 2:
        voice_info = VoiceInfo(stream=chosen, stream_count=len(pm.audio_tracks), signature=sig,
                               remembered=not just_chosen, mix=mix,
                               doubled=doubled_voice(mix, chosen, enabled_streams), path=primary, profile=profile)
    # 얼마나: 쉼은 쉰 길이의 합, 튀는 소리는 표시 구간 길이의 합
    total = sum(r.value for r in rows) if req.kind == "mark_pauses" else sum((r.end - r.start) / fps for r in rows)
    debug["typical"] = {p: round(a.typical, 1) for p, a in analyses.items()}
    return Proposal(
        id=pid, kind=req.kind, slot=req.slot, origin=req.origin or (f"button:{req.slot}" if req.slot else ""),
        request=req.name, params=params, rows=rows, specs=specs, timeline=snap.timeline_record(), fps=fps,
        tl_start=tl_start, tl_end=tl_end, fingerprint=snap.fingerprint(), scope=scope, voice=voice_info,
        tracks=sorted({w.item.track for w in check.windows}), warnings=warnings, total_s=round(total, 3),
        found=found, point_only=point, debug=debug,
    )


def _range_trimmed(before: Piece, after: Piece) -> bool:
    return after.start != before.start or after.end != before.end


def _pause_rows(params: Dict[str, Any], analyses: Dict[str, StreamAnalysis], by_path: Dict[str, List],
                fps: float, lo: int, hi: Optional[int]) -> Tuple[List[MarkerRow], int]:
    min_s, pad_s = float(params["min_s"]), float(params["pad_s"])
    pieces: List[Piece] = []
    for p, windows in by_path.items():
        a = analyses.get(p)
        if a is None:
            continue
        runs = quiet_runs(a.momentary, a.typical, float(params["below_lu"]))
        pieces += map_regions(runs, windows)
    merged = [q for q in merge_pieces(pieces, 1) if q.raw_frames / fps + 1e-9 >= min_s]
    padded = pad_pieces(merged, int(round(pad_s * fps)))
    rows: List[MarkerRow] = []
    for q in padded:
        cut = clip_pieces([q], lo, hi)
        if not cut:
            continue
        c = cut[0]
        if _range_trimmed(q, c) and c.frames / fps + 1e-9 < min_s:
            continue  # 요청 범위에 걸쳐 잘린 조각이 min_s보다 짧으면 뺀다 (설계 B2.4 5)
        seconds = c.raw_frames / fps
        rows.append(MarkerRow(c.start, c.end, round(seconds, 3), pause_name(params.get("name") or "쉼", seconds), ""))
    return rows, len(rows)


def _spike_rows(params: Dict[str, Any], analyses: Dict[str, StreamAnalysis], by_path: Dict[str, List],
                fps: float, lo: int, hi: Optional[int]) -> Tuple[List[MarkerRow], int]:
    above, merge_s = float(params["above_lu"]), float(params["merge_s"])
    pieces: List[Piece] = []
    typical_of: List[float] = []
    for p, windows in by_path.items():
        a = analyses.get(p)
        if a is None:
            continue
        regions = find_spikes(a.momentary, a.typical, above, merge_s)
        # level을 "말소리보다 몇 dB 큰지"로 바꿔 둔다 (파일마다 보통 크기가 다를 수 있어서)
        for r in regions:
            r.level = r.level - a.typical
        pieces += map_regions(regions, windows)
        typical_of.append(a.typical)
    merged = merge_pieces(pieces, int(round(merge_s * fps)))
    rows = [MarkerRow(c.start, c.end, round(c.level, 2), spike_name(c.level), "")
            for c in clip_pieces(merged, lo, hi) if c.frames >= 1]
    return rows, len(rows)


def plan_duration_s(media: Dict[str, MediaInfo]) -> float:
    return sum(m.duration for m in media.values())
