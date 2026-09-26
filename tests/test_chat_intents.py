"""기본 도우미(RuleBrain)의 한국어 부탁 풀이 (설계 B4.2, B11 test_chat_intents).

표 하나에 "적는 말 → 도우미가 알아들은 것"을 적어 두고 모두 돌린다 (80개 이상).
- 설계 A부 3장의 예문과 못 하는 부탁(부록 A Refusals), 되묻기, 양과 출처, 한도, 색 낱말.
- 모자람 검사: 두 부탁 중 하나만 알아들으면 "못 알아들은 부분", 나눈 뒤 동작만 남으면 되묻기.
- 범위 자르기: "5분~6분에서 2초 넘게 쉰 곳 표시"에 쉼 4:58~5:03, 5:30~5:33, 5:59~6:04 → 5:00~5:03, 5:30~5:33.

타임라인: 60fps, 시작 01:00:00:00(216000), 길이 10분, 재생 위치 0:10, 대화 기본 색 초록.
두뇌는 리졸브에 묻지 않는다 (여기에는 리졸브가 아예 없다).
"""

from __future__ import annotations

import pytest

from engine.chat import actions
from engine.chat.brain import (DEFAULT, FOUND, OPS_2_1, SAID, SETTING, ChatBrain, Command, NeedTimeline, NotUnderstood,
                               ProposalDraft, Question, Reply, validate_draft)
from engine.chat.context import AssistContext
from engine.chat.rules import MAX_MARK_ITEMS, RuleBrain, audio_mark_name
from engine.chat.session import ChatSession
from engine.chat.timeparse import TimeContext

TL0 = 216000
FPS = 60


class Caps:
    def __init__(self, **values) -> None:
        self.values = values

    def has(self, cap):
        return self.values.get(cap)


def _ctx(length_s: float = 600, playhead_s=10.0, **kw) -> AssistContext:
    t = TimeContext(fps=60.0, start=TL0, end=TL0 + int(length_s * FPS), fps_text="60", start_tc="01:00:00:00",
                    playhead=None if playhead_s is None else TL0 + int(playhead_s * FPS), name="Timeline 1")
    return AssistContext(time=t, **kw)


def _s(frame: int) -> float:
    return round((frame - TL0) / FPS, 3)


def _cmd(c: Command):
    if c.op == "mark":
        items = tuple((_s(i["at"]), _s(i["end"]) if i["end"] - i["at"] > 1 else None, i["color"], i["name"])
                      for i in c.params["items"])
        if c.offer == "audio":
            return ("mark", "audio", items, c.params["db"], c.params["direction"])
        return ("mark", c.offer, items)
    if c.op in ("mark_pauses", "mark_spikes", "clear_marks"):
        rng = (_s(c.scope.range[0]), _s(c.scope.range[1])) if c.scope.range else None
        params = {k: v for k, v in c.params.items() if k != "time"}
        if c.op == "clear_marks":
            return (c.op, params, rng, c.scope.range_src, c.scope.said_whole)
        return (c.op, params, rng, c.scope.range_src)
    if c.op == "jump_to":
        return ("jump_to", _s(c.params["frame"]))
    return (c.op, dict(c.params))


def summary(r):
    if isinstance(r, ProposalDraft):
        return ("draft", tuple(_cmd(c) for c in r.commands), tuple(r.leftovers))
    if isinstance(r, Question):
        return ("?", r.code)
    if isinstance(r, NeedTimeline):
        return ("need",)
    if isinstance(r, Reply):
        return ("reply", r.code)
    if isinstance(r, NotUnderstood):
        return ("??",)
    raise AssertionError(r)


def draft(*cmds, left=()):
    return ("draft", tuple(cmds), tuple(left))


def mark(*items, offer=None):
    return ("mark", offer, tuple(items))


def pt(s, color="Green", name="표시"):
    return (s, None, color, name)


def audio(at, end=None, db=6.0, direction="down"):
    word = "줄이기" if direction == "down" else "키우기"
    shown = int(db) if db == int(db) else db
    return ("mark", "audio", ((at, end, "Yellow", f"여기 {shown}dB {word}"),), db, direction)


def cut(at):
    return mark((at, None, "Purple", "자르기 후보"), offer="cut")


def pauses(params=None, rng=None, src="none"):
    return ("mark_pauses", params or {}, rng, src)


def spikes(params=None, rng=None, src="none"):
    return ("mark_spikes", params or {}, rng, src)


def clear(params=None, rng=None, src="none", whole=False):
    return ("clear_marks", params or {}, rng, src, whole)


def jump(s):
    return ("jump_to", s)


UNDO = ("undo", {"which": "last"})


def reply(code):
    return ("reply", code)


def ask(code):
    return ("?", code)


NOT_UNDERSTOOD = ("??",)

# ── 설계 A부 3장 "부탁 예시"와 못 하는 부탁 ─────────────────────────

