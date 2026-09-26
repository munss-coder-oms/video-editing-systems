"""소리 스트림 ↔ 타임라인 트랙, 목소리 고르기 도움 (설계 B2.3).

OBS 녹화 하나에는 소리 스트림이 여러 개 있다 (예: 1 전체, 2 마이크, 3 게임, 4 음악).
리졸브에 넣으면 A1~A4에 하나씩 들어간다. 목소리가 어느 스트림인지는 사용자가 한 번 고르고
(녹화 모양 = layout_signature마다 기억), 어느 트랙이 그 스트림인지는 아래 순서로 정한다.

1. 리졸브의 연결 정보(GetSourceAudioChannelMapping, probe_read가 읽음): channel_idx는 스트림을
   이어서 센 채널 번호다. ffprobe의 스트림별 채널 수로 어느 스트림인지 안다.
2. 순서 규칙: 같은 파일·같은 자리(start, end)의 소리 클립이 스트림 수와 같으면 n번째 트랙 = 스트림 n−1.
3. 같은 트랙의 같은 파일 클립 가운데 이미 정해진 것을 따른다.
4. 그래도 모르면 빼고 카드에 알린다.

"전체 소리" 찾기(detect_mix): 한 스트림의 크기가 나머지를 모두 더한 크기와 ±1dB 안에서 같으면
(말소리가 있는 60초의 0.1초 가운데 80% 넘게) 섞은 것으로 본다. 추정일 뿐이고, 사용자가 들어 보고 고른다.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .. import ffmpeg
from ..analysis_cache import StreamAnalysis
from ..loudness import SILENCE
from ..probe import MediaInfo
from ..resolve_link.ops import Item

MIX_WINDOW_S = 60.0
MIX_TOLERANCE_DB = 1.0
MIX_SHARE = 0.8
MIX_MIN_FRAMES = 30  # 소리 나는 0.1초가 이보다 적으면 판단하지 않는다
LISTEN_SECONDS = 3.0
PROFILE_TYPICAL_LU = 10.0
PROFILE_ACTIVE = 0.5
NEAR_SILENT_LUFS = -50.0


# ── 녹화 모양 ─────────────────────────────────────────────────────────

def stream_channels(media: MediaInfo) -> List[int]:
    return [max(0, int(t.channels or 0)) for t in media.audio_tracks]


def layout_signature(media: MediaInfo) -> str:
    """"a4:2,2,2,2:마이크|게임||" 꼴. 스트림 수, 스트림별 채널 수, 스트림 이름표(OBS가 붙인 것)."""
    tracks = media.audio_tracks
    chans = ",".join(str(int(t.channels or 0)) for t in tracks)
    titles = "|".join((t.title or "").replace("|", "/") for t in tracks)
    return f"a{len(tracks)}:{chans}:{titles}"


# ── 트랙 → 스트림 ─────────────────────────────────────────────────────

def stream_from_mapping(text: Any, channels: Sequence[int]) -> Optional[int]:
    """GetSourceAudioChannelMapping JSON 글 → 스트림 번호 (0부터). 여러 스트림에 걸치거나 모르면 None.

    channel_idx는 1부터, 스트림을 이어서 센다 (스트림 0의 채널 2개가 1·2, 스트림 1이 3·4 ...).
    mute:true인 줄은 뺀다.
    """
    if not isinstance(text, str) or not text.strip() or not channels:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    mapping = data.get("track_mapping") if isinstance(data, dict) else None
    if not isinstance(mapping, dict):
        return None
    bounds: List[Tuple[int, int]] = []  # 스트림마다 (첫 채널, 끝 채널) 1부터
    first = 1
    for n in channels:
        bounds.append((first, first + n - 1))
        first += n
    found: Set[int] = set()
    for entry in mapping.values():
        if not isinstance(entry, dict) or entry.get("mute") is True:
            continue
        idx = entry.get("channel_idx")
        if not isinstance(idx, list):
            continue
        for c in idx:
            if isinstance(c, bool) or not isinstance(c, (int, float)):
                return None
            c = int(c)
            hit = [s for s, (lo, hi) in enumerate(bounds) if lo <= c <= hi]
            if not hit:
                return None
            found.add(hit[0])
    return found.pop() if len(found) == 1 else None


def track_streams_from_probe(probe_read: Optional[Dict[str, Any]], items: Sequence[Item],
                             channels_by_path: Dict[str, List[int]]) -> Dict[int, Tuple[str, int]]:
    """probe_read의 트랙별 첫 클립 연결 정보 → {트랙: (그 클립의 파일, 스트림)}."""
    out: Dict[int, Tuple[str, int]] = {}
    rows = (probe_read or {}).get("source_audio_mapping")
    if not isinstance(rows, list):
        return out
    first_on_track: Dict[int, Item] = {}
    for it in items:
        if it.start is None:
            continue
        cur = first_on_track.get(it.track)
        if cur is None or (cur.start is not None and it.start < cur.start):
            first_on_track[it.track] = it
    for row in rows:
        if not isinstance(row, dict) or row.get("truncated"):
            continue
        track = row.get("track")
        if not isinstance(track, int) or track not in first_on_track:
            continue
        path = first_on_track[track].path
        chans = channels_by_path.get(path or "")
        if not path or not chans:
            continue
        stream = stream_from_mapping(row.get("mapping"), chans)
        if stream is not None:
            out[track] = (path, stream)
    return out


@dataclass
class StreamAssignment:
    stream_of: Dict[str, int] = field(default_factory=dict)  # 클립 uid → 스트림
    method: Dict[str, str] = field(default_factory=dict)  # 클립 uid → mapping / ordinal / track / single
    unknown: List[Item] = field(default_factory=list)
    conflicts: int = 0  # 연결 정보와 순서 규칙이 서로 다른 클립 수 (결과 파일용)

    def counts(self) -> Dict[str, int]:
        return dict(Counter(self.method.values()))


def _key(item: Item) -> str:
    return item.uid or f"{item.track}:{item.start}:{item.path}"


def assign_streams(items: Sequence[Item], streams_by_path: Dict[str, int],
                   track_mapping: Optional[Dict[int, Tuple[str, int]]] = None) -> StreamAssignment:
    """클립마다 원본 파일의 몇 번째 소리 스트림인지 정한다 (위 순서)."""
    track_mapping = track_mapping or {}
    out = StreamAssignment()
    groups: Dict[Tuple[str, Any, Any], List[Item]] = defaultdict(list)
    for it in items:
        if it.path:
            groups[(it.path, it.start, it.end)].append(it)
    ordinal: Dict[str, int] = {}
    for (path, _, _), group in groups.items():
        n = streams_by_path.get(path, 0)
        tracks = sorted({g.track for g in group})
        if n >= 2 and len(group) == n and len(tracks) == n:
            for g in group:
                ordinal[_key(g)] = tracks.index(g.track)
    pending: List[Item] = []
    for it in items:
        k = _key(it)
        n = streams_by_path.get(it.path or "", 0)
        if n == 1:
            out.stream_of[k], out.method[k] = 0, "single"
            continue
        mapped = track_mapping.get(it.track)
        if mapped is not None and mapped[0] == it.path and 0 <= mapped[1] < n:
            out.stream_of[k], out.method[k] = mapped[1], "mapping"
            if k in ordinal and ordinal[k] != mapped[1]:
                out.conflicts += 1
            continue
        if k in ordinal:
            out.stream_of[k], out.method[k] = ordinal[k], "ordinal"
            continue
        pending.append(it)
    for it in pending:
        k = _key(it)
        same = [out.stream_of[_key(o)] for o in items
                if o.track == it.track and o.path == it.path and _key(o) in out.stream_of]
        if same and it.path in streams_by_path and streams_by_path[it.path] >= 1:
            (stream, _), = Counter(same).most_common(1)
            out.stream_of[k], out.method[k] = stream, "track"
        else:
            out.unknown.append(it)
    return out


# ── 전체 소리(섞은 스트림) 찾기 ─────────────────────────────────────────

@dataclass
class MixResult:
    stream: Optional[int]  # 섞은 것으로 보이는 스트림 (하나일 때만), 없으면 None
    shares: Dict[int, float] = field(default_factory=dict)  # 스트림마다 ±1dB 안에 든 비율
    frames: Dict[int, int] = field(default_factory=dict)  # 스트림마다 본 0.1초 수
    window: Optional[Tuple[float, float]] = None  # 본 60초 (원본 초)


def _power(lufs: float) -> float:
    return 0.0 if lufs <= SILENCE + 1e-6 or lufs < -120 else 10.0 ** (lufs / 10.0)


def detect_mix(analyses: Dict[int, StreamAnalysis], window_s: float = MIX_WINDOW_S,
               tol_db: float = MIX_TOLERANCE_DB, share: float = MIX_SHARE) -> MixResult:
    """한 스트림이 나머지를 모두 섞은 것인지 (크기 합 비교). 딱 하나만 그럴 때만 알려 준다.

    스트림 k의 소리가 있는 0.1초(바닥보다 큰 곳)마다 P_k와 나머지 합 Σ P_j를 견준다. 섞은 스트림은
    P_k ≈ 나머지(원본들)의 합이다. 스트림이 셋 미만이면 판단하지 않는다 (둘이면 서로가 "나머지 합"이다).
    """
    streams = sorted(analyses)
    if len(streams) < 3:
        return MixResult(None)
    n = min(len(analyses[s].m) for s in streams)
    if n == 0:
        return MixResult(None)
    floors = {s: max(analyses[s].presence_floor(), SILENCE + 1.0) for s in streams}
    present = {s: [analyses[s].m[i] > floors[s] for i in range(n)] for s in streams}
    # 스트림 둘 이상에 소리가 있는 0.1초가 가장 많은 60초를 본다
    score = [sum(1 for s in streams if present[s][i]) >= 2 for i in range(n)]
    width = max(1, min(n, int(round(window_s * 10))))
    run = sum(score[:width])
    best, best_i = run, 0
    for i in range(1, n - width + 1):
        run += score[i + width - 1] - score[i - 1]
        if run > best:
            best, best_i = run, i
    lo, hi = best_i, best_i + width
    result = MixResult(None)
    t = analyses[streams[0]].t
    result.window = (t[lo] if lo < len(t) else 0.0, t[hi - 1] if 0 <= hi - 1 < len(t) else 0.0)
    flagged = []
    for k in streams:
        ok = total = 0
        for i in range(lo, hi):
            if not present[k][i]:
                continue
            rest = sum(_power(analyses[j].m[i]) for j in streams if j != k)
            pk = _power(analyses[k].m[i])
            total += 1
            if rest > 0 and pk > 0 and abs(10.0 * math.log10(pk / rest)) <= tol_db:
                ok += 1
        result.frames[k] = total
        result.shares[k] = ok / total if total else 0.0
        if total >= MIX_MIN_FRAMES and ok / total >= share:
            flagged.append(k)
    result.stream = flagged[0] if len(flagged) == 1 else None
    return result


def doubled_voice(mix_stream: Optional[int], voice_stream: Optional[int], enabled_streams: Iterable[int]) -> bool:
    """섞은 스트림과 목소리(모르면 다른 아무 스트림)가 함께 켜져 있으면 목소리가 두 번 들린다."""
    if mix_stream is None:
        return False
    on = set(enabled_streams)
    if mix_stream not in on:
        return False
    if voice_stream is not None and voice_stream != mix_stream:
        return voice_stream in on
    return any(s != mix_stream for s in on)


# ── 3초 듣기 ──────────────────────────────────────────────────────────

def best_speech_moment(analysis: StreamAnalysis, seconds: float = LISTEN_SECONDS) -> float:
    """3초 음량(S)이 가장 큰 3초의 시작 (원본 초). S(t)는 [t−3, t]의 크기다."""
    best_t, best_v = None, None
    for t, v in zip(analysis.t, analysis.s):
        if t < seconds - 1e-6:
            continue
        if best_v is None or v > best_v:
            best_t, best_v = t, v
    if best_t is None:
        return 0.0
    return max(0.0, best_t - seconds)


def excerpt_wav(path: str, stream: int, start: float, out_path: Path, seconds: float = LISTEN_SECONDS,
                media: Optional[MediaInfo] = None) -> Path:
    """스트림 하나의 start초부터 seconds초를 WAV(48kHz, 스테레오, 16비트)로 꺼낸다. 리졸브는 건드리지 않는다."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    offset = media.video_start - media.format_start if media is not None and media.has_video else 0.0
    ffmpeg.run([
        "-y", "-ss", f"{max(0.0, start + offset):.3f}", "-i", str(path), "-map", f"0:a:{int(stream)}",
        "-t", f"{seconds:.3f}", "-vn", "-sn", "-dn", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le",
        str(out_path),
    ])
    return out_path


# ── 소리 모양 확인 ────────────────────────────────────────────────────

def profile_change(old: Optional[Dict[str, Any]], new: Dict[str, Any]) -> Optional[str]:
    """지난번 고른 목소리와 소리 모양이 많이 다르면 이유("typical", "active", "silent"), 비슷하면 None."""
    integrated = new.get("integrated")
    if isinstance(integrated, (int, float)) and integrated < NEAR_SILENT_LUFS:
        return "silent"
    if not old:
        return None
    a, b = old.get("typical"), new.get("typical")
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(a - b) > PROFILE_TYPICAL_LU:
        return "typical"
    a, b = old.get("active_ratio"), new.get("active_ratio")
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(a - b) > PROFILE_ACTIVE:
        return "active"
    return None
