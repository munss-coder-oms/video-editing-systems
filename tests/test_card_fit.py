"""카드가 대화 칸 안에 들어가는지, 창의 대화 상자가 읽히는지, 대화가 새 줄을 따라가는지 (offscreen).

- 카드마다 (표시 확인 카드의 줄과 [−][+][이동], 지우기 카드, 영수증, 저장 줄, M2·M3 질문, 목소리 고르기,
  권하는 표시 카드, 되돌리기 확인) 창 너비 360~460과 380×640, 글자 크기 100%·130%에서
  보이는 것의 오른쪽 끝이 대화 칸 안에 있고 옆으로 밀 필요가 없다. 단추 글이 잘리지 않는다.
- 화면 배율 150%는 따로 띄운 Qt(QT_SCALE_FACTOR=1.5)에서 같은 검사를 한다.
- 창이 띄우는 확인 창(ask_box, choose_box)은 어두운 바탕에서 글 대비가 4.5 이상이다.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from app.companion import strings_ko as S  # noqa: E402
from tests.fakes import FakeLuaBridge, FakeResolve, timeline_info  # noqa: E402
from tests.test_panel import make_window, qapp, settle, wait_until  # noqa: E402,F401

ROOT = Path(__file__).resolve().parent.parent
TL0 = 216000
FPS = 60
WIDTHS = (460, 440, 420, 400, 380, 360)


@pytest.fixture
def resolve(tmp_path):
    fake = FakeLuaBridge(tmp_path)
    fr = FakeResolve(timeline_info())
    fr.add_user_marker(600, custom="", color="Blue", name="내 파란 표시")
    fake.resolve = fr
    return fake, fr


def _idle(qapp, w, timeout=30.0):
    wait_until(qapp, lambda: not w.busy and w.pending == 0 and not w.chat_flow.active, timeout)


def _send(qapp, w, text):
    w.chat.input.setPlainText(text)
    w.chat.send_btn.click()
    _idle(qapp, w)


def _last(w, cls):
    return [c for c in w.runs.cards if isinstance(c, cls)][-1]


def _pauses_proposal(compact_request: str = "11초~21.5초에서 0.5초 넘게 쉰 곳 표시해줘"):
    """대화에서 온 쉬는 곳 제안 (저장 줄, 목소리 줄, [−][+] 쉰 길이가 모두 보이는 카드)."""
    from engine.edits.proposal import MarkerRow, Proposal, VoiceInfo, build_specs, new_proposal_id, pause_name

    pid = new_proposal_id()
    rows = [MarkerRow(TL0 + 660 + i * 120, TL0 + 700 + i * 120, 0.7, pause_name("쉬는 곳", 0.7), "")
            for i in range(5)]
    specs = build_specs(pid, rows, TL0, "Blue", point=False, note_for=lambda r: "쉼")
    return Proposal(
        id=pid, kind="mark_pauses", slot=None, origin="chat:rule", request=compact_request,
        params={"min_s": 0.5, "color": "Blue", "scope": "whole", "merge_s": 0.3, "max": 200},
        rows=rows, specs=specs, timeline={"name": "Timeline 1", "timeline_uid": "tl-1"}, fps=float(FPS),
        tl_start=TL0, tl_end=TL0 + 67560, fingerprint="fp",
        scope={"kind": "range", "lo": TL0 + 660, "hi": TL0 + 1290, "src": "said"},
        voice=VoiceInfo(stream=1, stream_count=4, signature="sig", remembered=True, mix=0, doubled=True),
        tracks=[2, 5], total_s=3.5, found=5, warnings={"missing_files": 1, "single_file": 1},
        provenance={"min_s": "said", "range": "said", "color": "setting", "places": "found"},
        requested={"range": [TL0 + 660, TL0 + 1290], "range_src": "said", "said_whole": False, "tracks": None,
                   "count_hint": None})


def _voice_question():
    from engine.automation.plan import StreamChoice, VoiceQuestion

    streams = [StreamChoice(index=i, channels=2, title="", codec="aac", start=0.0, best_start=1.0,
                            profile={"integrated": -60.0 if i == 3 else -20.0}, enabled_on_timeline=True)
               for i in range(4)]
    return VoiceQuestion(signature="sig", path="C:/obs.mkv", streams=streams, mix=0, doubled=True, reason="changed",
                         previous=1)


def build_all_cards(qapp, w, fr):
    """카드마다 하나씩 대화에 붙인다. 돌려주는 값: {이름: 카드}."""
    from app.companion.cards import ClearCard, ProposalCard, QuestionCard
    from app.companion.voice_picker import VoicePickerCard, doubled_text
    from engine.edits.apply import ApplyOutcome

    cards = {}
    _send(qapp, w, "1분, 2분, 3분 30초에 빨간 표시해줘, 메모는 '자막 확인'")
    cards["mark"] = _last(w, ProposalCard)
    assert cards["mark"].extra.get("inc:at:0") is not None and cards["mark"].extra.get("view:2") is not None
    _send(qapp, w, "1분, 2분, 3분 30초에 파란 표시해줘")
    cards["receipt"] = _last(w, ProposalCard)
    cards["receipt"].buttons["apply"].click()
    wait_until(qapp, lambda: cards["receipt"].state == "receipt" and not w.busy, 20)
    _send(qapp, w, "3분에서 잘라줘")
    cards["offer_cut"] = _last(w, ProposalCard)
    _send(qapp, w, "3분에서 소리 줄여줘")
    cards["offer_audio"] = _last(w, ProposalCard)
    assert cards["offer_audio"].extra.get("inc:db") is not None
    _send(qapp, w, "도우미가 넣은 파란 표시 지워줘")
    cards["clear"] = _last(w, ClearCard)
    _send(qapp, w, "방금 거 취소")
    cards["confirm_undo"] = [c for c in w.runs.cards if isinstance(c, QuestionCard)
                             and c.title.text() == S.CARD_TITLE_UNDO][-1]
    cards["m3"] = w.runs.ask_manual("M3", again=True)
    cards["m2"] = w.runs.ask_manual("M2", again=True)
    compact = w.layout_name == "tight"
    p = _pauses_proposal()
    card = ProposalCard(p, compact=compact, guard=w.runs.guard_for(p), save_slots=w.runs.save_slots(p),
                        doubled_note=doubled_text(0), add_note=S.RERUN_ADD_SKIP.format(n=2, m=3))
    cards["save_row"] = w.runs._add_card(card)
    assert card.save_combo is not None and "save" in card.extra
    p2 = _pauses_proposal()
    card2 = ProposalCard(p2, compact=compact, save_slots=w.runs.save_slots(p2))
    card2.show_receipt(ApplyOutcome(status="applied", proposal_id=p2.id, expected=5, placed=5, at="14:02",
                                    point_fallback=True, skipped_existing=1))
    cards["save_receipt"] = w.runs._add_card(card2)
    cards["voice"] = w.runs._add_card(VoicePickerCard(_voice_question()))
    rerun = QuestionCard(S.RERUN_QUESTION.format(color_word="파란", n=12), [S.RERUN_EXPLAIN],
                         [("replace", S.BTN_REPLACE, True), ("add", S.BTN_ADD_MORE, False),
                          ("cancel", S.BTN_CANCEL, False)])
    cards["confirm_rerun"] = w.runs._add_card(rerun)
    _idle(qapp, w)
    return cards


def fit_problems(w, cards):
    """대화 칸 밖으로 나간 것, 카드 안에서 잘린 것 (카드 이름, 무엇, 끝, 들어갈 자리)."""
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QAbstractButton, QLabel, QWidget

    log = w.chat.log
    vp = log.viewport()
    out = []
    if log.inner.width() > vp.width():
        out.append(("list", "inner", log.inner.width(), vp.width()))
    if log.horizontalScrollBar().maximum() > 0:
        out.append(("list", "hscroll", log.horizontalScrollBar().maximum(), 0))
    for name, card in cards.items():
        if not card.isVisible():
            out.append((name, "card hidden", 0, vp.width()))
            continue
        # 높이: 카드는 지금 너비에서 필요한 높이를 다 받는다 (버튼 줄이 두 줄로 쌓여도 아래가 잘리지 않게)
        need_h = card.heightForWidth(card.width()) if card.hasHeightForWidth() else card.sizeHint().height()
        if card.height() < need_h:
            out.append((name, "card too short", need_h, card.height()))
        for child in [card] + card.findChildren(QWidget):
            if not child.isVisible() or child.width() <= 0:
                continue
            right = child.mapTo(vp, QPoint(child.width(), 0)).x()
            if right > vp.width():
                out.append((name, child.metaObject().className() + ":" + _text(child), right, vp.width()))
            parent = child.parentWidget()
            if child is not card and parent is not None and child.x() + child.width() > parent.width():
                out.append((name, "outside parent " + _text(child), child.x() + child.width(), parent.width()))
            if child is not card and parent is not None and child.y() + child.height() > parent.height():
                out.append((name, "below parent " + _text(child), child.y() + child.height(), parent.height()))
            if isinstance(child, QAbstractButton) and child.text():
                need = child.fontMetrics().horizontalAdvance(child.text().replace("&", ""))
                if need > child.width():
                    out.append((name, "clipped button " + child.text(), need, child.width()))
            if isinstance(child, QLabel) and not child.wordWrap() and child.text() and child.sizePolicy():
                need = child.fontMetrics().horizontalAdvance(child.text())
                if need > child.width() + 1 and child.width() < child.sizeHint().width():
                    out.append((name, "clipped label " + child.text(), need, child.width()))
    return out


def _text(widget) -> str:
    t = getattr(widget, "text", None)
    try:
        return str(t())[:30] if callable(t) else ""
    except TypeError:
        return ""


def _resize(qapp, w, width, height):
    w._placed = True
    w.resize(width, height)
    settle(qapp, 0.08)


@pytest.mark.parametrize("scale", [100, 130])
@pytest.mark.parametrize("height", [900, 640])
def test_every_card_fits_the_chat_at_every_width(qapp, make_window, resolve, height, scale):
    fake, fr = resolve
    w = make_window(fake)
    wait_until(qapp, lambda: w.connected and w.pending == 0)
    w.apply_text_scale(scale)
    _resize(qapp, w, 460, height)
    cards = build_all_cards(qapp, w, fr)
    assert len(cards) == 12
    for width in WIDTHS + tuple(reversed(WIDTHS)):
        _resize(qapp, w, width, height)
        assert w.width() == width
        for name, card in cards.items():
            w.chat.log.ensureWidgetVisible(card, 0, 0)
        settle(qapp, 0.03)
        assert fit_problems(w, cards) == [], (width, height, scale)
    # 버튼 줄은 줄을 바꿔 쌓인다 (M3 답 4개 + 나중에): 가장 좁을 때 한 줄보다 높다
    _resize(qapp, w, 360, height)
    m3 = cards["m3"]
    rows = {b.y() for b in m3.buttons.values()}
    assert len(rows) >= 2 and all(b.isVisible() for b in m3.buttons.values())


def test_compact_380x640_cards_fit(qapp, make_window, resolve):
    """좁은 화면(380×640)에서 처음부터 만든 카드 (compact 모양)."""
    fake, fr = resolve
    w = make_window(fake)
    wait_until(qapp, lambda: w.connected and w.pending == 0)
    _resize(qapp, w, 380, 640)
    assert w.layout_name == "tight"
    cards = build_all_cards(qapp, w, fr)
    for card in cards.values():
        w.chat.log.ensureWidgetVisible(card, 0, 0)
    settle(qapp, 0.05)
    assert fit_problems(w, cards) == []
    # compact 카드: "안 바뀌는 것"은 따로 한 줄이 아니라 풀이(툴팁)로
    assert S.KEEP_MARKERS not in cards["mark"].plain_text()


def test_small_buttons_in_cards_get_the_short_style(qapp, make_window, resolve):
    """카드 안의 [−][+]·이동은 낮은 단추 (스타일 속성이 QWidget의 size 속성과 겹치면 스타일이 안 먹는다)."""
    from PySide6.QtWidgets import QToolButton

    from app.companion import theme

    fake, fr = resolve
    w = make_window(fake)
    wait_until(qapp, lambda: w.connected and w.pending == 0)
    _resize(qapp, w, 420, 900)
    cards = build_all_cards(qapp, w, fr)
    steps = [b for c in cards.values() for b in c.findChildren(QToolButton)
             if b.property("role") == "step" and b.isVisible()]
    assert steps and {b.height() for b in steps} == {theme.IN_CARD_H + 2}


SCALED = r"""
import os, sys
sys.path.insert(0, {root!r})
os.environ["APPDATA"] = {tmp!r} + "/Roaming"
os.environ["PROGRAMDATA"] = {tmp!r} + "/ProgramData"
from pathlib import Path
from PySide6.QtWidgets import QApplication
app = QApplication([])
from tests.fakes import FakeLuaBridge, FakeResolve, timeline_info
from tests import test_card_fit as T
from tests.test_panel import wait_until
from app.companion.window import HelperWindow
tmp = Path({tmp!r})
fake = FakeLuaBridge(tmp)
fr = FakeResolve(timeline_info())
fake.resolve = fr
w = HelperWindow(bridge=fake, interactive=False, report_dir=tmp / "desktop", auto_ping_ms=50,
                 state_root=tmp / "state", process_check=None)