PART_A = [
    ("여기 표시해줘", draft(mark(pt(10.0)))),
    ("3분 20초에 빨간 표시, 메모는 '자막 확인'", draft(mark(pt(200.0, "Red", "자막 확인")))),
    ("3분 20초로 가줘", draft(jump(200.0))),
    ("5분~6분에서 2초 넘게 쉰 곳 표시해줘", draft(pauses({"min_s": 2.0}, (300.0, 360.0), "said"))),
    ("도우미가 넣은 파란 표시 지워줘", draft(clear({"colors": ["Blue"]}))),
    ("방금 거 취소", draft(UNDO)),
    ("3분쯤 웃음소리 있는 곳 표시해줘", reply("needs_ai")),  # 그다음 판 (AI)
    ("3분 15초~3분 25초 6dB 줄여줘", draft(audio(195.0, 205.0))),  # 구간 소리 조절 전: 노란 표시를 권한다
    ("3분 20초 소리 줄여줘", draft(audio(200.0))),
    # 못 하는 부탁 (부록 A Refusals)
    ("여기서 잘라줘", draft(cut(10.0))),
    ("이 클립 옮겨줘", draft(cut(10.0))),
    ("3분 20초에서 잘라줘", draft(cut(200.0))),
    ("리졸브 자동 자막 만들어줘", reply("studio")),
    ("목소리 분리 켜줘", reply("studio")),
    ("음성 인식으로 받아쓰기 해줘", reply("studio")),
    ("볼륨 키프레임으로 서서히 줄여줘", reply("keyframe")),
    ("EQ 걸어줘", reply("keyframe")),
    ("컴프레서 걸어줘", reply("keyframe")),
    ("노이즈 제거 해줘", reply("keyframe")),
    ("3분 20초 소리 15dB 키워줘", ask("over_gain")),
    ("색 보정 해줘", reply("out_of_scope")),
    ("다른 타임라인 지워줘", reply("out_of_scope")),
    ("렌더 해줘", reply("out_of_scope")),
    ("쉬는 곳 잘라줘", reply("cut_no_place")),
    ("자막 만들어줘", reply("subtitles_later")),
    ("3분 20초에 자막 넣어줘", reply("subtitles_later")),  # 시간을 말해도 자막을 표시로 바꿔 넣지 않는다
    ("소리 줄여줘", reply("audio_not_ready")),  # 자리를 말하지 않음
    ("소리 고르게 해줘", reply("balance_not_ready")),
    ("-16으로 맞춰줘", reply("balance_not_ready")),
    ("16 LUFS로 맞춰줘", reply("balance_not_ready")),
    ("원본 클립 꺼줘", reply("not_ready")),
]

# ── 표시: 시간 모양, 색, 이름 ──────────────────────────────────────────

MARKS = [
    ("3분 20초에 표시해줘", draft(mark(pt(200.0)))),
    ("3분20초에 표시", draft(mark(pt(200.0)))),
    ("3:20에 표시해줘", draft(mark(pt(200.0)))),
    ("200초에 표시해줘", draft(mark(pt(200.0)))),
    ("3분 반에 표시해줘", draft(mark(pt(210.0)))),
    ("01:03:20:00에 표시해줘", draft(mark(pt(200.0)))),
    ("1:03:20에 표시해줘", draft(mark(pt(200.0)))),  # 지난 시간이면 밖 → 리졸브 시간으로 (카드에 적는다)
    ("3분쯤 표시해줘", draft(mark(pt(180.0)))),  # 표시 넣기의 "쯤"은 그 자리
    ("여기 앞뒤 2초 표시해줘", draft(mark((8.0, 12.0, "Green", "표시")))),
    ("여기서부터 10초 표시해줘", draft(mark((10.0, 20.0, "Green", "표시")))),
    ("3분에 빨강 마커 찍어줘", draft(mark(pt(180.0, "Red")))),
    ("3분에 노란 표시", draft(mark(pt(180.0, "Yellow")))),
    ("3분에 노랑 표시", draft(mark(pt(180.0, "Yellow")))),
    ("3분에 파랑 표시", draft(mark(pt(180.0, "Blue")))),
    ("3분에 초록 표시", draft(mark(pt(180.0, "Green")))),
    ("3분에 녹색 표시", draft(mark(pt(180.0, "Green")))),
    ("3분에 보라 표시", draft(mark(pt(180.0, "Purple")))),
    ("3분에 분홍 표시", draft(mark(pt(180.0, "Pink")))),
    ("3분에 핑크 표시", draft(mark(pt(180.0, "Pink")))),
    ("3분에 하늘색 표시", draft(mark(pt(180.0, "Sky")))),
    ("3분에 민트 표시", draft(mark(pt(180.0, "Mint")))),
    ("3분에 연보라 표시", draft(mark(pt(180.0, "Lavender")))),
    ("3분 20초에 '인트로 끝' 표시해줘", draft(mark(pt(200.0, name="인트로 끝")))),
    ("3분 20초에 \u201c자막 확인\u201d 표시", draft(mark(pt(200.0, name="자막 확인")))),
    ("3분 20초에 표시해줘 이름은 오프닝", draft(mark(pt(200.0, name="오프닝")))),
    ("1분이랑 2분에 표시해줘", draft(mark(pt(60.0), pt(120.0)))),
    ("1분, 2분, 3분에 표시해줘", draft(mark(pt(60.0), pt(120.0), pt(180.0)))),
    ("3분에 표시하고 4분에 자르기", draft(mark(pt(180.0)), cut(240.0))),
    ("1분에 표시해줘. 2분으로 가줘", draft(mark(pt(60.0)), jump(120.0))),
    ("끝에 표시해줘", ask("mark_where")),  # "끝에"는 시간 말이 아니다 (2.1): 넣지 않고 어디에 할지 묻는다
    ("여기서부터 끝까지 표시해줘", draft(mark((10.0, 600.0, "Green", "표시")))),
    # "3분 20"의 생략한 초 뒤에 다른 말이 와도 3분 20초 (3분으로 줄이지 않는다)
    ("3분 20 표시해줘", draft(mark(pt(200.0)))),
    ("3분 20 빨간 표시", draft(mark(pt(200.0, "Red")))),
    ("3분 20, 표시해줘", draft(mark(pt(200.0)))),  # 시간만 있는 마디가 "어디에?" 마디에 붙는다
    ("1~2분 30초 표시해줘", draft(mark((60.0, 150.0, "Green", "표시")))),  # 1분~2분 30초
]

