"""원본 시각(초)의 구간 → 리졸브 타임라인 프레임 (설계 B2.4 3~6).

소리를 새로 만들지 않고 표시만 넣는 자동화(쉬는 곳, 튀는 소리)는 원본 파일에서 찾은 구간을
타임라인의 클립들을 거쳐 프레임으로 옮긴다.

- 클립의 원본 시작: src_in = source_start / clip_fps (clip_fps는 클립의 FPS, 타임라인 fps가 아님).
  left_offset / tl_fps와 1프레임 넘게 다르면 경고만 하고 source_start를 쓴다.
- 클립이 덮는 원본 구간: [src_in, src_in + duration / tl_fps)
- 구간 [a, b)의 겹친 부분 → tl_frame = item.start + round((t − src_in) × tl_fps)
- 속도를 바꾼 클립(|(source_end − source_start) × tl_fps / clip_fps − duration| > 2)은 쓰지 않는다.
- 컷으로 나뉜 구간은 조각이 되고, 1프레임 안으로 붙은 조각은 다시 하나로 잇는다.

프레임은 모두 리졸브가 주는 절대 프레임(타임라인 시작 프레임 포함)이다. 표시를 넣을 때
AddMarker의 기준(타임라인 시작부터 센 프레임)으로 바꾸는 것은 넣는 쪽(proposal)이 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from fractions import Fraction
from typing import Iterable, List, Optional, Sequence

from ..loudness import Region
from ..resolve_link.ops import Item

SPEED_TOLERANCE_FRAMES = 2
# 흔한 NTSC 속도는 정확한 분수로 (29.97은 사실 30000/1001)
_NTSC = {23.976: Fraction(24000, 1001), 29.97: Fraction(30000, 1001), 47.952: Fraction(48000, 1001),
         59.94: Fraction(60000, 1001), 119.88: Fraction(120000, 1001)}


def exact_fps(text) -> Optional[float]:
    """"29.97", "60", 25 → 초당 프레임 수 (NTSC 속도는 1001로 나눈 정확한 값). 모르면 None."""
    if text is None or isinstance(text, bool):
        return None
    try:
        value = float(str(text).strip().upper().replace("NDF", "").replace("DF", "").replace("FPS", "").strip())
    except ValueError:
        return None
    if not 0 < value <= 1000:
        return None
    for k, v in _NTSC.items():
        if abs(value - k) < 0.006:
            return float(v)
    return value


@dataclass
class ItemWindow:
    """타임라인 클립 하나가 원본의 어느 시간을 보여 주는지."""

    item: Item
    src_in: float  # 초
    src_out: float  # 초 (들어가지 않는 끝)
    tl_fps: float

    def to_frame(self, t: float) -> int:
        return int(self.item.start) + int(round((t - self.src_in) * self.tl_fps))


@dataclass
class ItemCheck:
    windows: List[ItemWindow] = field(default_factory=list)
    refused_speed: List[Item] = field(default_factory=list)  # 속도를 바꾼 클립
    unmappable: List[Item] = field(default_factory=list)  # 시작·길이를 읽지 못한 클립
    offset_mismatch: int = 0  # left_offset과 source_start가 1프레임 넘게 다른 클립 수
    speed_unchecked: int = 0  # source_end가 없어 속도를 확인하지 못한 클립 수


def item_windows(items: Iterable[Item], tl_fps: float) -> ItemCheck:
    out = ItemCheck()
    for item in items:
        if item.start is None or item.end is None or item.end <= item.start:
            out.unmappable.append(item)
            continue
        duration = item.duration if item.duration is not None else item.end - item.start
        clip_fps = exact_fps(item.clip_fps) or tl_fps
        if item.source_start is not None:
            src_in = item.source_start / clip_fps
            if item.left_offset is not None and abs(src_in - item.left_offset / tl_fps) > 1.0 / tl_fps + 1e-9:
                out.offset_mismatch += 1
        elif item.left_offset is not None:
            src_in = item.left_offset / tl_fps
        else:
            out.unmappable.append(item)
            continue
        if item.source_start is not None and item.source_end is not None:
            played = (item.source_end - item.source_start) * tl_fps / clip_fps
            if abs(played - duration) > SPEED_TOLERANCE_FRAMES:
                out.refused_speed.append(item)
                continue
        else:
            out.speed_unchecked += 1
        out.windows.append(ItemWindow(item, src_in, src_in + duration / tl_fps, tl_fps))
    return out


@dataclass
class Piece:
    """타임라인 위의 구간 조각 (절대 프레임, end는 들어가지 않음).

    start_edge / end_edge: 그 끝이 원래 구간의 끝인지 (False = 클립 경계나 요청 범위에서 잘림).
    raw_start / raw_end: 여유(pad)를 두기 전의 경계 (쉼 길이를 적을 때 쓴다).
    """

    start: int
    end: int
    level: float
    start_edge: bool = True
    end_edge: bool = True
    raw_start: Optional[int] = None
    raw_end: Optional[int] = None

    def __post_init__(self) -> None:
        if self.raw_start is None:
            self.raw_start = self.start
        if self.raw_end is None:
            self.raw_end = self.end

    @property
    def frames(self) -> int:
        return self.end - self.start

    @property
    def raw_frames(self) -> int:
        return int(self.raw_end) - int(self.raw_start)


def map_regions(regions: Iterable[Region], windows: Sequence[ItemWindow]) -> List[Piece]:
    """원본 구간들을 클립마다 옮긴 조각 (잇지 않음, 시작 순서)."""
    out: List[Piece] = []
    regs = list(regions)
    for w in windows:
        lo_frame, hi_frame = int(w.item.start), int(w.item.end)
        for r in regs:
            a, b = max(r.start, w.src_in), min(r.end, w.src_out)
            if b <= a:
                continue
            start = max(lo_frame, w.to_frame(a))
            end = min(hi_frame, w.to_frame(b))
            if end <= start:
                continue
            out.append(Piece(start, end, r.level, start_edge=r.start >= w.src_in - 1e-9,
                             end_edge=r.end <= w.src_out + 1e-9))
    out.sort(key=lambda p: (p.start, p.end))
    return out


def merge_pieces(pieces: Iterable[Piece], gap_frames: int = 1) -> List[Piece]:
    """gap_frames 안으로 붙거나 겹친 조각을 하나로 (level은 큰 값)."""
    out: List[Piece] = []
    for p in sorted(pieces, key=lambda x: (x.start, x.end)):
        if out and p.start - out[-1].end <= gap_frames:
            cur = out[-1]
            if p.end > cur.end:
                cur.end, cur.end_edge, cur.raw_end = p.end, p.end_edge, max(int(cur.raw_end), int(p.raw_end))
            elif p.end == cur.end:
                cur.end_edge = cur.end_edge or p.end_edge
            cur.level = max(cur.level, p.level)
        else:
            out.append(replace(p))
    return out


def pad_pieces(pieces: Iterable[Piece], pad_frames: int) -> List[Piece]:
    """조각 앞뒤를 pad_frames씩 줄인다 (쉬는 곳의 말 바로 옆은 남겨 둔다). 없어지는 조각은 버린다."""
    out: List[Piece] = []
    for p in pieces:
        start, end = p.start + pad_frames, p.end - pad_frames
        if end > start:
            out.append(Piece(start, end, p.level, p.start_edge, p.end_edge, p.raw_start, p.raw_end))
    return out


def clip_pieces(pieces: Iterable[Piece], lo: Optional[int], hi: Optional[int]) -> List[Piece]:
    """요청 범위 [lo, hi) 안쪽만 남긴다 (설계 B2.4 5). 잘린 끝은 edge=False. 여유 전 경계도 같이 자른다."""
    out: List[Piece] = []
    for p in pieces:
        start, end = p.start, p.end
        s_edge, e_edge = p.start_edge, p.end_edge
        raw_start, raw_end = int(p.raw_start), int(p.raw_end)
        if lo is not None:
            if start < lo:
                start, s_edge = lo, False
            raw_start = max(raw_start, lo)
        if hi is not None:
            if end > hi:
                end, e_edge = hi, False
            raw_end = min(raw_end, hi)
        if end > start:
            out.append(Piece(start, end, p.level, s_edge, e_edge, raw_start, max(raw_start, raw_end)))
    return out
