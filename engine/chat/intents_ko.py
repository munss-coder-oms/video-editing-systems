"""한국어 낱말표와 찾기 도구 (설계 B4.2 Normalise, Amounts, Direction, Colours, Objects, Verbs).

여기 있는 한국어는 알아들을 말(부탁에 나오는 낱말)뿐이다. 화면에 보이는 글은 app/companion/strings_ko.py에 있다.
찾기 함수는 모두 (자리, 뜻) 목록을 돌려준다. 자리(span)는 정리한 글(normalize) 안의 [시작, 끝).
규칙(rules.py)은 쓴 자리를 모아 두고, 남은 자리에 시간·동작·색 낱말이 있으면 "못 알아들은 부분"으로 알린다.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Sequence, Tuple

Span = Tuple[int, int]

# ── 정리 ─────────────────────────────────────────────────────────────

_QUOTES = {"“": '"', "”": '"', "„": '"', "‘": "'", "’": "'", "「": "'", "」": "'",
           "『": '"', "』": '"', "«": '"', "»": '"', "`": "'"}
_DASHES = {"–": "-", "—": "-", "−": "-", "〜": "~", "～": "~", "∼": "~"}


def normalize(text: str) -> str:
    """NFC, 전각 → 반각, 따옴표·물결·줄표를 한 모양으로, 빈칸을 하나로."""
    text = unicodedata.normalize("NFC", text or "")
    out = []
    for ch in text:
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            ch = chr(code - 0xFEE0)
        elif code == 0x3000:
            ch = " "
        ch = _QUOTES.get(ch, ch)
        ch = _DASHES.get(ch, ch)
        out.append(ch)
    return re.sub(r"\s+", " ", "".join(out)).strip()


# ── 찾은 것 ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Found:
    start: int
    end: int
    kind: str  # 낱말의 갈래 (색: 색 이름, 동작: mark/clear/..., 대상: marker/pause/...)
    value: Any = None
    text: str = ""

    @property
    def span(self) -> Span:
        return (self.start, self.end)


def _find(regex: "re.Pattern[str]", text: str, kind: str, value: Any = None,
          group: int = 0) -> List[Found]:
    out = []
    for m in regex.finditer(text):
        a, b = m.span(group)
        if b > a:
            out.append(Found(a, b, kind, value, text[a:b]))
    return out


def inside(span: Span, spans: Iterable[Span]) -> bool:
    return any(a <= span[0] and span[1] <= b for a, b in spans)


def overlaps(span: Span, spans: Iterable[Span]) -> bool:
    return any(span[0] < b and a < span[1] for a, b in spans)


# ── 색 (리졸브 표시 색 16개 가운데) ─────────────────────────────────────

COLOR_TABLE: Sequence[Tuple[str, str]] = (
    ("Red", r"빨간|빨강|붉은|레드"),
    ("Yellow", r"노란|노랑|옐로"),
    ("Blue", r"파란|파랑|블루"),
    ("Green", r"초록|녹색|그린"),
    ("Purple", r"보라|퍼플"),
    ("Pink", r"분홍|핑크"),
    ("Sky", r"하늘"),
    ("Mint", r"민트"),
    ("Cyan", r"청록|시안"),
    ("Lavender", r"연보라|라벤더"),
    ("Cream", r"크림"),
    ("Lemon", r"레몬"),
)
# 리졸브 표시에 없는 색 (알아들은 척하지 않고 "못 알아들은 부분"으로 알린다)
UNKNOWN_COLOR_RE = re.compile(r"주황|오렌지|검정|검은|까만|흰|하얀|흰색|회색|갈색")
_COLOR_RES = [(c, re.compile(r"(?:" + p + r")(?:\s*색)?")) for c, p in COLOR_TABLE]


def find_colors(text: str) -> List[Found]:
    out: List[Found] = []
    taken: List[Span] = []
    # 연보라처럼 긴 말을 먼저
    for color, regex in sorted(_COLOR_RES, key=lambda cr: -len(cr[1].pattern)):
        for f in _find(regex, text, "color", color):
            if not overlaps(f.span, taken):
                out.append(f)
                taken.append(f.span)
    out.sort(key=lambda f: f.start)
    return out


def find_unknown_colors(text: str) -> List[Found]:
    return _find(UNKNOWN_COLOR_RE, text, "unknown_color")


# ── 동작 ─────────────────────────────────────────────────────────────

_VERB_TABLE: Sequence[Tuple[str, str]] = (
    # 되돌리기 ("방금 거 취소", "되돌려 줘")
    ("undo", r"취소|되돌려|되돌리|되돌려줘|원래\s*대로|없던\s*걸로|언두"),
    ("save", r"저장"),
    ("clear", r"지워|지우|지운|없애|삭제|빼\s*줘|빼\s*주|빼\s*요|빼라|빼$|빼주세요|제거"),
    ("jump", r"(?:으로|로)\s*(?:가\s*줘|가줘|가\s*자|가요|가\b|가$|가\s*볼래|이동|옮겨|점프)|이동해|이동시켜|옮겨\s*줘|옮겨줘"
             r"|점프|넘어가|찾아가|보여\s*줘|보여줘"),
    ("mark", r"표시\s*(?:해|하|좀|를\s*해|를\s*하|넣)|찍어|찍고|찍어줘|찍|달아|달고|마커\s*(?:넣|달|찍)|넣어\s*줘|넣어줘"),
    ("find", r"찾아|찾기|골라|알려"),
    ("down", r"줄여|줄이|낮춰|낮추|작게|줄여줘|덜\s*크게"),
    ("up", r"키워|키우|올려|올리|크게|높여|높이"),
    ("even", r"고르게|맞춰|맞추|평준화|노멀라이즈|노말라이즈"),
    ("press", r"덜\s*눌러|더\s*눌러"),
    ("onoff", r"켜\s*줘|켜줘|꺼\s*줘|꺼줘|켜|꺼"),
)
_VERB_RES = [(k, re.compile(p)) for k, p in _VERB_TABLE]
EDIT_VERBS = ("clear", "mark", "jump", "find", "down", "up", "even", "press", "onoff")
CONTEXT_VERBS = ("undo", "save")  # 무엇을 할지 앞뒤 말(방금 거, 이대로)로 아는 동작


def find_verbs(text: str) -> List[Found]:
    out: List[Found] = []
    taken: List[Span] = []
    for kind, regex in _VERB_RES:
        for f in _find(regex, text, kind):
            if not overlaps(f.span, taken):
                out.append(f)
                taken.append(f.span)
    out.sort(key=lambda f: f.start)
    return out


# ── 대상 ─────────────────────────────────────────────────────────────

_OBJECT_TABLE: Sequence[Tuple[str, str]] = (
    ("ours", r"도우미\s*(?:가|께서)?\s*(?:넣은|찍은|단|만든)|도우미\s*(?:표시|마커|것|거)|우리가\s*넣은|AI\s*가?\s*넣은"
             r"|자동으로\s*넣은"),
    ("pause", r"쉬는\s*(?:곳|부분|데|구간)?|쉰\s*(?:곳|부분|데|구간)|무음|빈\s*(?:곳|부분|데|구간)|조용한\s*(?:곳|부분|데|구간)"
              r"|말\s*(?:안|않)\s*한\s*(?:곳|부분|데)|공백|쉼"),
    ("spike", r"튀는\s*(?:소리|곳|부분|데)?|큰\s*소리|시끄러운\s*(?:곳|소리|부분|데)?|갑자기\s*커지는\s*(?:곳|소리|부분)?|피크"),
    ("marker", r"표시|마커|깃발|마크"),
    ("audio", r"소리|음량|목소리|볼륨|오디오|사운드|음성"),
    ("subtitle", r"자막"),
    ("clip", r"클립|장면"),
)
_OBJECT_RES = [(k, re.compile(p, re.IGNORECASE)) for k, p in _OBJECT_TABLE]


def find_objects(text: str) -> List[Found]:
    out: List[Found] = []
    taken: List[Span] = []
    for kind, regex in _OBJECT_RES:
        for f in _find(regex, text, kind):
            if not overlaps(f.span, taken):
                out.append(f)
                taken.append(f.span)
    out.sort(key=lambda f: f.start)
    return out


# ── 트랙 ─────────────────────────────────────────────────────────────

_TRACK_RE = re.compile(r"(?<![A-Za-z])[Aa]\s*([1-8])(?!\d)|오디오\s*([1-8])\s*번?\s*트랙|([1-8])\s*번\s*트랙")
_VOICE_TRACK_RE = re.compile(r"마이크|내\s*목소리")
_GAME_TRACK_RE = re.compile(r"게임\s*(?:소리)?")


def find_tracks(text: str) -> List[Found]:
    out = []
    for m in _TRACK_RE.finditer(text):
        n = next(int(g) for g in m.groups() if g)
        out.append(Found(m.start(), m.end(), "track", n, m.group(0)))
    out += _find(_VOICE_TRACK_RE, text, "voice_track")
    out += _find(_GAME_TRACK_RE, text, "game_track")
    out.sort(key=lambda f: f.start)
    return out


# ── 양 ───────────────────────────────────────────────────────────────

_NUM = r"\d+(?:\.\d+)?"
_DB_RE = re.compile(r"([+\-]?\s*" + _NUM + r")\s*(?:dB|db|DB|Db|데시벨|디비)")
_ABOVE_DB_RE = re.compile(r"(" + _NUM + r")\s*(?:dB|db|DB|Db|데시벨|디비)\s*(?:넘게|이상|넘는|넘은|보다\s*(?:큰|크게|더))")
_LUFS_RE = re.compile(r"(-?\s*" + _NUM + r")\s*(?:LUFS|lufs|Lufs|러프스)|(-" + _NUM + r")\s*(?:으로|로)\s*(?:맞춰|맞추)")
_MIN_S_RE = re.compile(r"(" + _NUM + r")\s*초\s*(?:넘게|이상|넘는|넘은|넘어가는|보다\s*(?:길게|긴|오래|더))")
_COUNT_RE = re.compile(r"(\d+|한|두|세|네|다섯)\s*(?:개|곳|군데)\s*(?:만|까지)?")
_ONE_RE = re.compile(r"이거|이것|이\s*부분|이\s*곳|한\s*곳|하나만")
_WORD_NUM = {"한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5}
WORD_AMOUNTS: Sequence[Tuple[str, float]] = (("조금|살짝|약간|좀만", 3.0), ("많이|확|크게|팍", 10.0))
_WORD_AMOUNT_RES = [(re.compile(p), v) for p, v in WORD_AMOUNTS]
BARE_AMOUNT_DB = 6.0


def find_db(text: str) -> List[Found]:
    out = []
    for m in _DB_RE.finditer(text):
        raw = m.group(1).replace(" ", "")
        out.append(Found(m.start(), m.end(), "db", float(raw), m.group(0)))
    return out


def find_above_db(text: str) -> List[Found]:
    return [Found(m.start(), m.end(), "above_db", float(m.group(1)), m.group(0)) for m in _ABOVE_DB_RE.finditer(text)]


def find_lufs(text: str) -> List[Found]:
    out = []
    for m in _LUFS_RE.finditer(text):
        raw = (m.group(1) or m.group(2) or "").replace(" ", "")
        out.append(Found(m.start(), m.end(), "lufs", float(raw), m.group(0)))
    return out


def find_min_s(text: str) -> List[Found]:
    return [Found(m.start(), m.end(), "min_s", float(m.group(1)), m.group(0)) for m in _MIN_S_RE.finditer(text)]


def find_counts(text: str) -> List[Found]:
    out = []
    for m in _COUNT_RE.finditer(text):
        raw = m.group(1)
        n = _WORD_NUM.get(raw)
        if n is None:
            n = int(raw)
        out.append(Found(m.start(), m.end(), "count", n, m.group(0)))
    for f in _find(_ONE_RE, text, "one", 1):
        if not overlaps(f.span, [o.span for o in out]):
            out.append(f)
    out.sort(key=lambda f: f.start)
    return out


def find_word_amounts(text: str) -> List[Found]:
    out: List[Found] = []
    for regex, value in _WORD_AMOUNT_RES:
        out += _find(regex, text, "amount_word", value)
    out.sort(key=lambda f: f.start)
    return out


# ── 메모·이름 ────────────────────────────────────────────────────────

_QUOTE_RE = re.compile(r"'([^']{1,200})'|\"([^\"]{1,200})\"")
_MEMO_KEY_RE = re.compile(r"(?:메모|이름|노트|설명|내용)\s*(?:는|은|를|을|:|로|으로)?\s*$")
_MEMO_BARE_RE = re.compile(r"(?:메모|이름)\s*(?:는|은|:)\s*([^,;.!?'\"]{1,40}?)\s*(?:로|으로|라고)?\s*(?:해\s*줘|해줘|달아|넣어|$|[,;.!?])")


def find_quotes(text: str) -> List[Found]:
    """따옴표 안의 글 (표시 이름·메모). 바로 앞에 "메모는"이 있으면 그 말까지 같은 자리로 본다."""
    out = []
    for m in _QUOTE_RE.finditer(text):
        body = (m.group(1) or m.group(2) or "").strip()
        a = m.start()
        key = _MEMO_KEY_RE.search(text[:a])
        kind = "quote"
        if key:
            a = key.start()
            kind = "memo"
        if body:
            out.append(Found(a, m.end(), kind, body, text[a:m.end()]))
    for m in _MEMO_BARE_RE.finditer(text):
        if not overlaps(m.span(), [f.span for f in out]):
            body = m.group(1).strip()
            if body:
                end = m.end(1)
                out.append(Found(m.start(), end, "memo", body, text[m.start():end]))
    out.sort(key=lambda f: f.start)
    return out


# ── 거절할 부탁과 부정 ────────────────────────────────────────────────

NEGATION_RE = re.compile(r"(?:빼고|말고|제외하고|제외한|빼놓고|빼 놓고)(?=\s*\S)")
STUDIO_RE = re.compile(r"자동\s*자막|자막\s*(?:자동|생성)|리졸브\s*(?:의\s*)?자막|음성\s*인식|받아\s*쓰기|트랜스크립|전사"
                       r"|목소리\s*분리|음성\s*분리|보이스\s*아이솔|voice\s*isolation|매직\s*마스크|스마트\s*리프레임"
                       r"|슈퍼\s*스케일|음성\s*생성", re.IGNORECASE)
SUBTITLE_AUTO_RE = re.compile(r"자막")
KEYFRAME_RE = re.compile(r"키\s*프레임|\bEQ\b|이퀄|이큐|컴프레서|컴프(?!\S*터)|리미터|페이드|서서히|점점|노이즈\s*(?:제거|리덕션|줄)"
                         r"|잡음\s*(?:제거|없애|줄)|리버브|에코", re.IGNORECASE)
CUT_RE = re.compile(r"잘라|자르|잘라내|잘라\s*내|컷\s*(?:해|편집)|분할|쪼개|트림|잘리게|이어\s*붙")
MOVE_CLIP_RE = re.compile(r"(?:클립|장면|영상|구간|부분)\S*\s*(?:을|를)?\s*(?:옮겨|이동|움직여|당겨|밀어|바꿔|지워|삭제|없애)")
NEEDS_AI_RE = re.compile(r"웃음|박수|환호|말\s*실수|말실수|기침|숨\s*소리|숨소리|추임새|욕|더듬|음\s*\.\.\.|어\s*\.\.\.")
OUT_OF_SCOPE_RE = re.compile(
    r"색\s*보정|컬러\s*(?:보정|그레이딩|교정)|그레이딩|렌더|내보내|출력\s*해|업로드|유튜브에\s*올"
    r"|(?:다른\s*)?타임라인\s*(?:을|를)?\s*(?:지워|삭제|만들|없애|복사)|새\s*타임라인|프로젝트\s*(?:를|을)?\s*(?:지워|삭제|저장|닫)"
    r"|전환\s*효과|트랜지션|효과\s*(?:넣|걸)|타이틀\s*넣|텍스트\s*넣|글자\s*넣|음악\s*(?:넣|깔)|배경\s*음악|속도\s*(?:바꿔|빠르게|느리게|조절)"
    r"|배속|크롭|확대|줌\s*인|화면\s*(?:키워|줄여|바꿔)|밝게|어둡게|채도|노출")
AUDIO_NOT_READY_RE = re.compile(r"원본\s*(?:클립)?\s*(?:을|를)?\s*(?:꺼|켜|끄|켜)|트랙\s*이름|이름\s*(?:을)?\s*바꿔")

# ── 도움말·상태·되돌리기·저장 ─────────────────────────────────────────

HELP_RE = re.compile(r"뭐\s*(?:를)?\s*할\s*수\s*있|무엇을\s*할\s*수|뭘\s*할\s*수|할\s*수\s*있는\s*(?:거|것|일)|도움말|도와줘|사용법"
                     r"|어떻게\s*(?:써|쓰는|사용)|기능\s*(?:이|은)?\s*뭐|명령어|help", re.IGNORECASE)
STATUS_RE = re.compile(r"몇\s*분\s*(?:이야|이에요|이지|짜리|인가|이니|이냐)|길이\s*(?:가|는)?\s*(?:얼마|몇|어떻게)|얼마나\s*길"
                       r"|(?:지금|현재)\s*(?:재생\s*)?위치\s*(?:가|는)?\s*(?:어디|몇)|어디\s*(?:야|에요|쯤이야)|상태\s*(?:는|가)?\s*(?:어때|알려)"
                       r"|몇\s*프레임|프레임\s*(?:레이트|속도)\s*(?:는|가)?\s*(?:뭐|몇|얼마)|타임라인\s*(?:정보|이름)")
UNDO_LAST_RE = re.compile(r"방금\s*(?:거|것|꺼|넣은\s*(?:거|것|표시|마커)?)|아까\s*(?:거|것|꺼|넣은)|마지막\s*(?:거|것|꺼|으로\s*넣은)"
                          r"|직전|바로\s*전")
SAVE_SLOT_RE = re.compile(r"(?:자동화|버튼)\s*(?:버튼)?\s*([1-3])?\s*(?:번)?\s*(?:에|으로|로)?\s*(?:저장|넣어\s*둬|등록)"
                          r"|([1-3])\s*번\s*(?:버튼|자동화)\s*(?:에|으로|로)?\s*(?:저장|등록)|이대로\s*저장")
REMOVE_ALL_RE = re.compile(r"(?:도우미\s*(?:가|께서)?\s*(?:넣은|한)\s*(?:거|것|걸)?\s*(?:을|를)?\s*|도우미\s*(?:것|거)\s*(?:을|를)?\s*)"
                           r"(?:모두|전부|다)\s*(?:빼|지워|없애|삭제|치워)")
ALL_RE = re.compile(r"모두|전부|다\s+(?=지|빼|없)|다(?=지워|빼|없애)|싹|몽땅")
CONTEXT_FILLER = re.compile(r"^(?:이대로|그대로|그거|이거|방금\s*(?:거|것|꺼)|아까\s*(?:거|것))$")


def first(items: Sequence[Found], kinds: Optional[Sequence[str]] = None) -> Optional[Found]:
    for f in items:
        if kinds is None or f.kind in kinds:
            return f
    return None