# ── 쉬는 곳·튀는 소리 찾기 ─────────────────────────────────────────────

FINDS = [
    ("쉬는 곳 표시해줘", draft(pauses())),
    ("튀는 소리 표시해줘", draft(spikes())),
    ("무음 구간 찾아줘", draft(pauses())),
    ("큰 소리 표시해줘", draft(spikes())),
    ("6dB 넘게 튀는 소리 표시해줘", draft(spikes({"above_lu": 6.0}))),
    ("쉬는 곳 3개만 표시해줘", draft(pauses({"count": 3}))),
    ("한 곳만 쉬는 곳 표시해줘", draft(pauses({"count": 1}))),
    ("전체에서 쉬는 곳 표시해줘", draft(pauses(rng=(0.0, 600.0), src="whole"))),
    ("처음부터 끝까지 튀는 소리 표시해줘", draft(spikes(rng=(0.0, 600.0), src="whole"))),
    ("5분에서 6분 사이 쉬는 곳 표시해줘", draft(pauses(rng=(300.0, 360.0), src="said"))),
    ("5분하고 6분 사이 쉬는 곳 표시해줘", draft(pauses(rng=(300.0, 360.0), src="said"))),
    ("5~6분 쉬는 곳 표시해줘", draft(pauses(rng=(300.0, 360.0), src="said"))),
    ("5분부터 6분까지 쉬는 곳 표시해줘", draft(pauses(rng=(300.0, 360.0), src="said"))),
    ("9분~11분 쉬는 곳 표시해줘", draft(pauses(rng=(540.0, 600.0), src="said"))),  # 끝에서 자른다
    ("3분쯤 쉬는 곳 표시해줘", draft(pauses(rng=(175.0, 185.0), src="around"))),  # 찾는 말의 "쯤"은 앞뒤 5초
    ("여기 튀는 소리 표시해줘", draft(spikes(rng=(5.0, 15.0), src="around"))),
    ("여기서부터 10초 동안 쉬는 곳 표시해줘", draft(pauses(rng=(10.0, 20.0), src="relative"))),
    ("여기 앞뒤 3초 튀는 소리", draft(spikes(rng=(7.0, 13.0), src="relative"))),
    ("마지막 30초 쉬는 곳 표시해줘", draft(pauses(rng=(570.0, 600.0), src="relative"))),
    ("처음 5분 쉬는 곳 표시", draft(pauses(rng=(0.0, 300.0), src="relative"))),
    ("5분~6분에서 2초 넘게 쉰 곳 파란색으로 표시해줘",
     draft(pauses({"min_s": 2.0, "color": "Blue"}, (300.0, 360.0), "said"))),
    ("5분~6분에서 쉬는 곳 표시해줘, 빨간색으로", draft(pauses({"color": "Red"}, (300.0, 360.0), "said"))),
    ("쉬는 곳 표시하고 튀는 소리 표시해줘", draft(pauses(), spikes())),
    ("In~Out에서 쉬는 곳 표시해줘", reply("time_in_out")),  # 기능 점검 전
    ("5~6분 30초 쉬는 곳 표시해줘", draft(pauses(rng=(300.0, 390.0), src="said"))),  # 5초~가 아니다
    # "2초 (정도) 쉰 곳"은 쉰 길이 (앞 7초 안에서 찾기가 아니다). 분이 있거나 조사가 붙으면 자리
    ("2초 정도 쉰 곳 표시해줘", draft(pauses({"min_s": 2.0}))),
    ("2초 쉰 곳 표시해줘", draft(pauses({"min_s": 2.0}))),
    ("3분 20초 쉬는 곳 표시해줘", draft(pauses(rng=(195.0, 205.0), src="around"))),
    ("3초에 쉬는 곳 표시해줘", draft(pauses(rng=(0.0, 8.0), src="around"))),
    # 마디 맨 앞의 조사 없는 "지금"은 "이제" (재생 위치 앞뒤 5초로 좁히지 않는다). "지금 위치"는 재생 위치
    ("지금 쉬는 곳 표시해줘", draft(pauses())),
    ("지금 위치 쉬는 곳 표시해줘", draft(pauses(rng=(5.0, 15.0), src="around"))),
    # 트랙을 골라 찾기는 2.1에서 못 한다: 늘 막히는 카드 대신 먼저 알린다
    ("A2에서 쉬는 곳 표시해줘", reply("find_track")),
    ("2번 트랙 튀는 소리 표시해줘", reply("find_track")),
    ("쉬는 곳 찾고 표시해줘", draft(pauses())),  # "찾고 표시해줘"는 한 부탁
]

# ── 지우기 (도우미 표시만) ─────────────────────────────────────────────

