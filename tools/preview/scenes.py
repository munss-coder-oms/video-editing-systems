"""미리보기 단계: 2.1 시험 안내(guides/도우미창-2.1-시험-안내.md)의 순서대로 창을 누르고 찍는다.

단계마다 제목, 할 일, 보이는 것, (있으면) 알아 둘 것과 찾은 문제, 안내 번호, 그림. 글 속 단추와 문구는
strings_ko나 창에서 그대로 가져오고, 설명에 적은 문구가 창에 정말 있는지 expect()로 확인한다 (없으면 그 단계에서
멈춘다). [...]는 단추, '...'는 창에 보이는 문구다 (page.py가 단추 모양으로 그린다). 창의 문구 바로 뒤에는 조사를
붙이지 않는다 (문구마다 받침이 달라서): "맨 위: '...'"처럼 적는다.

- 안내의 한 줄이 창에서 여러 번 누르는 일이면 단계를 나눈다 (같은 안내 번호).
- 안내에 없는 것(연결 전, 옛 스크립트, 리졸브를 껐을 때, 새 판 받기, 작은 화면)은 따로 모았다 (안내 번호 없음).
- 미리보기에서 할 수 없는 것(소리 듣기, 리졸브의 Ctrl+Z와 자르기)은 알아 둘 것에 적는다.
- 찍으면서 잰 창의 문제(잘린 단추, 흐린 글씨 등)는 찾은 문제(issues)에 적는다.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import Qt

from app.companion import strings_ko as S
from app.companion.cards import ClearCard, ProposalCard, QuestionCard
from app.companion.voice_picker import VoicePickerCard
from engine.resolve_link import SCRIPT_NAME
from engine.settings import TEXT_SCALES

from .director import Director, Image
from .world import TEST_NAME

SECTIONS = (
    ("connect", "연결과 기능 점검"),
    ("pauses", "버튼 1 · 쉬는 곳 표시"),
    ("spikes", "버튼 2 · 튀는 소리 표시"),
    ("chat", "대화 칸"),
    ("undo", "모두 빼기와 결과 저장"),
    ("trouble", "이럴 때는"),
    ("small", "작은 화면"),
)
GEAR = f"{S.SLOT_SETTINGS}︎"  # 톱니바퀴를 그림 글자가 아닌 글자로
STRIP_NOTE = "아래 줄은 리졸브 화면이 아니라, 가짜 리졸브가 받은 표시를 그린 흉내 그림이에요."
TEMP_PATH_NOTE = "그림 속 파일 경로는 미리보기가 쓴 임시 폴더예요 (윈도우에서는 바탕 화면 경로가 보여요)."


@dataclass
class Step:
    do: str
    see: str
    images: List[Image]
    note: str = ""
    issues: List[str] = field(default_factory=list)  # 찍으면서 찾은 창의 문제


@dataclass
class Scene:
    key: str
    section: str
    guide: Optional[int]
    title: str
    run: Callable[["Flow"], Step]
    media: bool = True  # 녹화 파일(소리 계산)이 있어야 하는 단계
    subprocess: bool = False  # 다른 화면 배율로 따로 띄워 찍는 단계


SCENES: List[Scene] = []


def scene(key: str, section: str, guide: Optional[int], title: str, *, media: bool = True,
          subprocess: bool = False):
    def deco(fn: Callable[["Flow"], Step]) -> Callable[["Flow"], Step]:
        SCENES.append(Scene(key, section, guide, title, fn, media, subprocess))
        return fn
    return deco


def btn(label: str) -> str:
    return f"[{label}]"


def q(text: str) -> str:
    """창에 보이는 문구를 따옴표로 (문구 안에 작은따옴표가 있으면 큰따옴표로). 여러 줄은 ' / '로 잇는다."""
    text = " / ".join(line.strip() for line in str(text).splitlines() if line.strip())
    return f'"{text}"' if "'" in text else f"'{text}'"


def buttons_of(labels: List[str]) -> str:
    return " ".join(btn(b) for b in labels)


def batchim(word: str) -> Optional[bool]:
    """마지막 글자에 받침이 있는지 (한글과 숫자만 안다, 모르면 None). 따옴표·괄호는 건너뛴다."""
    ch = word.rstrip("'\" )")[-1:]
    if "가" <= ch <= "힣":
        return (ord(ch) - 0xAC00) % 28 != 0
    if ch.isdigit():
        return ch in "013678"  # 영 일 삼 육 칠 팔
    return None


def particle_slip(text: str, word: str) -> Optional[str]:
    """창 문구에서 word 바로 뒤의 을/를이 받침과 맞지 않으면 찾은 문제 한 줄 (맞으면 None)."""
    has = batchim(word)
    if has is None:
        return None
    right, wrong = ("을", "를") if has else ("를", "을")
    if word + wrong not in text:
        return None
    return f"창 문구 {q(text)}: {q(word)} 뒤에는 '{wrong}' 대신 '{right}'이 맞아요."


class Flow:
    """단계 사이에 넘기는 것 (카드, 수)과 자주 쓰는 확인."""

    def __init__(self, d: Director) -> None:
        self.d = d
        self.w = d.w
        self.world = d.world
        self.found: Dict[str, Any] = {}
        self.played: List[Any] = []
        # 버튼 1의 첫 계산을 "소리 꺼내는 중"에서 잠깐 세워 진행 줄을 찍는다 (소리 계산이 빨라서)
        self.gate = threading.Event()
        self.gate.set()
        self.gate_waiting = threading.Event()
        real = self.w.runs.probe_file

        def gated(path):
            if not self.gate.is_set():
                self.gate_waiting.set()
                self.gate.wait(60)
            return real(path)

        self.w.runs.probe_file = gated
        # 다른 화면 배율로 따로 띄워 찍는 일 (build.py가 넣는다): scaled(alt) → Image
        self.scaled: Optional[Callable[[str], Image]] = None

    # 창 안의 것
    def cards(self, cls) -> List[Any]:
        return [c for c in self.w.runs.cards if type(c) is cls]

    def quiet(self) -> bool:
        w = self.w
        return not w.busy and w.pending == 0 and not w.chat_flow.active

    def new_card(self, cls, before: int, what: str, *, title: Optional[str] = None, timeout: float = 90.0):
        def ready():
            found = [c for c in self.cards(cls)[before:] if title is None or c.title.text() == title]
            return found[-1] if found and self.quiet() else None

        card = self.d.wait(ready, what, timeout)
        self.d.idle()
        return card

    def slot(self, n: int):
        return self.w.automation.button(n)

    def slot_lines(self) -> str:
        """자동화 버튼 아래 줄을 같은 것끼리 묶어서: 버튼 1·2 아래: '…'. 버튼 3 아래: '…'."""
        groups: Dict[str, List[str]] = {}
        for i, b in enumerate(self.w.automation.buttons, start=1):
            if b.isVisible():
                groups.setdefault(b.receipt.text(), []).append(str(i))
        return " ".join(f"버튼 {'·'.join(ns)} 아래: {q(text)}." for text, ns in groups.items())

    def log_last(self) -> str:
        """연결 점검 쪽 기록 칸의 마지막 줄."""
        lines = [ln for ln in self.w.log_view.toPlainText().splitlines() if ln.strip()]
        return lines[-1] if lines else ""

    def inside(self, widget) -> bool:
        """창 안에 다 보이는지 (아래나 옆으로 잘리지 않음)."""
        top = widget.mapTo(self.w, widget.rect().topLeft())
        return widget.isVisible() and self.w.rect().contains(widget.rect().translated(top))

    def screen_text(self) -> str:
        """창에 적힌 글 (머리말, 버튼, 대화 칸, 쪽)."""
        w = self.w
        parts = [w.status.text(), w.header.hint.text(), w.header.summary.text(), w.message.text()]
        for b in w.automation.buttons:
            parts += [b.title.text(), b.summary.text(), b.receipt.text()]
        parts += [w.automation.progress.label.text(), w.chat.log.toPlainText(), w.log_view.toPlainText()]
        return "\n".join(parts)

    def expect(self, text: str, *needles: str) -> None:
        missing = [n for n in needles if n not in text]
        if missing:
            raise self.d.fail("설명에 적은 문구가 창에 없음: " + " / ".join(missing))

    def on_panel(self) -> None:
        self.d.wait(lambda: self.w.stack.currentWidget() is self.w.panel, "도우미 창 첫 쪽")

    def receipt_shot(self, card, alt: str, strip_alt: str) -> List[Image]:
        """영수증 카드가 보이게 굴려서 창, 카드가 잘렸으면 카드만, 그리고 타임라인 흉내."""
        images = self.card_shot(card, alt)
        images.append(self.d.strip(strip_alt))
        return images

    def card_shot(self, card, alt: str) -> List[Image]:
        self.d.show_in_chat(card)
        self.d.check_clipped(card)
        images = [self.d.shot(alt)]
        if self.d.card_cut(card):
            images.append(self.d.widget_shot(card, alt + " (카드만)"))
        return images

    def apply(self, card, what: str, timeout: float = 60.0) -> None:
        self.d.press(card.buttons.get("apply"), f"{what} {btn(S.BTN_APPLY)}")
        self.d.wait(lambda: card.state == "receipt" and self.quiet(), f"{what} 영수증", timeout)
        self.d.idle()

    def chat(self, text: str, cls, what: str, *, title: Optional[str] = None, timeout: float = 90.0):
        before = len(self.cards(cls))
        self.d.type_send(text)
        return self.new_card(cls, before, what, title=title, timeout=timeout)


# ── 연결과 기능 점검 ─────────────────────────────────────────────────

@scene("before", "connect", None, "스크립트를 누르기 전", media=False)
def s_before(f: Flow) -> Step:
    w = f.w
    image = None
    for _ in range(5):
        # 2초마다 묻는 동안은 잠깐 '확인하는 중'이 보여서, 찍은 뒤에도 그대로인지 본다
        f.d.wait(lambda: w.status.text() == S.STATUS_NOT_CONNECTED and f.quiet(), "연결 안 됨 표시")
        image = f.d.shot("연결 안 됨: 스크립트를 누르라는 안내")
        if w.status.text() == S.STATUS_NOT_CONNECTED:
            break
    f.expect(f.screen_text(), S.STATUS_NOT_CONNECTED, S.CONNECT_HINT, S.SLOT_DISABLED_NOT_CONNECTED)
    return Step(
        do=f"도우미 창을 켠 채, 리졸브에서 아직 {SCRIPT_NAME}를 누르지 않은 때예요.",
        see=(f"맨 위: {q(S.DOT_OFF + ' ' + S.STATUS_NOT_CONNECTED)}. 안내: {q(S.CONNECT_HINT)}. "
             + f.slot_lines()),
        images=[image],
        note="창은 2초마다 리졸브에 물어봐요. 스크립트를 누르면 저절로 연결돼요.",
    )


@scene("connected", "connect", 1, "스크립트를 눌러 연결", media=False)
def s_connected(f: Flow) -> Step:
    w = f.w
    f.world.fake.online = True
    f.d.wait(lambda: w.connected and "Timeline 1" in w.header.summary.text(), "연결됨", 15)
    f.d.idle()
    summary = w.header.summary.text()
    f.expect(f.screen_text(), S.STATUS_CONNECTED, S.CHAT_TRY_CONNECTED, *S.CHIPS_AFTER_CONNECT)
    f.expect(summary, "7:00", "소리 트랙 5개")
    images = [f.d.shot("연결됨: 타임라인 요약과 예문 칩"),
              f.d.strip("리졸브 타임라인 흉내: 처음 상태 (직접 찍은 표시와 지난번 시험 표시)")]
    return Step(
        do=f"리졸브에서 Workspace → Scripts → {SCRIPT_NAME}",
        see=(f"맨 위: {q(S.DOT_CONNECTED + ' ' + S.STATUS_CONNECTED)}. 요약: {q(summary)}. "
             f"대화 칸: {q(S.CHAT_TRY_CONNECTED)}, 그 아래 예문 칩 {len(S.CHIPS_AFTER_CONNECT)}개."),
        images=images,
        note=("미리보기 타임라인은 7분짜리예요 (창에는 7:00). 안내서의 '18분 46초'는 창에서 18:46 모양으로 "
              "적혀요. 소리 트랙 5개는 녹화의 소리 4개와 지난번 시험 트랙 'AI 도우미 시험'이에요. "
              + STRIP_NOTE + " 직접 찍은 표시 2개(초록, 파랑)와 지난번 시험의 노란 표시 2개가 있어요."),
    )


@scene("more-menu", "connect", 2, "⋯ 메뉴 열기", media=False)
def s_more_menu(f: Flow) -> Step:
    w = f.w
    labels = [a.text() for a in w.more_menu.actions() if a.text()]
    image = f.d.menu(w.header.more_btn, w.more_menu, S.MENU_CHECK_PAGE, "⋯ 메뉴")
    f.expect(" ".join(labels), S.MENU_CHECK_PAGE, S.MENU_REPORT, S.MENU_SETTINGS, S.MENU_HELP, S.MENU_UPDATE)
    if not w.update_action.isEnabled():
        raise f.d.fail(f"메뉴 줄이 꺼져 있음: {S.MENU_UPDATE}")
    return Step(
        do=f"창 오른쪽 위 {btn(S.BTN_MORE)} → {S.MENU_CHECK_PAGE}",
        see=(f"메뉴 줄: {S.MENU_CHECK_PAGE} · {S.MENU_REPORT} · {S.MENU_SETTINGS} ▸ · {S.MENU_HELP}, "
             f"가는 줄 아래 {S.MENU_UPDATE}"),
        images=[image],
        note=f"{S.MENU_UPDATE}는 이번 판에 새로 생긴 줄이에요 ('이럴 때는'의 새 판 단계에 따로 찍었어요).",
    )


@scene("check-page", "connect", 2, "연결 점검 쪽", media=False)
def s_check_page(f: Flow) -> Step:
    w = f.w
    f.d.wait(lambda: w.stack.currentWidget() is w.check_page, "연결 점검 쪽")
    f.d.wait(lambda: w.probe_btn.isEnabled(), f"{btn(S.BTN_PROBE)}이 켜지기")
    f.d.settle(0.2)
    return Step(
        do=f"메뉴에서 {S.MENU_CHECK_PAGE}을 누르면 이 쪽으로 바뀌어요.",
        see=(f"맨 위: {btn(S.BTN_BACK)}, 제목 {q(S.CHECK_TITLE)}. 파란 {btn(S.BTN_PROBE)}, 예전 시험 도구 "
             f"{btn(S.BTN_TEST_MARKER)} {btn(S.BTN_TEST_AUDIO)} {btn(S.BTN_TEST_CLEANUP)}, "
             f"{btn(S.BTN_MANUAL_CHECKS)}, 그 아래 한 일 기록 칸."),
        images=[f.d.shot("연결 점검 쪽")],
    )


@scene("probe-confirm", "connect", 2, "기능 점검 시작")
def s_probe_confirm(f: Flow) -> Step:
    w = f.w
    f.world.probe_hold = "C4"
    image = f.d.dialog(lambda: f.d.press(w.probe_btn, btn(S.BTN_PROBE)), S.BTN_START, "기능 점검을 묻는 창")
    dlg = f.d.last_dialog
    f.expect(dlg["text"], S.PROBE_CONFIRM)
    return Step(
        do=f"{btn(S.BTN_PROBE)} → 묻는 창에서 {btn(S.BTN_START)}",
        see=f"묻는 창 제목: {q(dlg['title'])}. 글: {q(dlg['text'])}. 단추: {buttons_of(dlg['buttons'])}.",
        images=[image],
        note="묻는 창의 제목 줄은 윈도우가 그려요. 미리보기에서는 제목만 담은 줄로 그렸어요.",
    )


@scene("probe-running", "connect", 2, "기능 점검 도는 중")
def s_probe_running(f: Flow) -> Step:
    w, world = f.w, f.world
    running = S.PROBE_RUNNING.format(stage="")
    f.d.wait(lambda: world.probe_waiting.is_set(), "기능 점검이 4번째 점검에 닿기", 30)
    f.d.wait(lambda: w.message.text().startswith(running) and w.message.text() != running, "단계 이름", 10)
    f.d.settle(0.3)
    last = f.log_last()
    page = w.check_page
    if any(b.isEnabled() for b in (page.marker_btn, page.audio_btn, page.cleanup_btn)):
        raise f.d.fail("기능 점검이 도는 동안 예전 시험 도구 단추가 켜져 있음")
    f.expect(last, S.RESULT_OK)
    image = f.d.shot("기능 점검 도는 중")
    world.probe_release.set()
    return Step(
        do="기다려요. 리졸브는 건드리지 않아요.",
        see=(f"기록 칸에 점검마다 한 줄씩 쌓여요 (지금 마지막 줄: {q(last)}). 도는 동안 예전 시험 도구 "
             "단추는 흐리게 꺼져요."),
        images=[image],
        note=("실제로는 1~2분 걸리고, 그동안 리졸브에 점검용 복사 타임라인이 잠깐 생겼다가 지워져요. "
              "미리보기에서는 도는 모습을 찍으려고 4번째 점검 앞에서 잠깐 세웠어요."),
    )


@scene("probe-done", "connect", 2, "기능 점검 끝")
def s_probe_done(f: Flow) -> Step:
    w = f.w
    done = S.PROBE_DONE.split("{")[0]
    f.d.wait(lambda: done in w.message.text(), "기능 점검 끝", 60)
    f.d.idle()
    last = f.log_last()
    f.expect(last, done)
    return Step(
        do="끝날 때까지 기다려요.",
        see=f"기록 칸 마지막 줄: {q(last)}",
        images=[f.d.shot("기능 점검을 마침")],
        note=("가짜 리졸브는 모든 점검에 '됨'으로 답해요. 실제 결과는 리졸브 판에 따라 다를 수 있어요. 기록 칸의 "
              "'결과 저장' 줄은 점검 전에 결과 파일을 먼저 저장해 둔 거예요. " + TEMP_PATH_NOTE),
    )


# ── 버튼 1: 쉬는 곳 표시 ─────────────────────────────────────────────

@scene("slot1-run", "pauses", 3, "버튼 1 누르기")
def s_slot1_run(f: Flow) -> Step:
    w = f.w
    f.d.press(w.check_page.back_btn, btn(S.BTN_BACK))
    f.on_panel()
    f.gate.clear()
    f.d.press(f.slot(1), f"버튼 1 '{S.SLOT_DEFAULT_NAMES[1]}'")
    f.d.wait(lambda: f.gate_waiting.is_set() and w.automation.progress.isVisible(), "진행 줄", 30)
    f.d.settle(0.3)
    progress = w.automation.progress.label.text()
    f.expect(f.slot(2).receipt.text(), S.SLOT_DISABLED_BUSY)
    where = "진행 줄" if f.slot(1).isVisible() else "버튼 1 자리에 진행 줄"
    image = f.d.shot("버튼 1 계산 중: 진행 줄")
    f.gate.set()
    return Step(
        do=f"{btn(S.BTN_BACK)} → 버튼 1 '{S.SLOT_DEFAULT_NAMES[1]}'",
        see=f"{where}: {q(progress)} {btn(S.BTN_STOP)}. 버튼 2 아래: {q(S.SLOT_DISABLED_BUSY)}.",
        images=[image],
        note=("계산은 이 PC에서 녹화 파일만 읽어요 (리졸브에는 아직 아무것도 넣지 않아요). 7분짜리 미리보기 "
              "녹화는 몇 초면 끝나서 첫 단계에서 잠깐 세워 찍었어요."),
    )


@scene("voice-card", "pauses", 3, "목소리 고르기 카드")
def s_voice_card(f: Flow) -> Step:
    card = f.new_card(VoicePickerCard, 0, "목소리 고르기 카드", timeout=180)
    f.found["voice"] = card
    text = card.plain_text()
    n = len(card.question.streams)
    lines = [S.VOICE_INTRO.format(n=n)]
    if card.question.mix is not None:
        lines.append(S.VOICE_MIX.format(n=card.question.mix + 1))
    if S.DOUBLED_VOICE in text:
        lines.append(S.DOUBLED_VOICE)
    f.expect(text, S.VOICE_TITLE, *lines)
    return Step(
        do="계산이 끝나기를 기다려요 (처음 한 번만 묻는 카드예요).",
        see=(f"카드 {q(S.VOICE_TITLE)}: " + " ".join(q(t) for t in lines)
             + f". 소리마다 {btn(S.BTN_LISTEN)} {btn(S.BTN_PICK)}, 맨 아래 {btn(S.BTN_CANCEL)}."),
        images=f.card_shot(card, "목소리 고르기 카드"),
        note=(f"미리보기 녹화는 OBS처럼 소리 {n}개예요: 소리 1은 모두 섞은 것, 소리 2는 목소리, "
              "소리 3은 게임, 소리 4는 음악."),
    )


@scene("listen", "pauses", 4, "3초 듣기")
def s_listen(f: Flow) -> Step:
    w = f.w
    card = f.found["voice"]
    listening = S.LISTENING.format(n=2)
    f.d.press(card.listen_buttons[1], f"소리 2 {btn(S.BTN_LISTEN)}")
    f.d.wait(lambda: w.message.text() == listening, "소리를 트는 중 알림", 30)
    f.d.idle()
    slip = particle_slip(listening, S.VOICE_STREAM.format(n=2))
    return Step(
        do=f"소리마다 {btn(S.BTN_LISTEN)} (여기서는 소리 2)",
        see=f"맨 위 알림: {q(listening)}. 소리마다 3초씩 들려요.",
        images=f.card_shot(card, "소리 2를 트는 중"),
        note=("미리보기에서는 소리가 나지 않아요 (틀기 직전에 막아 두었어요). 3초를 꺼내는 일까지는 진짜로 해요. "
              "소리를 트는 동안에도 리졸브에는 아무것도 묻지 않아요."),
        issues=[slip] if slip else [],
    )


@scene("voice-pick", "pauses", 5, "목소리 고르기")
def s_voice_pick(f: Flow) -> Step:
    card = f.found["voice"]
    before = len(f.cards(ProposalCard))
    f.d.press(card.pick_buttons[1], f"소리 2 {btn(S.BTN_PICK)}")
    pc = f.new_card(ProposalCard, before, "쉬는 곳 카드", timeout=180)
    p = pc.proposal
    resolve = S.RESOLVE_RANGE.format(color_word=S.COLOR_WORDS["Blue"], n=p.count)
    voice = S.VOICE_ROW.format(n=2, why=S.VOICE_JUST_PICKED)
    f.expect(pc.plain_text(), S.CARD_TITLE_FOUND, resolve, voice)
    f.expect(card.plain_text(), S.VOICE_PICKED.format(n=2))
    f.found["pause1"] = pc
    return Step(
        do=f"목소리인 소리 옆 {btn(S.BTN_PICK)} (여기서는 소리 2)",
        see=(f"카드 {q(S.CARD_TITLE_FOUND)}. 리졸브: {q(resolve)}. 목소리: {q(voice)}. "
             f"맨 아래 {btn(S.BTN_APPLY)} {btn(S.BTN_CANCEL)}."),
        images=f.card_shot(pc, "쉬는 곳 확인 카드"),
        note="이 카드까지는 리졸브에 아무것도 넣지 않았어요. 다음부터는 목소리를 묻지 않아요.",
    )


@scene("pause1-apply", "pauses", 6, "리졸브에 넣기")
def s_pause1_apply(f: Flow) -> Step:
    pc = f.found["pause1"]
    f.apply(pc, "쉬는 곳 카드")
    title = pc.title.text()
    receipt = f.slot(1).receipt.text()
    n = pc.outcome.placed
    f.found["pause1_n"] = n
    f.expect(receipt, S.SLOT_RECEIPT.split("{")[0])
    if len(f.world.ours("Blue")) != n:
        raise f.d.fail(f"가짜 리졸브의 파란 표시 수가 영수증과 다름 ({len(f.world.ours('Blue'))}, {n})")
    return Step(
        do=btn(S.BTN_APPLY),
        see=(f"카드가 영수증으로 바뀌어요: {q(title)}, 맨 아래 {btn(S.BTN_UNDO_ONE)}. 버튼 1 아래: {q(receipt)}. "
             f"흉내 그림: 쉬는 곳마다 파란 구간 표시 {n}개."),
        images=f.receipt_shot(pc, "쉬는 곳 표시를 넣은 영수증", f"리졸브 타임라인 흉내: 파란 구간 표시 {n}개"),
        note=STRIP_NOTE + " 도우미 표시는 채운 깃발, 직접 찍은 표시는 속이 빈 깃발로 그렸어요.",
    )


@scene("m3", "pauses", 7, "Ctrl+Z 질문 카드")
def s_m3(f: Flow) -> Step:
    card = f.w.runs.manual_cards.get("M3")
    if card is None:
        raise f.d.fail("Ctrl+Z 질문 카드가 뜨지 않음")
    f.expect(card.plain_text(), S.M3_QUESTION, S.M3_STEP, *S.M3_ANSWERS.values())
    f.expect(f.screen_text(), S.CHAT_TRY_AFTER_APPLY, *S.CHIPS_AFTER_APPLY)
    answers = " ".join(btn(a) for a in S.M3_ANSWERS.values())
    images = f.card_shot(card, "Ctrl+Z 질문 카드")
    # 이 카드는 단추가 한 줄이라 창 너비 420에서 대화 칸보다 넓다: 잰 값으로 찾은 문제에 적는다 (일반 알림 대신)
    f.d.notes.clear()
    later = card.buttons.get("later")
    over = f.d.chat_overflow()
    issues = []
    if later is not None and over is not None and f.d.hidden_part(later) > 0:
        _, need, view = over
        issues.append(
            f"창 너비 {f.w.width()}에서는 이 카드의 단추 {len(card.buttons)}개가 한 줄에 다 들어가지 않아요 "
            f"(카드 최소 {need}픽셀, 대화 칸 {view}픽셀). 대화 칸은 옆으로 굴릴 수 없어서 {btn(S.BTN_LATER)}가 "
            "오른쪽에서 잘려요. 이 카드가 대화 칸에 남아 있는 동안은 대화 칸 전체가 넓어져서, 뒤에 오는 카드도 "
            f"오른쪽이 잘려요 (목록의 {btn(S.BTN_VIEW_SHORT)}, 저장 줄의 {btn(S.BTN_SAVE)} 등).")
    return Step(
        do="(질문 카드가 뜨면) 리졸브 타임라인의 빈 곳 클릭 → Ctrl+Z 한 번 → 본 대로 고르기",
        see=f"카드 {q(S.M3_QUESTION)}, 안내 {q(S.M3_STEP)}. 단추: {answers} {btn(S.BTN_LATER)}",
        images=images,
        note=("미리보기에서는 리졸브의 Ctrl+Z를 해 볼 수 없어서 답하지 않고 두었어요. 넣은 뒤에는 대화 칸에 "
              f"{q(S.CHAT_TRY_AFTER_APPLY)}, 그 아래 예문 칩도 나와요."),
        issues=issues,
    )


@scene("slot1-gear", "pauses", 8, f"버튼 1 {GEAR} 쉰 길이 2초")
def s_slot1_gear(f: Flow) -> Step:
    w = f.w
    f.d.press(w.automation.gears[0], f"버튼 1 {S.SLOT_SETTINGS}")
    page = w.settings_page
    f.d.wait(lambda: w.stack.currentWidget() is page and "min_s" in page.fields, "버튼 1 설정 쪽")
    spin = page.fields["min_s"]
    start = spin.value()
    page.scroll.ensureWidgetVisible(spin)
    steps = int(round((2.0 - start) / spin.singleStep()))
    f.d.keys(spin, Qt.Key_Up, steps)
    if abs(spin.value() - 2.0) > 1e-6:
        raise f.d.fail(f"쉰 길이가 2초가 되지 않음 ({spin.value()})")
    preview = page.preview.text()
    f.expect(preview, "2초")
    return Step(
        do=f"버튼 1 옆 {GEAR} → '{S.PARAM_LABELS['min_s']}' 칸의 ▲를 눌러 2.0초로",
        see=(f"설정 쪽 제목: {page.title.text()}. {S.PARAM_LABELS['min_s']}: {spin.text()}. "
             f"아래 요약 줄: {q(preview)}. 맨 아래 {btn(S.BTN_CANCEL)} {btn(S.BTN_SAVE)}."),
        images=[f.d.shot("버튼 1 설정 쪽: 쉰 길이 2초")],
        note=f"처음 값은 {start:g}초예요. 칸에 숫자를 바로 적어도 돼요.",
    )


@scene("slot1-saved", "pauses", 8, "설정 저장")
def s_slot1_saved(f: Flow) -> Step:
    w = f.w
    f.d.press(w.settings_page.save_btn, btn(S.BTN_SAVE))
    f.on_panel()
    f.d.idle()
    summary = f.slot(1).summary.text()
    saved = S.SETTINGS_SAVED.format(name=S.SLOT_DEFAULT_NAMES[1])
    f.expect(w.message.text(), saved)
    return Step(
        do=btn(S.BTN_SAVE),
        see=f"첫 쪽으로 돌아와요. 버튼 1 요약: {q(summary)}. 맨 위 알림: {q(saved)}.",
        images=[f.d.shot("버튼 1 요약이 바뀜")],
        note=f"안내서 8번의 요약 '2초 넘게 쉰 곳 표시'는 창에서 조금 달라요: {q(summary)}.",
    )


@scene("slot1-rerun", "pauses", 9, "버튼 1 다시 누르기")
def s_slot1_rerun(f: Flow) -> Step:
    before = len(f.cards(QuestionCard))
    f.d.press(f.slot(1), "버튼 1")
    n = f.found["pause1_n"]
    question = S.RERUN_QUESTION.format(color_word=S.COLOR_WORDS["Blue"], n=n)
    card = f.new_card(QuestionCard, before, "바꿀지 묻는 카드", title=question, timeout=120)
    f.found["rerun"] = card
    return Step(
        do="버튼 1",
        see=f"카드 {q(question)}. 단추: {btn(S.BTN_REPLACE)} {btn(S.BTN_ADD_MORE)}",
        images=f.card_shot(card, "이전 표시를 바꿀지 묻는 카드"),
        note="같은 버튼으로 넣은 표시가 타임라인에 남아 있으면 먼저 이렇게 물어요.",
    )


@scene("slot1-replace", "pauses", 9, "바꾸기")
def s_slot1_replace(f: Flow) -> Step:
    card = f.found["rerun"]
    before = len(f.cards(ProposalCard))
    f.d.press(card.buttons.get("replace"), btn(S.BTN_REPLACE))
    pc = f.new_card(ProposalCard, before, "새 쉬는 곳 카드")
    n_old = f.found["pause1_n"]
    what = S.WHAT_PAUSES.format(min_s=2)
    replace = S.RESOLVE_REPLACE.format(color_word=S.COLOR_WORDS["Blue"], n=n_old)
    resolve = S.RESOLVE_RANGE.format(color_word=S.COLOR_WORDS["Blue"], n=pc.proposal.count)
    f.expect(pc.plain_text(), what, resolve, replace)
    f.found["pause2"] = pc
    return Step(
        do=btn(S.BTN_REPLACE),
        see=f"새 카드. 할 일: {q(what)}. 리졸브: {q(resolve)}, {q(replace)}.",
        images=f.card_shot(pc, "2초 설정으로 다시 찾은 카드"),
    )


@scene("pause2-apply", "pauses", 9, "다시 넣기")
def s_pause2_apply(f: Flow) -> Step:
    pc = f.found["pause2"]
    f.apply(pc, "새 쉬는 곳 카드")
    n_old, n = f.found["pause1_n"], pc.outcome.placed
    if n >= n_old:
        raise f.d.fail(f"2초 설정의 표시 수({n})가 처음({n_old})보다 적지 않음")
    if len(f.world.ours("Blue")) != n:
        raise f.d.fail(f"가짜 리졸브의 파란 표시 수가 영수증과 다름 ({len(f.world.ours('Blue'))}, {n})")
    replaced = S.RECEIPT_REPLACED.format(color_word=S.COLOR_WORDS["Blue"], n=n_old)
    f.expect(pc.plain_text(), replaced)
    return Step(
        do=btn(S.BTN_APPLY),
        see=(f"영수증 {q(pc.title.text())}, {q(replaced)}. 흉내 그림: 파란 표시 {n_old}개가 빠지고 {n}개 "
             "(안내 6번 때보다 적음)."),
        images=f.receipt_shot(pc, "2초 설정 영수증", f"리졸브 타임라인 흉내: 파란 표시 {n}개 (전 것은 빠짐)"),
        note=STRIP_NOTE,
    )


# ── 버튼 2: 튀는 소리 표시 ───────────────────────────────────────────

@scene("slot2-card", "spikes", 10, "버튼 2 누르기")
def s_slot2_card(f: Flow) -> Step:
    before = len(f.cards(ProposalCard))
    f.d.press(f.slot(2), f"버튼 2 '{S.SLOT_DEFAULT_NAMES[2]}'")
    pc = f.new_card(ProposalCard, before, "튀는 소리 카드", timeout=120)
    p = pc.proposal
    if p.count == 0:
        raise f.d.fail("튀는 소리를 찾지 못함 (미리보기 녹화에는 4곳이 있음)")
    resolve = S.RESOLVE_RANGE.format(color_word=S.COLOR_WORDS["Red"], n=p.count)
    voice = S.VOICE_ROW.format(n=2, why=S.VOICE_REMEMBERED)
    f.expect(pc.plain_text(), S.CARD_TITLE_FOUND, resolve, voice)
    f.found["spikes"] = pc
    return Step(
        do=f"버튼 2 '{S.SLOT_DEFAULT_NAMES[2]}'",
        see=f"목소리를 다시 묻지 않고 카드 {q(S.CARD_TITLE_FOUND)}. 리졸브: {q(resolve)}. 목소리: {q(voice)}.",
        images=f.card_shot(pc, "튀는 소리 확인 카드"),
    )


@scene("spikes-apply", "spikes", 10, "리졸브에 넣기")
def s_spikes_apply(f: Flow) -> Step:
    pc = f.found["spikes"]
    f.apply(pc, "튀는 소리 카드")
    n = pc.outcome.placed
    if len(f.world.ours("Red")) != n:
        raise f.d.fail(f"가짜 리졸브의 빨간 표시 수가 영수증과 다름 ({len(f.world.ours('Red'))}, {n})")
    f.expect(f.screen_text(), S.M2_QUESTION)
    return Step(
        do=btn(S.BTN_APPLY),
        see=(f"영수증 {q(pc.title.text())}. 흉내 그림: 빨간 표시 {n}개가 더해져요. 대화 칸 아래에 자르기 질문 "
             "카드도 떠요 (안내 21번)."),
        images=f.receipt_shot(pc, "튀는 소리 영수증", f"리졸브 타임라인 흉내: 빨간 표시 {n}개 더해짐"),
        note=STRIP_NOTE,
    )


@scene("view", "spikes", 11, "목록의 보기")
def s_view(f: Flow) -> Step:
    w, world = f.w, f.world
    pc = f.found["spikes"]
    before = world.info.get("current_tc")
    key = "view:1"
    f.d.press(pc.extra.get(key), f"두 번째 줄 {btn(S.BTN_VIEW_SHORT)}")
    done = S.CHAT_JUMP_DONE.split("{")[0]
    f.d.wait(lambda: w.message.text().startswith(done), "재생 위치를 옮김", 20)
    f.d.idle()
    message = w.message.text()
    after = world.info.get("current_tc")
    if after == before:
        raise f.d.fail("재생 위치가 바뀌지 않음")
    images = [f.d.shot("재생 위치를 옮김"), f.d.strip(f"리졸브 타임라인 흉내: 재생 위치 {after}")]
    return Step(
        do=f"그 카드 목록의 한 줄 옆 {btn(S.BTN_VIEW_SHORT)} (여기서는 두 번째 줄)",
        see=f"맨 위 알림: {q(message)}. 흉내 그림: 재생 위치 {before} → {after}.",
        images=images,
        note="편집이 아니라서 카드나 되돌리기 목록에 남지 않아요. " + STRIP_NOTE,
    )


# ── 대화 칸 ───────────────────────────────────────────────────────

@scene("chat-mark", "chat", 12, "3분 20초에 빨간 표시")
def s_chat_mark(f: Flow) -> Step:
    text = "3분 20초에 빨간 표시해줘"
    pc = f.chat(text, ProposalCard, "표시 카드")
    f.expect(pc.plain_text(), S.CARD_TITLE_PROPOSE, "3:20.0", "빨간 표시 1개")
    f.found["mark"] = pc
    return Step(
        do=f"대화 칸에 \"{text}\" → Enter",
        see=(f"카드 {q(S.CARD_TITLE_PROPOSE)}. 언제: '3:20.0'. 리졸브: 빨간 표시 1개. "
             f"{btn(S.BTN_APPLY)} {btn(S.BTN_CANCEL)}."),
        images=f.card_shot(pc, "3분 20초 표시 카드"),
        note=f"대화 칸 맨 아래 {q(S.CHAT_BRAIN_LINE)}. 이번 시험의 대화는 사용량을 쓰지 않아요.",
    )


@scene("chat-mark-apply", "chat", 12, "리졸브에 넣기")
def s_chat_mark_apply(f: Flow) -> Step:
    pc = f.found["mark"]
    f.apply(pc, "표시 카드")
    fps = float(f.world.info["fps"])
    if not any(abs(fr - 200 * fps) <= 1 for fr in f.world.ours("Red")):
        raise f.d.fail(f"3분 20초 자리에 빨간 표시가 없음 ({sorted(f.world.ours('Red'))})")
    return Step(
        do=btn(S.BTN_APPLY),
        see=f"영수증 {q(pc.title.text())}. 흉내 그림: 3분 20초 자리에 빨간 표시.",
        images=f.receipt_shot(pc, "3분 20초 표시 영수증", "리졸브 타임라인 흉내: 3분 20초에 빨간 표시"),
        note=STRIP_NOTE,
    )


@scene("chat-clear", "chat", 13, "도우미 파란 표시 지우기")
def s_chat_clear(f: Flow) -> Step:
    text = "도우미가 넣은 파란 표시 지워줘"
    card = f.chat(text, ClearCard, "지우기 카드")
    n = card.proposal.count
    f.expect(card.plain_text(), S.CARD_TITLE_CLEAR, S.CLEAR_RESOLVE.format(n=n), S.CLEAR_KEEP)
    f.found["clear"] = card
    return Step(
        do=f"\"{text}\" → Enter",
        see=(f"카드 {q(S.CARD_TITLE_CLEAR)}: {q(S.CLEAR_RESOLVE.format(n=n))}, {q(S.CLEAR_KEEP)}. "
             f"{btn(S.BTN_APPLY)} {btn(S.BTN_CANCEL)}."),
        images=f.card_shot(card, "도우미 파란 표시 지우기 카드"),
    )


@scene("chat-clear-apply", "chat", 13, "리졸브에 넣기 (지우기)")
def s_chat_clear_apply(f: Flow) -> Step:
    card = f.found["clear"]
    f.apply(card, "지우기 카드")
    user_blue = [m for m in f.world.user_markers().values() if m.get("color") == "Blue"]
    if f.world.ours("Blue") or not user_blue:
        raise f.d.fail("도우미 파란 표시만 없어지지 않음")
    return Step(
        do=btn(S.BTN_APPLY),
        see=(f"영수증 {q(card.title.text())}. 흉내 그림: 도우미가 넣은 파란 표시만 없어지고, 직접 찍은 파란·초록 "
             "표시는 그대로예요."),
        images=f.receipt_shot(card, "지우기 영수증", "리졸브 타임라인 흉내: 도우미 파란 표시가 빠짐"),
        note=STRIP_NOTE + " 속이 빈 깃발이 직접 찍은 표시예요.",
    )


@scene("chat-range", "chat", 14, "5분~6분에서 2초 넘게 쉰 곳")
def s_chat_range(f: Flow) -> Step:
    text = "5분~6분에서 2초 넘게 쉰 곳 표시해줘"
    pc = f.chat(text, ProposalCard, "구간 쉬는 곳 카드", timeout=120)
    when = S.WHEN_RANGE.format(a="5:00.0", b="6:00.0")
    resolve = S.RESOLVE_RANGE.format(color_word=S.COLOR_WORDS["Blue"], n=pc.proposal.count)
    f.expect(pc.plain_text(), S.CARD_TITLE_FOUND, when, resolve, S.PROVENANCE["said"], S.SAVE_ROW)
    hi = pc.proposal.scope["hi"]
    if any(r.start < hi < r.end for r in pc.proposal.rows):
        raise f.d.fail("6분에 걸친 쉬는 곳이 카드에 들어감")
    f.found["range"] = pc
    return Step(
        do=f"\"{text}\" → Enter",
        see=(f"카드 {q(S.CARD_TITLE_FOUND)}. 언제: {q(when)}. 리졸브: {q(resolve)}. "
             f"값마다 어디서 온 값인지 붙어요 (예: {q(S.PROVENANCE['said'])})."),
        images=f.card_shot(pc, "5분~6분 쉬는 곳 카드"),
        note="6분에 걸친 쉬는 곳은 말한 범위를 넘어서 빼요.",
    )


@scene("chat-range-apply", "chat", 14, "리졸브에 넣기")
def s_chat_range_apply(f: Flow) -> Step:
    pc = f.found["range"]
    f.apply(pc, "구간 쉬는 곳 카드")
    fps = float(f.world.info["fps"])
    lo, hi = 300 * fps, 360 * fps
    frames = list(f.world.ours("Blue"))
    if not frames or any(not (lo <= fr < hi) for fr in frames):
        raise f.d.fail(f"새 파란 표시가 5분~6분 밖에도 있음: {frames}")
    return Step(
        do=btn(S.BTN_APPLY),
        see=f"영수증 {q(pc.title.text())}. 흉내 그림: 새 파란 표시가 5분~6분 안에만 있어요.",
        images=f.receipt_shot(pc, "5분~6분 영수증", "리졸브 타임라인 흉내: 5분~6분 안에만 파란 표시"),
        note=STRIP_NOTE,
    )


@scene("save-slot", "chat", 15, "자동화 버튼 3에 저장")
def s_save_slot(f: Flow) -> Step:
    pc = f.found["range"]
    combo = pc.save_combo
    if combo is None:
        raise f.d.fail("영수증에 저장 줄이 없음")
    f.d.show_in_chat(combo)
    target = combo.findData(3)
    f.d.keys(combo, Qt.Key_Down, target - combo.currentIndex())
    if combo.currentData() != 3:
        raise f.d.fail("자동화 3을 고르지 못함")
    choice = combo.currentText()
    card_image = f.d.widget_shot(pc, "자동화 3을 고른 저장 줄", name="card")
    f.d.press(pc.extra.get("save"), btn(S.BTN_SAVE))
    f.d.idle()
    b3 = f.slot(3)
    done = S.SAVE_DONE.format(n=3)
    dropped = S.SAVE_RANGE_DROPPED.format(a="5:00.0", b="6:00.0")
    f.expect(f.w.message.text(), done)
    f.expect(f.w.chat.log.toPlainText(), done, dropped)
    f.d.chat_bottom()
    return Step(
        do=f"그 카드 아래 {q(S.SAVE_ROW)} → {q(choice)} 고르기 → {btn(S.BTN_SAVE)}",
        see=(f"맨 위 알림: {q(done)}. 버튼 3 이름: {q(b3.title.text())}, 요약: {q(b3.summary.text())}. "
             f"대화 칸 맨 아래: {q(done)}, {q(dropped)}."),
        images=[card_image, f.d.shot("버튼 3이 바뀜")],
    )


@scene("slot3-run", "chat", 16, "버튼 3 누르기")
def s_slot3_run(f: Flow) -> Step:
    q_before = len(f.cards(QuestionCard))
    p_before = len(f.cards(ProposalCard))
    f.d.press(f.slot(3), "버튼 3")

    def rerun_questions():
        return [c for c in f.cards(QuestionCard)[q_before:] if S.BTN_REPLACE in c.plain_text()]

    def next_card():
        if not f.quiet():
            return None
        if len(f.cards(ProposalCard)) > p_before:
            return "card"
        return "question" if rerun_questions() else None

    kind = f.d.wait(next_card, "버튼 3의 카드", 120)
    images: List[Image] = []
    note = ""
    if kind == "question":
        question = rerun_questions()[-1]
        images += f.card_shot(question, "버튼 3: 이전 표시를 바꿀지 묻는 카드")
        f.d.press(question.buttons.get("add"), btn(S.BTN_ADD_MORE))
        note = (f"안내서 16번에는 없지만, 안내 14번에 넣은 파란 표시가 남아 있어서 먼저 이렇게 물어요: "
                f"{q(question.title.text())}. 여기서는 {btn(S.BTN_ADD_MORE)}를 눌렀어요 (안내 17번에서 취소하니 "
                "어느 쪽이든 리졸브는 그대로예요).")
    pc = f.new_card(ProposalCard, p_before, "버튼 3 카드")
    what = S.WHAT_PAUSES.format(min_s=2)
    when = S.WHEN_WHOLE.format(length=_length(pc))
    f.expect(pc.plain_text(), what, when)
    f.found["slot3"] = pc
    images += f.card_shot(pc, "버튼 3 카드: 2초 넘게 쉰 곳, 전체")
    return Step(
        do="버튼 3",
        see=f"카드. 할 일: {q(what)}. 언제: {q(when)} (구간 없이 전체).",
        images=images,
        note=note,
    )


def _length(pc) -> str:
    from app.companion import fmt

    p = pc.proposal
    return fmt.length((p.tl_end - p.tl_start) / (p.fps or 1.0))


@scene("slot3-cancel", "chat", 17, "카드 취소")
def s_slot3_cancel(f: Flow) -> Step:
    pc = f.found["slot3"]
    f.d.press(pc.buttons.get("cancel"), btn(S.BTN_CANCEL))
    f.d.idle()
    f.expect(pc.plain_text(), S.CARD_CANCELLED)
    return Step(
        do=f"그 카드에서 {btn(S.BTN_CANCEL)}",
        see=f"카드 맨 아래: {q(S.CARD_CANCELLED)}",
        images=f.card_shot(pc, "취소한 카드"),
    )


@scene("slot3-gear", "chat", 18, f"버튼 3 {GEAR}")
def s_slot3_gear(f: Flow) -> Step:
    w = f.w
    f.d.press(w.automation.gears[2], f"버튼 3 {S.SLOT_SETTINGS}")
    page = w.settings_page
    f.d.wait(lambda: w.stack.currentWidget() is page and page.restore_btn.isVisible(), "버튼 3 설정 쪽")
    f.d.settle(0.2)
    return Step(
        do=f"버튼 3 옆 {GEAR}",
        see=f"설정 쪽 제목: {page.title.text()}. 아래쪽에 {btn(S.BTN_RESTORE_PREVIOUS)}.",
        images=[f.d.shot("버튼 3 설정 쪽")],
    )


@scene("slot3-restore", "chat", 18, "이전 설정으로 되돌리기")
def s_slot3_restore(f: Flow) -> Step:
    w = f.w
    f.d.press(w.settings_page.restore_btn, btn(S.BTN_RESTORE_PREVIOUS))
    f.on_panel()
    f.d.idle()
    b3 = f.slot(3)
    restored = S.SETTINGS_RESTORED.format(name=S.SLOT_DEFAULT_NAMES[3])
    f.expect(w.message.text(), restored)
    title, receipt = b3.title.text(), b3.receipt.text()
    if title != S.SLOT_DEFAULT_NAMES[3]:
        raise f.d.fail(f"버튼 3 이름이 돌아오지 않음 ({title})")
    slip = particle_slip(restored, f"'{title}'")
    return Step(
        do=btn(S.BTN_RESTORE_PREVIOUS),
        see=(f"버튼 3이 처음 것으로 돌아와요. 이름: {q(title)}, 요약: {q(b3.summary.text())}, "
             f"아래: {q(receipt)}. 맨 위 알림: {q(restored)}."),
        images=[f.d.shot("버튼 3이 소리 고르게로 돌아옴")],
        note=f"안내서 18번의 '소리 고르게 · 준비 중'은 창에서 이름과 아래 줄로 나뉘어 보여요: {q(title)} / {q(receipt)}.",
        issues=[slip] if slip else [],
    )


@scene("undo-last", "chat", 19, "방금 거 취소")
def s_undo_last(f: Flow) -> Step:
    text = "방금 거 취소"
    card = f.chat(text, QuestionCard, "뺄지 묻는 카드", title=S.CARD_TITLE_UNDO)
    target = f.found["range"]
    what = S.UNDO_WHAT_MARKS.format(request=target.proposal.request, n=target.outcome.placed)
    f.expect(card.plain_text(), what)
    f.found["undo"] = card
    return Step(
        do=f"대화 칸에 \"{text}\" → Enter",
        see=f"카드 {q(S.CARD_TITLE_UNDO)}: {q(what)}. {btn(S.BTN_REMOVE)} {btn(S.BTN_CANCEL)}.",
        images=f.card_shot(card, "방금 거 취소 카드"),
    )


@scene("undo-last-apply", "chat", 19, "빼기")
def s_undo_last_apply(f: Flow) -> Step:
    card = f.found["undo"]
    f.d.press(card.buttons.get("remove"), btn(S.BTN_REMOVE))
    f.d.wait(lambda: S.UNDO_STARTED in card.plain_text() and f.quiet(), "빼기 끝", 30)
    f.d.idle()
    if f.world.ours("Blue"):
        raise f.d.fail("14번에 넣은 파란 표시가 남음")
    target = f.found["range"]
    n = target.outcome.placed
    return Step(
        do=f"카드의 {btn(S.BTN_REMOVE)}",
        see=f"안내 14번의 영수증이 바뀌어요: {q(target.title.text())}. 흉내 그림: 그때 넣은 파란 표시 {n}개가 빠져요.",
        images=f.receipt_shot(target, "14번 영수증이 뺌으로 바뀜", "리졸브 타임라인 흉내: 14번 파란 표시가 빠짐"),
        note=STRIP_NOTE,
    )


@scene("cut", "chat", 20, "여기서 잘라줘")
def s_cut(f: Flow) -> Step:
    text = "여기서 잘라줘"
    pc = f.chat(text, ProposalCard, "보라 표시를 권하는 카드", title=S.OFFER_TITLES["cut"])
    f.expect(pc.plain_text(), btn(S.BTN_OFFER_YES), btn(S.BTN_OFFER_NO))
    f.found["cut"] = pc
    return Step(
        do=f"\"{text}\" → Enter",
        see=f"카드 {q(S.OFFER_TITLES['cut'])}. 단추: {btn(S.BTN_OFFER_YES)} {btn(S.BTN_OFFER_NO)}",
        images=f.card_shot(pc, "자를 수 없다는 카드"),
    )


@scene("cut-no", "chat", 20, "괜찮아요")
def s_cut_no(f: Flow) -> Step:
    pc = f.found["cut"]
    f.d.press(pc.buttons.get("cancel"), btn(S.BTN_OFFER_NO))
    f.d.idle()
    f.expect(pc.plain_text(), S.CHAT_OFFER_DECLINED)
    return Step(
        do=btn(S.BTN_OFFER_NO),
        see=f"카드 맨 아래: {q(S.CHAT_OFFER_DECLINED)}",
        images=f.card_shot(pc, "괜찮아요를 누른 카드"),
    )


@scene("m2", "chat", 21, "자르기 질문 카드")
def s_m2(f: Flow) -> Step:
    card = f.w.runs.manual_cards.get("M2")
    if card is None:
        raise f.d.fail("자르기 질문 카드가 없음")
    f.expect(card.plain_text(), S.M2_QUESTION, S.M2_NOTE, *S.M2_ANSWERS.values())
    answers = " ".join(btn(a) for a in S.M2_ANSWERS.values())
    return Step(
        do="(시간이 되면) 시험용 타임라인 앞부분 한 곳을 평소처럼 잘라 낸 뒤 카드에서 고르기",
        see=(f"대화 칸을 위로 굴리면 안내 10번 뒤에 뜬 카드: {q(S.M2_QUESTION)}. 안내 {q(S.M2_NOTE)}. "
             f"단추: {answers} {btn(S.BTN_LATER)}"),
        images=f.card_shot(card, "자르기 질문 카드"),
        note="미리보기에서는 리졸브에서 자를 수 없어서 답하지 않았어요.",
    )


# ── 모두 빼기와 결과 저장 ─────────────────────────────────────────────

@scene("undo-menu", "undo", 22, "되돌리기 메뉴")
def s_undo_menu(f: Flow) -> Step:
    w = f.w
    entries = w.footer.entry_texts()
    if not entries:
        raise f.d.fail("되돌리기 목록이 비어 있음")
    shots: List[Image] = []

    def open_menu() -> None:
        shots.append(f.d.menu(w.footer.undo_btn, w.footer.menu, S.UNDO_ALL, "되돌리기 메뉴"))

    dialog = f.d.dialog(open_menu, S.BTN_REMOVE, "모두 빼기를 묻는 창", timeout=30)
    # 묻는 창은 다음 단계에서 보인다: 그 그림과 찾은 문제를 넘긴다
    f.found["remove_dialog"] = (dialog, dict(f.d.last_dialog), list(f.d.issues))
    f.d.issues.clear()
    undone = f"({S.UNDO_STATUS['undone']})"
    removed = [e for e in entries if e.endswith(undone)]
    greyed = f" 뺀 일은 흐리게, 끝에 {q(undone)} ({len(removed)}줄)." if removed else ""
    return Step(
        do=f"창 아래 {btn(S.BTN_UNDO)} → {S.UNDO_ALL}",
        see=(f"넣은 일이 새것부터 한 줄씩 보여요. 맨 위: {q(entries[0])}.{greyed} 맨 아래 {q(S.UNDO_ALL)}, "
             f"안내 {q(S.UNDO_HINT)}."),
        images=shots,
    )


@scene("remove-all-confirm", "undo", 22, "모두 빼기 확인")
def s_remove_all_confirm(f: Flow) -> Step:
    image, dlg, issues = f.found["remove_dialog"]
    f.expect(dlg["text"], S.REMOVE_ALL_DETAIL.split(". ")[-1])
    return Step(
        do=f"묻는 창에서 {btn(S.BTN_REMOVE)}",
        see=f"묻는 창 제목: {q(dlg['title'])}. 글: {q(dlg['text'])}. 단추: {buttons_of(dlg['buttons'])}.",
        images=[image],
        note="리졸브가 편집(Edit) 화면이면 이 창이 떠요. 다른 화면일 때는 '이럴 때는'의 첫 단계를 보세요.",
        issues=issues,
    )


@scene("remove-all-done", "undo", 22, "모두 빠짐")
def s_remove_all_done(f: Flow) -> Step:
    w, world = f.w, f.world
    done = S.REMOVE_ALL_DONE.split("{")[0]
    f.d.wait(lambda: w.message.text().startswith(done) and f.quiet(), "모두 빼기 끝", 30)
    f.d.idle()
    message = w.message.text()
    left = world.resolve.markers
    if any(str(m.get("custom") or "").startswith("aih") for m in left.values()):
        raise f.d.fail("도우미 표시가 남음")
    tracks = world.info["tracks"]["audio"]
    if any(t.get("name") == TEST_NAME for t in tracks):
        raise f.d.fail("'AI 도우미 시험' 트랙이 남음")
    f.expect(f.w.chat.log.toPlainText(), message, S.REMOVE_ALL_TRACK_DONE)
    f.d.chat_bottom()
    images = [f.d.shot("모두 뺀 뒤"), f.d.strip("리졸브 타임라인 흉내: 직접 찍은 표시만 남음")]
    summary = w.header.summary.text()
    issues = []
    if f"소리 트랙 {len(tracks)}개" not in summary:
        issues.append(f"'AI 도우미 시험' 트랙을 뺐는데 머리말 요약은 그대로예요: {q(summary)}. 리졸브의 소리 "
                      f"트랙은 이제 {len(tracks)}개예요. {btn(S.BTN_CHECK_CONNECTION)}을 누르면 맞춰져요.")
    return Step(
        do="기다려요.",
        see=(f"맨 위 알림: {q(message)}. 대화 칸 맨 아래: {q(message)}, {q(S.REMOVE_ALL_TRACK_DONE)}. "
             f"흉내 그림: 직접 찍은 표시 {len(left)}개만 남아요."),
        images=images,
        note=STRIP_NOTE,
        issues=issues,
    )


@scene("report", "undo", 23, "결과 저장")
def s_report(f: Flow) -> Step:
    w = f.w
    saved = S.REPORT_SAVED.splitlines()[0]
    image = f.d.menu(w.header.more_btn, w.more_menu, S.MENU_REPORT, "⋯ 메뉴: 결과 저장")
    f.d.wait(lambda: w.message.text().startswith(saved) and not w.report_pending, "결과 파일", 60)
    f.d.idle()
    name = w.last_report.name
    if not name.startswith("AI도우미_결과_") or not w.last_report.is_file():
        raise f.d.fail(f"결과 파일 이름이 다름: {name}")
    f.expect(w.message.text(), name)
    return Step(
        do=f"{btn(S.BTN_MORE)} → {S.MENU_REPORT} (창 아래 {btn(S.BTN_REPORT)}도 같아요)",
        see=f"맨 위 알림: {q(saved)}, 그 아래 파일 경로 (이름 {q(name)}).",
        images=[image, f.d.shot("결과를 저장함")],
        note=("실제로는 바탕 화면에 파일이 생기고 그 폴더가 열려요. 미리보기에서는 폴더를 열지 않았어요. "
              + TEMP_PATH_NOTE),
    )


# ── 이럴 때는 (안내에 없는 화면) ──────────────────────────────────────

@scene("edit-page", "trouble", None, "편집 화면이 아닐 때 모두 빼기")
def s_edit_page(f: Flow) -> Step:
    w, world = f.w, f.world
    # 지난번 시험 흔적이 아직 있고, 리졸브가 색보정(Color) 화면인 경우
    world.restore_legacy()
    world.info["page"] = "color"
    opened = len(world.resolve.opened_pages)
    shots: List[Image] = []

    def open_menu() -> None:
        shots.append(f.d.menu(w.footer.undo_btn, w.footer.menu, S.UNDO_ALL, "되돌리기 메뉴"))

    dialog = f.d.dialog(open_menu, S.BTN_SWITCH_REMOVE, "편집 화면으로 바꿀지 묻는 창", timeout=30)
    dlg = dict(f.d.last_dialog)
    f.d.wait(lambda: w.message.text().startswith(S.REMOVE_ALL_DONE.split("{")[0]) and f.quiet(), "모두 빼기 끝", 30)
    f.d.idle()
    f.expect(dlg["title"], S.EDIT_PAGE_QUESTION)
    f.expect(dlg["text"], S.EDIT_PAGE_DETAIL)
    if world.resolve.opened_pages[opened:] != ["edit", "color"]:
        raise f.d.fail(f"편집 화면으로 바꿨다가 돌아가지 않음 ({world.resolve.opened_pages[opened:]})")
    if any(t.get("name") == TEST_NAME for t in world.info["tracks"]["audio"]):
        raise f.d.fail("'AI 도우미 시험' 트랙이 남음")
    f.expect(f.w.chat.log.toPlainText(), S.REMOVE_ALL_TRACK_DONE)
    f.d.chat_bottom()
    after = f.d.shot("편집 화면으로 바꿔서 뺀 뒤")
    world.info["page"] = "edit"
    return Step(
        do=(f"(지난번 'AI 도우미 시험' 트랙이 남아 있고 리졸브가 편집 화면이 아닐 때) {btn(S.BTN_UNDO)} → "
            f"{S.UNDO_ALL} → {btn(S.BTN_SWITCH_REMOVE)}"),
        see=(f"묻는 창 제목: {q(dlg['title'])}. 글: {q(dlg['text'])}. 단추: {buttons_of(dlg['buttons'])}. "
             f"{btn(S.BTN_SWITCH_REMOVE)}를 누르면 편집 화면으로 바꿔 트랙까지 빼고 원래 화면으로 돌아가요. "
             f"대화 칸 맨 아래: {q(S.REMOVE_ALL_TRACK_DONE)}."),
        images=[*shots, dialog, after],
        note="미리보기에서는 가짜 리졸브를 색보정(Color) 화면으로 두고 지난번 시험 흔적을 다시 넣어 찍었어요.",
    )


@scene("old-script", "trouble", None, "스크립트를 한 번 더 눌러야 할 때", media=False)
def s_old_script(f: Flow) -> Step:
    w, fake = f.w, f.world.fake
    fake.old_script = True
    f.d.press(w.connect_btn, btn(S.BTN_CHECK_CONNECTION))
    f.d.wait(lambda: w.status.text() == S.STATUS_OLD_SCRIPT and f.quiet(), "옛 스크립트 표시", 20)
    f.d.settle(0.2)
    f.expect(f.screen_text(), S.OLD_SCRIPT_HINT, S.SLOT_DISABLED_OLD_SCRIPT)
    image = f.d.shot("옛 스크립트: 한 번 더 눌러 달라는 안내")
    slots = f.slot_lines()
    fake.old_script = False
    f.d.press(w.connect_btn, btn(S.BTN_CHECK_CONNECTION))
    f.d.wait(lambda: w.status.text() == S.STATUS_CONNECTED and f.quiet(), "다시 연결됨", 20)
    f.d.idle()
    return Step(
        do=(f"리졸브에서 예전 스크립트가 돌고 있을 때 ({btn(S.BTN_CHECK_CONNECTION)} 또는 자동화 버튼을 "
            "누르면)"),
        see=f"맨 위: {q(S.DOT_WARN + ' ' + S.STATUS_OLD_SCRIPT)}. 안내: {q(S.OLD_SCRIPT_HINT)}. " + slots,
        images=[image],
        note=(f"리졸브에서 Workspace → Scripts → {SCRIPT_NAME}를 한 번 더 누르면 돼요. 안내서 첫머리의 "
              "\"2번을 한 번 더\"는 표의 1번(스크립트 누르기)을 말하는 것 같아요."),
    )


@scene("resolve-quit", "trouble", None, "리졸브를 껐을 때", media=False)
def s_resolve_quit(f: Flow) -> Step:
    w, world = f.w, f.world
    world.running = False
    f.d.wait(lambda: w.status.text() == S.STATUS_RESOLVE_QUIT, "리졸브가 꺼졌어요 표시", 15)
    f.d.settle(0.2)
    f.expect(f.screen_text(), S.RESOLVE_QUIT_HINT)
    image = f.d.shot("리졸브가 꺼짐")
    slots = f.slot_lines()
    world.running = True
    f.d.wait(lambda: w.connected and f.quiet(), "다시 연결됨", 20)
    f.d.idle()
    return Step(
        do="도우미 창을 켜 둔 채 리졸브를 끔",
        see=(f"몇 초 안에 맨 위: {q(S.DOT_OFF + ' ' + S.STATUS_RESOLVE_QUIT)}. 안내: {q(S.RESOLVE_QUIT_HINT)}. "
             + slots),
        images=[image],
        note="창은 5초마다 윈도우 작업 목록에서 리졸브가 켜져 있는지만 봐요 (리졸브에는 묻지 않아요).",
    )


@scene("update", "trouble", None, "새 판이 나왔을 때 (새 판 받기)", media=False)
def s_update(f: Flow) -> Step:
    w = f.w
    shots: List[Image] = []

    def open_menu() -> None:
        shots.append(f.d.menu(w.header.more_btn, w.more_menu, S.MENU_UPDATE, "⋯ 메뉴: 새 판 받기"))

    dialog = f.d.dialog(open_menu, S.BTN_CANCEL, "새 판 받기를 묻는 창", timeout=30)
    dlg = dict(f.d.last_dialog)
    f.d.settle(0.2)
    f.expect(dlg["title"], S.UPDATE_CONFIRM_TITLE)
    f.expect(dlg["text"], S.UPDATE_CONFIRM)
    if w.closing or not w.isVisible():
        raise f.d.fail(f"{btn(S.BTN_CANCEL)}를 눌렀는데 창이 닫힘")
    return Step(
        do=(f"{btn(S.BTN_MORE)} → {S.MENU_UPDATE} → 묻는 창의 글을 읽고 {btn(S.BTN_UPDATE)} "
            f"(미리보기에서는 {btn(S.BTN_CANCEL)})"),
        see=f"묻는 창 제목: {q(dlg['title'])}. 글: {q(dlg['text'])}. 단추: {buttons_of(dlg['buttons'])}.",
        images=[*shots, dialog],
        note=(f"{btn(S.BTN_UPDATE)}를 누르면 창이 닫히고, 검은 창이 새 판을 받아 지금 폴더에 넣은 뒤 창을 다시 "
              "열어요 (몇 분). 미리보기에서는 검은 창을 띄우지 않고 취소했어요. 리졸브에 넣거나 빼는 중에는 받지 "
              f"않아요 (맨 위 알림: {q(S.UPDATE_BUSY)}). 0.2.0에는 이 메뉴가 없어서, 이 판은 한 번 더 ZIP으로 "
              "설치해야 해요."),
    )


# ── 작은 화면 ───────────────────────────────────────────────────────

@scene("compact", "small", None, "좁고 낮은 창 (380×640)", media=False)
def s_compact(f: Flow) -> Step:
    w = f.w
    f.d.resize(380, 640)
    if w.layout_name != "tight":
        raise f.d.fail(f"좁은 모양이 아님 ({w.layout_name})")
    if w.header.summary_row.isVisible():
        raise f.d.fail("좁은 창에서도 머리말에 타임라인 요약 줄이 보임")
    text = "여기 표시해줘"
    pc = f.chat(text, ProposalCard, "표시 카드", title=S.CARD_TITLE_PROPOSE)
    images = f.card_shot(pc, "380×640 창: 버튼은 타일, 카드는 짧게")
    cut = [name for name, widget in (("입력 칸", w.chat.input), (btn(S.BTN_UNDO), w.footer.undo_btn),
                                     (btn(S.BTN_REPORT), w.footer.report_btn)) if not f.inside(widget)]
    if cut:
        raise f.d.fail("좁은 창에서 잘림: " + ", ".join(cut))
    return Step(
        do=f"창을 380×640으로 줄이고 \"{text}\" → Enter",
        see=("머리말에서 타임라인 요약 줄이 빠지고, 자동화 버튼이 한 줄에 셋인 타일로 바뀌어요 (톱니바퀴는 타일 "
             "아래). 카드는 리졸브 줄과 그대로 줄을 한 줄로 합쳐 짧아져요. 입력 칸과 아래 단추는 잘리지 않아요."),
        images=images,
        note="작은 노트북 화면이나 창을 줄였을 때의 모양이에요.",
    )


@scene("scale150", "small", None, "윈도우 화면 배율 150%", media=False, subprocess=True)
def s_scale150(f: Flow) -> Step:
    if f.scaled is None:
        raise f.d.fail("다른 배율로 띄울 방법이 없음")
    image = f.scaled("화면 배율 150%의 도우미 창")
    sizes = ", ".join(f"{s}%" for s in TEXT_SCALES if s != 100)
    return Step(
        do="윈도우 설정 → 디스플레이 → 배율이 150%인 1920×1080 화면 (노트북에 흔해요)",
        see=("창이 380×640 크기로 계산되어 바로 위와 같은 좁은 모양이 돼요. 그림은 그 화면의 실제 픽셀 크기로 "
             "보여서 글자가 1.5배 커 보여요."),
        images=[image],
        note=(f"윈도우의 화면 배율로 본 모양이에요. 도우미의 ⋯ → {S.MENU_SETTINGS} → {S.MENU_TEXT_SIZE}"
              f"({sizes})와는 다른 설정이에요. 새로 띄운 창이라 연결 뒤 \"여기 표시해줘\"를 보낸 카드까지 "
              "찍었어요."),
    )