w.show()
wait_until(app, lambda: w.connected and w.pending == 0)
bad = []
for width, height in ((380, 640), (420, 900)):
    T._resize(app, w, width, height)
    cards = T.build_all_cards(app, w, fr)
    for wd in (width, 360, 460):
        T._resize(app, w, wd, height)
        bad += [(wd, height) + p for p in T.fit_problems(w, cards)]
print("DPR", w.devicePixelRatio())
print("PROBLEMS", bad)
w.close()
"""


def test_cards_fit_at_display_scale_150(tmp_path):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_SCALE_FACTOR="1.5")
    code = SCALED.format(root=str(ROOT), tmp=str(tmp_path))
    r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env, capture_output=True, text=True,
                       timeout=240)
    assert r.returncode == 0, r.stderr[-3000:]
    assert "DPR 1.5" in r.stdout
    assert "PROBLEMS []" in r.stdout, r.stdout[-3000:]


# ---------------------------------------------------------------------------
# 대비
# ---------------------------------------------------------------------------

def _luminance(c) -> float:
    def ch(v):
        v = v / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    return 0.2126 * ch(c.red()) + 0.7152 * ch(c.green()) + 0.0722 * ch(c.blue())


def contrast(a, b) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _text_contrast(widget) -> float:
    """위젯을 그려서: 가장 많은 색(바탕)과 바탕에서 가장 먼 색(글)의 대비."""
    from collections import Counter

    from PySide6.QtGui import QColor

    img = widget.grab().toImage()
    counts = Counter()
    for y in range(0, img.height(), 1):
        for x in range(0, img.width(), 1):
            counts[img.pixel(x, y) & 0xFFFFFF] += 1
    bg = QColor(counts.most_common(1)[0][0])
    far = max(counts, key=lambda rgb: contrast(QColor(rgb), bg) if counts[rgb] >= 3 else 0)
    return contrast(QColor(far), bg)


@pytest.mark.parametrize("scale", [100, 130])
def test_dialogs_are_readable_in_the_dark_theme(qapp, make_window, tmp_path, scale):
    from PySide6.QtWidgets import QLabel, QPushButton
    from app.companion.runs import edit_page_text

    w = make_window(FakeLuaBridge(tmp_path))
    w.apply_text_scale(scale)
    box, ok = w.ask_box(S.PROBE_CONFIRM_TITLE, S.PROBE_CONFIRM, S.BTN_START)
    box2, buttons = w.choose_box(S.REMOVE_ALL_TITLE, edit_page_text(S.REMOVE_ALL_CONFIRM + "\n\n" + S.REMOVE_ALL_KEEP),
                                 [S.BTN_SWITCH_REMOVE, S.BTN_MARKERS_ONLY, S.BTN_CANCEL])
    for b in (box, box2):
        b.show()
        settle(qapp, 0.05)
        labels = [lb for lb in b.findChildren(QLabel) if lb.isVisible() and lb.text()]
        assert labels
        for lb in labels:
            assert _text_contrast(lb) >= 4.5, lb.text()
        pushes = [p for p in b.findChildren(QPushButton) if p.isVisible()]
        assert pushes
        for p in pushes:
            assert _text_contrast(p) >= 4.5, p.text()
        b.hide()
    # 확인 단추는 파란 단추 (primary)
    assert ok.property("kind") == "primary" and buttons[0].property("kind") == "primary"
    for b in (box, box2):
        b.deleteLater()


def test_menus_and_combo_popups_are_dark(qapp, make_window, tmp_path):
    from PySide6.QtGui import QColor

    w = make_window(FakeLuaBridge(tmp_path))
    menu = w.header.more_btn.menu()
    menu.popup(w.mapToGlobal(w.rect().center()))
    settle(qapp, 0.05)
    img = menu.grab().toImage()
    bg = QColor(img.pixel(img.width() // 2, 2))
    assert bg.lightness() < 80  # 어두운 바탕
    assert _text_contrast(menu) >= 4.5
    menu.hide()


def test_disabled_primary_button_is_not_bright_blue(qapp, make_window, tmp_path):
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QPushButton

    from app.companion import theme

    w = make_window(FakeLuaBridge(tmp_path))
    btn = QPushButton("리졸브에 넣기", w.panel)
    btn.setProperty("kind", "primary")
    btn.setEnabled(False)
    btn.resize(160, 40)
    btn.show()
    settle(qapp, 0.05)
    img = btn.grab().toImage()
    fill = QColor(img.pixel(btn.width() // 2, 4))
    assert fill.name() != theme.TOKENS.primary and abs(fill.blue() - QColor(theme.TOKENS.card).blue()) < 12
    btn.setEnabled(True)
    settle(qapp, 0.02)
    assert QColor(btn.grab().toImage().pixel(btn.width() // 2, 4)).name() == theme.TOKENS.primary


# ---------------------------------------------------------------------------
# 대화가 새 줄을 따라간다
# ---------------------------------------------------------------------------

def test_chat_follows_new_lines_and_stops_when_scrolled_up(qapp, make_window, tmp_path):
    w = make_window(FakeLuaBridge(tmp_path))
    _resize(qapp, w, 420, 900)
    log = w.chat.log
    bar = log.verticalScrollBar()
    for i in range(40):
        w.chat.add_helper(f"줄 {i}")
    settle(qapp, 0.05)
    assert bar.maximum() > 0 and bar.value() == bar.maximum()  # 맨 아래 (새 줄이 보인다)
    # 사람이 위로 올려 읽는 중: 도우미의 새 줄이 와도 끌어내리지 않는다 ... 가 아니라, 새 줄은 늘 보여 준다
    bar.setValue(0)
    settle(qapp, 0.02)
    assert not log.follow
    bar.setValue(bar.maximum())
    settle(qapp, 0.02)
    assert log.follow  # 맨 아래로 돌아오면 다시 따라간다
    w.chat.add_helper("마지막 줄")
    settle(qapp, 0.05)
    assert bar.value() == bar.maximum()
    last = log.items[-1]
    assert last.mapTo(log.viewport(), last.rect().bottomLeft()).y() <= log.viewport().height()


def test_tall_card_is_shown_from_its_top(qapp, make_window, resolve):
    """대화 칸보다 큰 카드는 맨 아래가 아니라 카드의 맨 위(제목)가 보이게 멈춘다."""
    from PySide6.QtWidgets import QWidget

    from app.companion.cards import QuestionCard

    fake, fr = resolve
    w = make_window(fake)
    _resize(qapp, w, 380, 640)
    log = w.chat.log
    for i in range(20):
        w.chat.add_helper(f"줄 {i}")
    lines = [f"{i}번째 줄: 대화 칸보다 긴 카드를 만들려고 넣은 글이에요" for i in range(30)]
    card = w.runs._add_card(QuestionCard(S.M3_QUESTION, lines, [("later", S.BTN_LATER, False)]))
    settle(qapp, 0.1)
    assert card.height() > log.viewport().height()
    top = card.mapTo(log.viewport(), card.rect().topLeft()).y()
    assert 0 <= top <= 12  # 제목이 칸의 맨 위에 보인다
    # 짧은 줄이 뒤따르면 다시 맨 아래로
    w.chat.add_helper("다음 줄")
    settle(qapp, 0.05)
    assert log.verticalScrollBar().value() == log.verticalScrollBar().maximum()
    # 칸 높이와 거의 같은 카드: 맨 아래로 가면 위아래 여백만큼 제목이 가려지므로 이것도 맨 위에서 멈춘다
    for extra in (-8, 0, 2):
        block = QWidget()
        block.setFixedHeight(log.viewport().height() + extra)
        w.chat.add_card(block)
        settle(qapp, 0.05)
        top = block.mapTo(log.viewport(), block.rect().topLeft()).y()
        assert 0 <= top <= 12, (extra, top)


def test_chat_toggle_sits_on_the_brain_line(qapp, make_window, tmp_path):
    w = make_window(FakeLuaBridge(tmp_path))
    _resize(qapp, w, 420, 900)
    chat = w.chat
    assert chat.brain.text() == S.CHAT_BRAIN_LINE
    toggle_mid = chat.toggle.geometry().center().y()
    assert chat.brain.geometry().top() <= toggle_mid <= chat.brain.geometry().bottom() + 8
    assert chat.toggle.geometry().top() > chat.log.geometry().bottom()  # 대화 목록 아래에 있다
    assert chat.toggle.height() <= 34


def test_voice_picker_listening_state_and_pick_collapses(qapp, make_window, tmp_path):
    from app.companion.voice_picker import VoicePickerCard

    w = make_window(FakeLuaBridge(tmp_path))
    card = w.runs._add_card(VoicePickerCard(_voice_question()))
    width = card.listen_buttons[1].width()
    card.set_playing(1, ms=60)
    assert card.listen_buttons[1].text() == S.BTN_LISTENING and card.listen_buttons[1].width() == width
    wait_until(qapp, lambda: card.listen_buttons[1].text() == S.BTN_LISTEN, 2)
    notes = []
    card.closed.connect(notes.append)
    card.done(2)
    assert card.title.text() == S.fill(S.VOICE_PICKED, n=3) and notes == [card.title.text()]
    assert card.plain_text() == card.title.text()  # 한 줄로 접혔다


def test_pauses_card_rows_are_short(qapp, make_window, tmp_path):
    """쉬는 곳 카드: 할 일 · 언제 · 얼마나 · 리졸브에 넣을 것 네 줄. 트랙은 목소리 줄에."""
    from app.companion.cards import ProposalCard, proposal_rows, voice_row_text

    w = make_window(FakeLuaBridge(tmp_path))
    p = _pauses_proposal()
    rows = proposal_rows(p, 0, False)
    assert [r[0] for r in rows] == [S.ROW_WHAT, S.ROW_WHEN, S.ROW_HOW, S.ROW_RESOLVE]
    card = w.runs._add_card(ProposalCard(p, save_slots=w.runs.save_slots(p)))
    text = card.plain_text()
    assert voice_row_text(p.voice, p.tracks) in text
    assert S.WARNINGS["single_file"].format(n=1) in text
    # 일 이름 줄에는 출처 꼬리표가 붙지 않는다
    assert S.PROVENANCE["said"] not in text