CLEARS = [
    ("3분에 표시한 거 지워줘", draft(clear(rng=(179.5, 180.517), src="point"))),
    ("5분~6분 표시 지워줘", draft(clear(rng=(300.0, 360.0), src="said"))),
    ("도우미 표시 모두 지워줘", draft(clear(whole=True))),
    ("도우미가 넣은 표시 전부 지워줘", draft(clear(rng=(0.0, 600.0), src="whole", whole=True))),
    ("쉬는 곳 표시 지워줘", draft(clear({"kinds": ["mark_pauses"]}))),
    ("튀는 소리 표시 없애줘", draft(clear({"kinds": ["mark_spikes"]}))),
    ("빨간 표시랑 파란 표시 지워줘", draft(clear({"colors": ["Blue", "Red"]}))),
    ("도우미가 넣은 거 모두 빼줘", draft(("remove_all_ours", {}))),
    ("3분 20초에 있는 거 지워줘", ask("clear_what")),  # 무엇을 지울지 모름
    # 시간·범위를 말한 "표시 취소"는 방금 것 되돌리기가 아니라 그 자리의 도우미 표시 지우기 (범위 지킴이를 거친다)
    ("3분에 표시 취소", draft(clear(rng=(179.5, 180.517), src="point"))),
    ("3분에 넣은 표시 취소해줘", draft(clear(rng=(179.5, 180.517), src="point"))),
    ("5분~6분 표시 취소", draft(clear(rng=(300.0, 360.0), src="said"))),
    ("3분 취소", ask("clear_what")),
    ("다 취소해줘", ask("undo_all")),  # 되돌리기는 하나씩: 모두 빼기인지 묻는다
    ("전체 취소", ask("undo_all")),
    # 사용자가 직접 넣은 표시는 지우지 않는다 (도우미 표시 모두 지우기로 바꾸지 않는다)
    ("내가 찍은 표시 지워줘", reply("own_markers")),
    ("제가 넣은 표시 지워줘", reply("own_markers")),
    ("직접 넣은 빨간 표시 지워줘", reply("own_markers")),
    # 방금 + 색: 방금 것 전체인지 그 색 표시 모두인지 모름 → 되묻기
    ("방금 파란 표시 지워", ask("recent_clear")),
    ("방금 넣은 빨간 표시 빼줘", ask("recent_clear")),
]

# ── 재생 위치·도움말·상태·되돌리기·저장 ─────────────────────────────────

OTHERS = [
    ("처음으로 가줘", draft(jump(0.0))),
    ("끝으로 가줘", draft(jump(599.983))),  # Lua jump_to는 끝 프레임을 밖으로 본다
    ("맨 앞으로 이동", draft(jump(0.0))),
    ("5분으로 이동해줘", draft(jump(300.0))),
    ("여기로 가줘", draft(jump(10.0))),
    ("01:05:00:00으로 가줘", draft(jump(300.0))),
    ("11분으로 가줘", reply("time_outside")),
    ("뭐 할 수 있어?", reply("help")),
    ("도움말", reply("help")),
    ("지금 타임라인 몇 분이야?", reply("status")),
    ("재생 위치 어디야?", reply("status")),
    ("길이가 얼마야?", reply("status")),
    ("되돌려 줘", draft(UNDO)),
    ("방금 넣은 거 빼줘", draft(UNDO)),
    ("자동화 3에 저장해줘", draft(("save_slot", {"slot": 3}))),
    ("2번 버튼에 저장", draft(("save_slot", {"slot": 2}))),
    ("이대로 저장", draft(("save_slot", {"slot": None}))),
]

# ── 되묻기와 못 알아들음 ──────────────────────────────────────────────

QUESTIONS = [
    ("쉬는 곳 빼고 표시해줘", ask("negation")),
    ("3분 말고 4분에 표시해줘", ask("negation")),
    ("3분 20초", ask("what_to_do_time")),
    ("지워줘", ask("what_to_do")),
    ("파란 거", ask("what_to_do")),
    ("표시해줘", ask("mark_where")),
    ("3분에 주황 표시해줘", ask("what_to_do_time")),  # 리졸브에 없는 색: 다른 색으로 넣지 않는다
    ("11분에 표시해줘", reply("time_outside")),
    ("6분~5분 쉬는 곳 표시해줘", reply("time_reversed")),
    ("In~Out에 표시해줘", reply("time_in_out")),
    ("아무말 대잔치", NOT_UNDERSTOOD),
    ("오늘 날씨 어때", NOT_UNDERSTOOD),
    ("", NOT_UNDERSTOOD),
]

# ── 소리: 양과 방향, 한도 ──────────────────────────────────────────────

AUDIO = [
    ("3분 20초 소리 조금 줄여줘", draft(audio(200.0, db=3.0))),
    ("3분 20초 소리 살짝 키워줘", draft(audio(200.0, db=3.0, direction="up"))),
    ("3분 20초 소리 많이 줄여줘", draft(audio(200.0, db=10.0))),
    ("3분 20초 소리 확 줄여줘", draft(audio(200.0, db=10.0))),
    ("3분 20초 소리 3데시벨 줄여줘", draft(audio(200.0, db=3.0))),
    ("3분 20초 소리 4디비 낮춰줘", draft(audio(200.0, db=4.0))),
    ("3분 20초 소리 12dB 키워줘", draft(audio(200.0, db=12.0, direction="up"))),
    ("3분 20초 소리 30dB 줄여줘", draft(audio(200.0, db=24.0))),  # 줄이기는 24dB까지
    ("여기 소리 작게 해줘", draft(audio(10.0))),
]

# ── 모자람 검사 (설계 B4.2 Under-delivery) ────────────────────────────

UNDER_DELIVERY = [
    # 두 부탁 모두 알아들음 → 한 카드에 두 줄
    ("3분 20초에 빨간 표시하고 5분에 파란 표시", draft(mark(pt(200.0, "Red")), mark(pt(300.0, "Blue")))),
    ("1분에 빨간 표시 그리고 2분에 파란 표시", draft(mark(pt(60.0, "Red")), mark(pt(120.0, "Blue")))),
    ("1분에 빨간 표시, 2분에 파란 표시, 3분에 초록 표시",
     draft(mark(pt(60.0, "Red")), mark(pt(120.0, "Blue")), mark(pt(180.0, "Green")))),
    ("3분에 빨간 표시하고 4분 5분에 파란 표시", draft(mark(pt(180.0, "Red")), mark(pt(240.0, "Blue"), pt(300.0, "Blue")))),
    # 둘째 마디를 못 알아들음 → 한 줄 + 못 알아들은 부분
    ("3분 20초에 빨간 표시하고 5분에 파란 뭐시기", draft(mark(pt(200.0, "Red")), left=("5분에 파란 뭐시기",))),
    ("3분 20초에 빨간 표시하고 5분에 주황 표시", draft(mark(pt(200.0, "Red")), left=("5분에 주황 표시",))),
    # 남은 색 낱말 → 경고
    ("3분 20초에 표시, 빨간색으로 초록도", draft(mark(pt(200.0, "Red")), left=("초록도",))),
    # 나눈 뒤 동작만 남은 마디 → 부탁 전체를 되묻기
    ("3분 20초에 표시하고 지워", ask("verb_only")),
    # 앞뒤 말로 대상을 아는 동작(취소)은 되묻지 않는다
    ("3분 20초에 표시하고 방금 거 취소", draft(mark(pt(200.0)), UNDO)),
    # 재생 위치 옮기기 + 못 알아들은 부분
    ("2분으로 가줘 그리고 뭐시기 해줘", draft(jump(120.0), left=("뭐시기 해줘",))),
    # 못 하는 부탁이 섞이면 할 수 있는 줄만 (못 하는 부분은 답으로 알린다)
    ("3분에 표시하고 EQ 걸어줘", draft(mark(pt(180.0)))),
    # "-주고"와 다른 동사 뒤의 "-고"도 나눈다 (표시가 옆 부탁의 시간을 가져가지 않는다)
    ("3분에 표시해주고 5분으로 가줘", draft(mark(pt(180.0)), jump(300.0))),
    ("3분에 표시 해 주고 5분으로 가줘", draft(mark(pt(180.0)), jump(300.0))),
    ("3분 소리 키우고 5분에 표시", draft(audio(180.0, direction="up"), mark(pt(300.0)))),
    ("3분 20초 소리 줄이고 5분에 표시", draft(audio(200.0), mark(pt(300.0)))),
    ("3분으로 옮기고 5분에 표시", draft(jump(180.0), mark(pt(300.0)))),
    ("3분에서 자르고 5분에 표시해줘", draft(cut(180.0), mark(pt(300.0)))),
    ("EQ 걸고 3분에 표시", draft(mark(pt(180.0)))),
    # 표시 규칙은 표시 동작만 쓴다: 같은 마디의 다른 동작은 못 알아들은 부분
    ("3분으로 가줘 표시해줘", draft(mark(pt(180.0)), left=("3분으로 가줘",))),
    ("3분 20초 소리 키워서 표시해줘", draft(mark(pt(200.0)), left=("키워서",))),
    ("쉬는 곳 줄여줘", draft(pauses(), left=("줄여줘",))),
    # 권하는 표시는 말한 시간마다 하나씩
    ("3분과 5분에서 잘라줘", draft(mark((180.0, None, "Purple", "자르기 후보"), (300.0, None, "Purple", "자르기 후보"),
                                    offer="cut"))),
    ("3분 20초와 5분 소리 줄여줘",
     draft(("mark", "audio", ((200.0, None, "Yellow", "여기 6dB 줄이기"), (300.0, None, "Yellow", "여기 6dB 줄이기")),
            6.0, "down"))),
    # 되돌리기는 시간을 쓰지 않는다: 같은 마디의 다른 부탁은 못 알아들은 부분
    ("방금 거 취소 3분에 표시", draft(UNDO, left=("3분에 표시",))),
    # 나눈 자리에 걸친 "찍고"·"달고"는 앞 마디의 동작 (거짓 "못 알아들은 부분"이 없다)
    ("3분에 표시 찍고 5분으로 가줘", draft(mark(pt(180.0)), jump(300.0))),
    ("5분에 빨간 표시 달고 6분에 파란 표시 달아줘", draft(mark(pt(300.0, "Red")), mark(pt(360.0, "Blue")))),
    ("3분에 찍고 5분에 찍어줘", draft(mark(pt(180.0)), mark(pt(300.0)))),
]

ALL_CASES = PART_A + MARKS + FINDS + CLEARS + OTHERS + QUESTIONS + AUDIO + UNDER_DELIVERY


@pytest.mark.parametrize("text,want", ALL_CASES, ids=[f"{i:03d}-{c[0][:20]}" for i, c in enumerate(ALL_CASES)])
def test_korean_requests(text, want):
    assert summary(RuleBrain().handle(text, _ctx(default_color="Green"))) == want


def test_table_is_big_enough():
    """설계 B11: 80개 이상 (모자람 검사 포함)."""
    assert len(ALL_CASES) >= 80, len(ALL_CASES)
    assert len(UNDER_DELIVERY) >= 5


# ── 출처 (설계 B4.4) ─────────────────────────────────────────────────


def _draft(text: str, **kw) -> ProposalDraft:
    r = RuleBrain().handle(text, _ctx(**kw))
    assert isinstance(r, ProposalDraft), r
    return r


def test_mark_provenance_is_per_value():
    it = _draft("3분 20초에 빨간 표시, 메모는 '자막 확인'").commands[0].params["items"][0]
    assert it["src"] == {"at": SAID, "color": SAID, "name": SAID} and it["note"] == "자막 확인"
    it = _draft("여기 표시해줘", default_color="Blue").commands[0].params["items"][0]
    assert it["color"] == "Blue" and it["src"] == {"at": SAID, "color": DEFAULT, "name": DEFAULT}
    assert it["note"] == "여기 표시해줘"  # 메모를 말하지 않으면 부탁 글 그대로
    it = _draft("이 클립 옮겨줘").commands[0].params["items"][0]
    assert it["src"]["at"] == DEFAULT  # 자리를 말하지 않아서 재생 위치
    assert actions.mark_provenance([it, dict(it, src={"at": SAID, "color": DEFAULT, "name": DEFAULT})])["at"] == "mixed"


def test_word_amounts_are_defaults_and_numbers_are_said():
    c = _draft("3분 20초 소리 조금 줄여줘").commands[0]
    assert c.params["db"] == 3.0 and c.provenance["db"] == DEFAULT
    c = _draft("3분 20초 소리 5dB 줄여줘").commands[0]
    assert c.params["db"] == 5.0 and c.provenance["db"] == SAID
    c = _draft("3분 20초 소리 줄여줘").commands[0]
    assert c.params["db"] == 6.0 and c.provenance["db"] == DEFAULT
    c = _draft("3분 20초 소리 30dB 줄여줘").commands[0]
    assert c.params["db"] == 24.0 and ("gain_clamped", {"db": 30.0, "max": 24.0}) in c.notes
    assert c.params["items"][0]["name"] == audio_mark_name(24.0, "down") == "여기 24dB 줄이기"


def test_find_values_come_from_what_was_said_then_the_slot_then_defaults():
    ctx = _ctx()
    c = _draft("5분~6분에서 2초 넘게 쉰 곳 표시해줘").commands[0]
    req, prov, slot_no = actions.find_request(c, ctx, text="5분~6분에서 2초 넘게 쉰 곳 표시해줘")
    assert req.kind == "mark_pauses" and req.slot is None and req.origin == "chat:rule"
    assert req.params["min_s"] == 2.0 and req.range == (TL0 + 300 * FPS, TL0 + 360 * FPS) and req.range_src == "said"
    assert prov == {"min_s": SAID, "color": DEFAULT, "range": SAID, "places": FOUND} and slot_no is None
    # 자동화 버튼의 설정이 있으면 말하지 않은 값은 설정값
    ctx = _ctx(slots=[{"slot": 1, "kind": "mark_pauses", "params": {"min_s": 3.0, "color": "Cyan"}}])
    c = _draft("쉬는 곳 표시해줘").commands[0]
    req, prov, slot_no = actions.find_request(c, ctx, text="쉬는 곳 표시해줘")
    assert req.params["min_s"] == 3.0 and req.params["color"] == "Cyan" and req.range is None
    assert prov["min_s"] == SETTING and prov["color"] == SETTING and prov["range"] == DEFAULT and slot_no == 1
    c = _draft("쉬는 곳 3개만 빨간색으로 표시해줘").commands[0]
    req, prov, _ = actions.find_request(c, ctx, text="")
    assert req.count == 3 and prov["count"] == SAID and req.params["color"] == "Red" and prov["color"] == SAID
    c = _draft("6dB 넘게 튀는 소리 표시해줘").commands[0]
    req, prov, _ = actions.find_request(c, _ctx(), text="")
    assert req.params["above_lu"] == 6.0 and prov["above_lu"] == SAID


# ── 되묻기의 모양 ─────────────────────────────────────────────────────


def test_time_read_two_ways_asks_with_both_chips():
    ctx = _ctx(length_s=2 * 3600)
    q = RuleBrain().handle("1:03:20에 표시해줘", ctx)
    assert isinstance(q, Question) and q.code == "tc_ambiguous"
    assert [c.key for c in q.chips] == ["tc_elapsed", "tc_resolve"]
    assert q.chips[0].data["seconds"] == 3800.0 and q.chips[1].data["tc"] == "01:03:20:00"
    assert q.chips[1].data["seconds"] == 200.0 and q.chips[1].data["raw"] == "1:03:20"


def test_over_gain_asks_with_a_clamped_draft():
    q = RuleBrain().handle("3분 20초 소리 15dB 키워줘", _ctx())
    assert isinstance(q, Question) and q.code == "over_gain" and q.data == {"db": 15.0, "max": 12.0}
    c = q.draft.commands[0]
    assert c.params["db"] == 12.0 and c.params["items"][0]["name"] == "여기 12dB 키우기" and c.offer == "audio"


def test_verb_only_question_offers_the_understood_part():
    q = RuleBrain().handle("3분 20초에 표시하고 지워", _ctx())
    assert q.data["clause"] == "지워"
    assert q.chips[0].key == "text" and q.chips[0].data["text"] == "3분 20초에 표시해줘"


def test_nothing_understood_gives_three_examples():
    r = RuleBrain().handle("오늘 날씨 어때", _ctx())
    assert isinstance(r, NotUnderstood) and len(r.chips) == 3
    assert all(c.key.startswith("example:") for c in r.chips)


def test_refused_part_is_noted_on_the_draft():
    d = _draft("3분에 표시하고 EQ 걸어줘")
    assert d.notes == [("refused", {"code": "keyframe"})]
    d = _draft("EQ 걸고 3분에 표시")
    assert d.notes == [("refused", {"code": "keyframe"})]


def test_help_with_another_request_keeps_the_rest_as_a_leftover():
    """도움말 낱말만 쓴다. 같은 마디의 "3분에 표시"는 조용히 버리지 않고 답에 실어 보낸다."""
    r = RuleBrain().handle("도움말 3분에 표시", _ctx())
    assert isinstance(r, Reply) and r.code == "help" and r.data["leftovers"] == ["3분에 표시"]
    r = RuleBrain().handle("도움말", _ctx())
    assert isinstance(r, Reply) and "leftovers" not in r.data


def test_undo_all_and_recent_colour_questions_offer_chips():
    q = RuleBrain().handle("다 취소해줘", _ctx())
    assert [c.key for c in q.chips] == ["example:undo", "example:remove_all"]
    q = RuleBrain().handle("방금 파란 표시 지워", _ctx())
    assert q.chips[0].key == "example:undo" and q.chips[1].data["text"] == "파란 표시 지워"
    r = RuleBrain().handle("내가 찍은 표시 지워줘", _ctx())
    assert [c.key for c in r.chips] == ["example:clear_ours"]


def test_find_on_a_named_track_is_refused_up_front():
    r = RuleBrain().handle("A2에서 쉬는 곳 표시해줘", _ctx())
    assert r.code == "find_track" and r.data == {"tracks": [2]} and r.chips[0].key == "example:pauses"
    d = _draft("3분에 표시하고 A2에서 튀는 소리 표시해줘")
    assert [c.op for c in d.commands] == ["mark"] and d.notes == [("refused", {"code": "find_track", "tracks": [2]})]


@pytest.mark.parametrize("text,key,said,used,note", [
    ("10초 넘게 쉰 곳 표시해줘", "min_s", 10.0, 5.0, "min_s_clamped"),
    ("0.2초 넘게 쉰 곳 표시해줘", "min_s", 0.2, 0.5, "min_s_clamped"),
    ("30dB 넘게 튀는 소리 표시해줘", "above_lu", 30.0, 20.0, "above_clamped"),
])
def test_clamped_find_values_are_not_called_said(text, key, said, used, note):
    """범위 밖의 말한 값은 끝으로 맞추고, "말씀하신 값"이라 하지 않고 카드에 한 줄 적는다 (설계 B4.4, B4.5)."""
    c = _draft(text).commands[0]
    req, prov, _ = actions.find_request(c, _ctx(), text=text)
    assert req.params[key] == used and prov[key] == actions.CLAMPED and prov[key] != SAID
    lo, hi = (0.5, 5.0) if key == "min_s" else (4.0, 20.0)
    assert (note, {"said": said, "used": used, "lo": lo, "hi": hi}) in c.notes
    actions.find_request(c, _ctx(), text=text)  # 다시 불러도 줄은 하나
    assert [n for n in c.notes if n[0] == note] == [(note, {"said": said, "used": used, "lo": lo, "hi": hi})]
    # 범위 안의 값은 그대로 말씀하신 값
    c = _draft("3초 넘게 쉰 곳 표시해줘").commands[0]
    req, prov, _ = actions.find_request(c, _ctx(), text="")
    assert req.params["min_s"] == 3.0 and prov["min_s"] == SAID and not c.notes


# ── 타임라인을 알아야 할 때만 연결을 확인한다 ───────────────────────────


@pytest.mark.parametrize("text,need", [
    ("3분 20초에 표시해줘", True), ("쉬는 곳 표시해줘", True), ("방금 거 취소", True), ("처음으로 가줘", True),
    ("뭐 할 수 있어?", False), ("자동화 3에 저장해줘", False), ("EQ 걸어줘", False), ("아무말", False),
])
def test_need_timeline_only_when_times_matter(text, need):
    r = RuleBrain().handle(text, AssistContext())
    assert isinstance(r, NeedTimeline) is need


def test_no_playhead_is_said_not_guessed():
    r = RuleBrain().handle("여기 표시해줘", _ctx(playhead_s=None))
    assert isinstance(r, Reply) and r.code == "time_no_playhead"


def test_in_out_after_the_check_is_allowed_for_finds_only():
    ctx = _ctx(caps=Caps(in_out=True))
    d = RuleBrain().handle("In~Out에서 쉬는 곳 표시해줘", ctx)
    assert isinstance(d, ProposalDraft)
    c = d.commands[0]
    assert c.params["scope"] == "in_out" and c.scope.range is None and c.scope.range_src == "in_out"
    req, prov, _ = actions.find_request(c, ctx, text="")
    assert req.params["scope"] == "in_out" and req.range is None and prov["range"] == SAID
    r = RuleBrain().handle("In~Out에 표시해줘", ctx)
    assert isinstance(r, Reply) and r.code == "time_in_out"


def test_status_reads_the_timeline():
    r = RuleBrain().handle("지금 타임라인 몇 분이야?", _ctx())
    assert r.data == {"timeline": "Timeline 1", "length_s": 600.0, "fps": "60", "playhead_s": 10.0,
                      "playhead_tc": "01:00:10:00"}


def test_many_marks_are_capped():
    text = ", ".join(f"{s}초" for s in range(1, MAX_MARK_ITEMS + 11)) + "에 표시해줘"
    c = _draft(text).commands[0]
    assert len(c.params["items"]) == MAX_MARK_ITEMS
    assert ("too_many_marks", {"n": MAX_MARK_ITEMS + 10, "cap": MAX_MARK_ITEMS}) in c.notes


def test_mark_items_join_one_card_in_time_order():
    d = _draft("3분에 빨간 표시하고 1분에 파란 표시")
    items, requested, notes = actions.mark_items([c for c in d.commands if c.op == "mark"])
    assert [(_s(i["at"]), i["color"]) for i in items] == [(60.0, "Blue"), (180.0, "Red")]
    assert requested["range"] == [TL0 + 60 * FPS, TL0 + 180 * FPS + 1] and requested["range_src"] == "point"
    assert requested["count_hint"] == 2 and notes == []


def test_clear_args():
    d = _draft("5분~6분 파란 표시 지워줘")
    assert actions.clear_args(d.commands[0]) == {"colors": ["Blue"], "kinds": None, "lo": TL0 + 300 * FPS,
                                                 "hi": TL0 + 360 * FPS, "point": False}
    d = _draft("도우미가 넣은 표시 전부 지워줘")
    assert actions.clear_args(d.commands[0]) == {"colors": None, "kinds": None}


def test_brain_shape_and_allowed_ops():
    brain = RuleBrain()
    assert isinstance(brain, ChatBrain) and brain.name == "rules"
    d = _draft("3분에 표시하고 쉬는 곳 표시해줘")
    assert validate_draft(d) == []
    d.commands.append(Command("range_gain"))
    assert validate_draft(d) == ["range_gain"]
    assert "range_gain" not in OPS_2_1


def test_session_keeps_recent_turns_and_writes_chat_jsonl(tmp_path):
    import json

    s = ChatSession(tmp_path)
    for n in range(8):
        s.add("me", f"말 {n}", tl_key="tl-1", result="draft")
    s.add("helper", "앱 기록", result="reply")
    assert [t["text"] for t in s.recent()] == ["말 3", "말 4", "말 5", "말 6", "말 7", "앱 기록"]
    rows = (tmp_path / "timelines" / "tl-1" / "chat.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 8 and json.loads(rows[0])["brain"] == "rules" and json.loads(rows[0])["text"] == "말 0"
    assert (tmp_path / "chat.jsonl").is_file()
    s.remember_card(kind="mark_pauses", proposal_id="P1")
    assert s.last_card == {"kind": "mark_pauses", "proposal_id": "P1"}


def test_session_without_a_folder_still_works(tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("x", encoding="utf-8")
    s = ChatSession(blocked)  # 폴더 자리에 파일: 쓰지 못해도 대화는 된다
    s.add("me", "안녕", tl_key="tl")
    assert s.write_errors == 1 and len(s.recent()) == 1


# ── 범위 자르기 (설계 B11: 4:58~5:03 → 5:00~5:03) ──────────────────────


def _minute_analysis(path: str, quiet, end: float = 420.0):
    """0.1초마다 M: 말소리 −20, 조용한 [a, b]는 M이 [a+0.4, b]에서 −70 (test_automation_plan과 같은 모양)."""
    from engine.analysis_cache import StreamAnalysis

    t, m = [], []
    for k in range(1, int(end * 10) + 1):
        x = round(k * 0.1, 3)
        t.append(x)
        m.append(-70.0 if any(a + 0.4 - 1e-9 <= x <= b + 1e-9 for a, b in quiet) else -20.0)
    return StreamAnalysis(path=path, stream=0, duration=end, integrated=-20.0, true_peak=-3.0, lra=2.0,
                          typical=-20.0, noise_floor=-70.0, t=t, m=m, s=list(m))


class _Cache:
    """늘 같은 분석을 돌려주는 가짜 분석 저장소 (ffmpeg 없이)."""

    def __init__(self, analysis) -> None:
        self.analysis = analysis
        self.hits = 0
        self.misses = 0

    def get_or_analyze(self, path, stream, media, progress=None, is_cancelled=None):
        self.hits += 1
        return self.analysis, True


def test_range_clip_end_to_end_from_the_sentence():
    """부탁 글 → 두뇌 → 계획 요청 → 계산 → 범위 지킴이. 걸친 쉼은 안쪽만, 1초만 남는 셋째는 뺀다. 막지 않는다."""
    from engine.automation.plan import PlanEnv, VoiceMemory, plan_slot
    from engine.chat.timeparse import context_from_info
    from engine.edits.scope import check_proposal
    from engine.probe import AudioTrack, MediaInfo
    from engine.resolve_link.ops import ResolveOps
    from tests.fakes import FakeResolve, audio_item, timeline_info

    tl0, fps, path = 108000, 30, "C:/long.wav"
    info = timeline_info(start_frame=tl0, end_frame=tl0 + 420 * fps, fps="30", start_tc="01:00:00:00",
                         current_tc="01:00:00:00")
    fake = FakeResolve(info, [audio_item("w", 1, tl0, 420 * fps, path, clip_fps="30")])
    ctx = AssistContext(time=context_from_info(info), default_color="Green")
    text = "5분~6분에서 2초 넘게 쉰 곳 표시해줘"
    d = RuleBrain().handle(text, ctx)
    cmd = d.commands[0]
    req, prov, _ = actions.find_request(cmd, ctx, text=text)
    assert req.range == (tl0 + 300 * fps, tl0 + 360 * fps) and req.params["min_s"] == 2.0
    # 앞뒤 0.2초 여유(pad_s)를 뺀 뒤의 쉼이 4:58~5:03, 5:30~5:33, 5:59~6:04가 되게
    pad = req.params["pad_s"]
    quiet = [(298.0 - pad, 303.0 + pad), (330.0 - pad, 333.0 + pad), (359.0 - pad, 364.0 + pad)]
    media = MediaInfo(path=path, duration=420.0, has_video=False, has_audio=True,
                      audio_tracks=[AudioTrack(index=0, channels=1, sample_rate=48000, codec="pcm_s16le")])
    env = PlanEnv(ops=ResolveOps(fake), cache=_Cache(_minute_analysis(path, quiet)), probe_file=lambda p: media,
                  exists=lambda p: True)
    p = plan_slot(req, env, VoiceMemory())
    got = [((r.start - tl0) / fps, (r.end - tl0) / fps) for r in p.rows]
    assert len(got) == 2, got
    assert got[0][0] == 300.0 and got[0][1] == pytest.approx(303.0, abs=0.15)
    assert got[1][0] == pytest.approx(330.0, abs=0.15) and got[1][1] == pytest.approx(333.0, abs=0.15)
    assert p.scope == {"kind": "range", "lo": tl0 + 300 * fps, "hi": tl0 + 360 * fps, "src": "said"}
    # 대화 카드가 적는 부탁과 견주면: 막지 않고, "전체" 경고도 없다
    p.origin, p.requested = "chat:rule", {"range": list(req.range), "range_src": "said", "said_whole": False,
                                          "tracks": None, "count_hint": None}
    g = check_proposal(p)
    assert not g.blocked and g.codes() == []
    assert prov["places"] == FOUND
    assert not (set(fake.requests) & {"add_markers", "delete_markers"})
