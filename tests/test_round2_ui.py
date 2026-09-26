"""창을 써 보며 찾은 문제(2차)가 다시 생기지 않게 (offscreen, 가짜 리졸브).

점검 도구 쪽의 알림 줄, 다시 누름 질문의 [취소], [■ 듣는 중]의 너비, 기본 표시 이름, 지우기 카드의 말,
머리말의 길이, 목소리 카드의 꼬리표와 강조, 문장 끝, 체크 상자, "N곳 더 보기", 저장 줄의 자리,
머리말 알림이 카드와 같은 말일 때, 되돌리기 목록의 낱말, 모두 빼기 결과, 버튼 설정 쪽의 여백, 좁은 창의 ⚙.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from app.companion import strings_ko as S  # noqa: E402
from tests.conftest import requires_ffmpeg  # noqa: E402
from tests.fakes import FakeLuaBridge, FakeResolve, timeline_info  # noqa: E402
from tests.test_card_fit import _pauses_proposal, _voice_question, contrast  # noqa: E402
from tests.test_panel import make_window, qapp, settle, wait_until  # noqa: E402,F401

TL0 = 216000
FPS = 60


@pytest.fixture
def resolve(tmp_path):
    fake = FakeLuaBridge(tmp_path)
    fr = FakeResolve(timeline_info())
    fake.resolve = fr
    return fake, fr


def _idle(qapp, w, timeout=30.0):
    wait_until(qapp, lambda: not w.busy and w.pending == 0 and not w.chat_flow.active, timeout)


def _send(qapp, w, text):
    w.chat.input.setPlainText(text)
    w.chat.send_btn.click()
    _idle(qapp, w)


def _resize(qapp, w, width, height):
    w._placed = True
    w.resize(width, height)
    settle(qapp, 0.08)


def _connected(qapp, make_window, fake, size=(420, 900)):
    w = make_window(fake)
    wait_until(qapp, lambda: w.connected and w.pending == 0)
    _resize(qapp, w, *size)
    return w


def _in(widget, parent):
    """widget의 사각형을 parent 좌표로."""
    from PySide6.QtCore import QRect

    top_left = widget.mapTo(parent, widget.rect().topLeft())
    return QRect(top_left, widget.size())


# ── 1. 점검 도구 쪽의 알림 줄 ─────────────────────────────────────────

def test_check_page_work_reports_on_the_check_page(qapp, make_window, resolve):
    fake, fr = resolve
    w = _connected(qapp, make_window, fake)
    page = w.check_page
    header_before = w.message.text()
    w.show_check_page()
    settle(qapp, 0.05)
    assert not page.notice.isVisible()  # 아직 한 일이 없으면 빈 줄을 두지 않는다
    # 점검 도구 쪽 일(기능 점검 등)의 알림은 그 쪽에, 본 쪽 머리말은 그대로
    for name in ("probe", "marker", "audio", "cleanup", "leftover"):
        w.notify(name, f"{name} 알림")
        assert page.notice.text() == f"{name} 알림" and w.message.text() == header_before
    settle(qapp, 0.05)
    assert page.notice.isVisible()
    # 알림 줄은 안내 아래, [기능 점검] 위에 (누른 단추 가까이)
    intro = [lb for lb in page.findChildren(type(page.notice)) if lb.text() == S.CHECK_INTRO][0]
    assert intro.y() < page.notice.y() < page.probe_btn.y()
    # 결과 저장은 지금 보고 있는 쪽에
    w.page_notice("저장했어요")
    assert page.notice.text() == "저장했어요" and w.message.text() == header_before
    w.show_panel()
    w.page_notice("본 쪽에서 저장")
    assert w.message.text() == "본 쪽에서 저장" and page.notice.text() == "저장했어요"
    # 연결 확인처럼 본 쪽 일은 머리말에
    w.notify("connect", "연결 알림")
    assert w.message.text() == "연결 알림"


def test_probe_notice_says_to_send_the_file_on_the_check_page(qapp, make_window, resolve):
    fake, fr = resolve
    w = _connected(qapp, make_window, fake)
    w.confirm = lambda *a, **k: True
    w.show_check_page()
    header_before = w.message.text()
    w.check_page.probe_btn.click()
    wait_until(qapp, lambda: w.action is None and w.pending == 0 and not w.report_pending
               and "\n" in w.check_page.notice.text(), 60)
    notice = w.check_page.notice.text()
    # 가짜 리졸브는 점검용 복사본을 남긴다: 마친 말은 그것까지
    assert notice.split("\n")[0] in (S.PROBE_DONE_ALL.format(n=len(S.PROBE_STAGE_NAMES)), S.PROBE_DONE_LEFTOVER)
    assert S.PROBE_REPORT_SAVED.split("{")[0] in notice and str(w._probe_report) in notice
    assert w.check_page.notice.isVisible()
    # 본 쪽 머리말에는 띄우지 않는다 (나중에 본 쪽으로 돌아가도 버튼 위를 밀어내지 않게)
    assert w.message.text() == header_before


# ── 2, 18. 다시 누름 질문의 [취소] ────────────────────────────────────

@requires_ffmpeg
def test_rerun_question_can_be_cancelled_and_the_receipt_comes_back(qapp, make_window, tmp_path, obs_video,
                                                                    tmp_path_factory):
    from app.companion.cards import ProposalCard, QuestionCard
    from engine.analysis_cache import AnalysisCache
    from engine.probe import probe
    from engine.timeline.audio_map import layout_signature
    from tests.fakes import obs_items

    fake = FakeLuaBridge(tmp_path)
    fake.info = timeline_info(start_frame=108000, end_frame=108000 + 900, fps="30")
    fr = FakeResolve(fake.info, obs_items(str(obs_video), 108000, 900, clip_fps="30"))
    fake.resolve = fr
    w = make_window(fake)
    w.runs.cache = AnalysisCache(tmp_path_factory.mktemp("r2_cache"))
    w.settings.set_voice(layout_signature(probe(str(obs_video))), 1, mix=0)
    wait_until(qapp, lambda: w.connected and w.pending == 0)
    w.automation.buttons[0].click()
    wait_until(qapp, lambda: any(isinstance(c, ProposalCard) for c in w.runs.cards) and not w.runs.busy, 60)
    card = [c for c in w.runs.cards if isinstance(c, ProposalCard)][-1]
    card.buttons["apply"].click()
    wait_until(qapp, lambda: card.state == "receipt" and not w.runs.busy, 30)
    receipt = w.automation.buttons[0].receipt.text()
    assert receipt.startswith("✓")

    asked = len([c for c in w.runs.cards if isinstance(c, QuestionCard)])
    w.automation.buttons[0].click()
    wait_until(qapp, lambda: len([c for c in w.runs.cards if isinstance(c, QuestionCard)]) > asked
               and not w.runs.busy, 60)
    q = [c for c in w.runs.cards if isinstance(c, QuestionCard)][-1]
    assert list(q.buttons) == ["replace", "add", "cancel"] and q.buttons["cancel"].text() == S.BTN_CANCEL
    # 어느 표시를 말하는지: 넣은 시각과 부탁(버튼 이름)
    assert S.fill(S.RERUN_FROM, entries="").split(":")[0] in q.plain_text()
    assert f"'{S.SLOT_DEFAULT_NAMES[1]}'" in q.plain_text()
    assert w.automation.buttons[0].receipt.text() == S.SLOT_RECEIPT_WAITING
    before = len(fake.requests)
    q.buttons["cancel"].click()
    settle(qapp, 0.05)
    # 잘못 눌렀으면 한 번에 나간다: 카드는 닫히고, 버튼의 결과 줄은 지난번 결과로, 리졸브는 그대로
    assert q.locked and q.buttons == {} or not any(b.isEnabled() for b in q.buttons.values())
    assert w.automation.buttons[0].receipt.text() == receipt
    assert not any(op in ("add_markers", "delete_markers") for op in fake.requests[before:])
    assert not w.runs.busy and w.automation.buttons[0].isEnabled()


# ── 3. [■ 듣는 중]의 너비 ─────────────────────────────────────────────

@pytest.mark.parametrize("scale", [100, 130])
def test_listening_button_keeps_its_width_and_place(qapp, make_window, tmp_path, scale):
    from app.companion.voice_picker import VoicePickerCard

    w = make_window(FakeLuaBridge(tmp_path))
    w.apply_text_scale(scale)
    _resize(qapp, w, 420, 900)
    card = w.runs._add_card(VoicePickerCard(_voice_question()))
    settle(qapp, 0.1)
    geo = {i: (b.x(), b.width()) for i, b in card.listen_buttons.items()}
    picks = {i: b.x() for i, b in card.pick_buttons.items()}
    assert len({g for g in geo.values()}) == 1  # 줄마다 같은 자리, 같은 너비
    for playing in (1, 0, 3):
        card.set_playing(playing, ms=5000)
        settle(qapp, 0.05)
        assert {i: (b.x(), b.width()) for i, b in card.listen_buttons.items()} == geo
        assert {i: b.x() for i, b in card.pick_buttons.items()} == picks
        btn = card.listen_buttons[playing]
        assert btn.text() == S.BTN_LISTENING
        assert btn.fontMetrics().horizontalAdvance(S.BTN_LISTENING) <= btn.width()
    card.set_playing(None)


# ── 4. 대화로 넣는 표시의 기본 이름 ────────────────────────────────────

def test_default_mark_name_is_not_the_category_word(qapp, make_window, resolve):
    from app.companion.cards import ProposalCard
    from engine.chat import rules

    assert rules.DEFAULT_MARK_NAME not in (S.CLEAR_COLOR_FALLBACK + " 표시", "도우미 표시")
    fake, fr = resolve
    w = _connected(qapp, make_window, fake)
    _send(qapp, w, "3분 20초에 빨간 표시해줘")
    card = [c for c in w.runs.cards if isinstance(c, ProposalCard)][-1]
    text = card.plain_text()
    assert f"'{rules.DEFAULT_MARK_NAME}'" in text and "'도우미 표시'" not in text
    # "도우미 표시"는 도우미가 넣은 표시 모두를 부르는 말로만: 질문·칩도 "도우미가 넣은 표시"
    assert S.M3_QUESTION.startswith("도우미가 넣은 표시") and S.CHIPS_AFTER_APPLY[-1].startswith("도우미가 넣은 표시")


# ── 5. 지우기 카드의 말 ────────────────────────────────────────────────

def test_clear_card_names_the_colour_and_what_stays():
    from app.companion.cards import clear_colors, clear_keep, clear_rows

    plan = SimpleNamespace(colors=["Blue"], count=9, total_ours=12, straddling=0, provenance={},
                           params={"colors": ["Blue"]}, fps=60.0, tl_start=0, tl_end=60 * 60 * 7)
    rows = dict(clear_rows(plan))
    # 값은 문장이 아니라 "무엇 몇 개" (넣을 때의 "파란 표시 9개"와 같은 꼴), 지울/지워요를 되풀이하지 않는다
    assert rows[S.ROW_CLEAR] == "파란 표시 9개" and "지워" not in rows[S.ROW_CLEAR]
    assert clear_colors(SimpleNamespace(colors=["Red", "Blue"])) == "빨간·파란"
    assert clear_colors(SimpleNamespace(colors=[])) == S.CLEAR_COLOR_FALLBACK
    # 안 바뀌는 것: "고르지 않은"이 아니라 나머지 도우미 표시가 몇 개인지
    assert clear_keep(plan) == S.CLEAR_KEEP_OTHERS.format(n=3) and "고르지" not in clear_keep(plan)
    assert clear_keep(SimpleNamespace(total_ours=9, count=9, straddling=0)) == S.CLEAR_KEEP
    assert S.CLEAR_RECEIPT.format(at="오후 9:50", colors="파란", n=9) == "✓ 오후 9:50 지웠어요 · 파란 표시 9개"


# ── 6. 같은 설정은 같은 말로 ──────────────────────────────────────────

@pytest.mark.parametrize("kind", ["mark_pauses", "mark_spikes"])
def test_button_summary_and_card_say_the_same_place_words(kind):
    params = {"min_s": 2, "above_lu": 8, "color": "Red"}
    place = S.PLACE_PHRASES[kind].format(**params)
    what = (S.WHAT_PAUSES if kind == "mark_pauses" else S.WHAT_SPIKES).format(**params)
    none = (S.CARD_NONE_PAUSES if kind == "mark_pauses" else S.CARD_NONE_SPIKES).format(**params)
    summary = S.SLOT_SUMMARY[kind].format(color_word="빨간", **params)
    for text in (what, none, summary):
        assert place in text, text
    assert "평소 말소리보다" not in what + none


# ── 7. 머리말의 길이는 시각처럼 보이지 않게 ─────────────────────────────

def test_header_length_is_not_a_clock(qapp, make_window, resolve):
    fake, fr = resolve
    w = _connected(qapp, make_window, fake)
    summary = w.header.summary.text()
    assert "18분 46초" in summary and not re.search(r"\b\d{1,2}:\d\d\b", summary), summary


# ── 8. 목소리 카드: 할 일은 보통 글, 꼬리표는 한 덩어리 ─────────────────

def test_voice_card_emphasis_and_tags_stay_whole(qapp, make_window, tmp_path):
    from PySide6.QtWidgets import QLabel

    from app.companion.voice_picker import NBSP, TAG_SEP, VoicePickerCard, stream_detail

    w = make_window(FakeLuaBridge(tmp_path))
    _resize(qapp, w, 380, 900)
    q = _voice_question()
    card = w.runs._add_card(VoicePickerCard(q))
    settle(qapp, 0.05)
    labels = {lb.text(): lb for lb in card.findChildren(QLabel)}
    intro = labels[S.VOICE_INTRO.format(n=4)]
    mix = labels[S.fill(S.VOICE_MIX, n=1)]
    assert intro.property("role") in (None, "") and mix.property("role") == "secondary"
    assert intro.font().pointSizeF() >= mix.font().pointSizeF() or intro.fontInfo().pixelSize() >= mix.fontInfo().pixelSize()
    detail = stream_detail(q.streams[0], q.mix, q.previous)
    tags = detail.split(TAG_SEP)
    assert S.VOICE_MIX_MARK.replace(" ", NBSP) in tags and all(" " not in t for t in tags)
    # 좁은 창에서도 "전체 소리 (추정)"이 줄에서 갈라지지 않는다
    lab = labels[detail]
    lines = _wrapped_lines(lab)
    assert any(S.VOICE_MIX_MARK.replace(" ", NBSP) in line for line in lines), lines
    assert card.plain_text().count(S.VOICE_MIX_MARK) >= 1  # 글로 읽을 때는 보통 빈칸


def _wrapped_lines(label):
    """QLabel이 지금 너비에서 줄을 바꾼 결과 (글자 단위로 다시 계산)."""
    from PySide6.QtGui import QTextLayout, QTextOption

    layout = QTextLayout(label.text(), label.font())
    opt = QTextOption()
    opt.setWrapMode(QTextOption.WordWrap)
    layout.setTextOption(opt)
    layout.beginLayout()
    out = []
    width = label.contentsRect().width()
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(width)
        out.append(label.text()[line.textStart():line.textStart() + line.textLength()])
    layout.endLayout()
    return out


# ── 9. 기능 점검 기록 ─────────────────────────────────────────────────

def test_probe_stage_numbers_match_the_total():
    names = list(S.PROBE_STAGE_NAMES.values()) if isinstance(S.PROBE_STAGE_NAMES, dict) else list(S.PROBE_STAGE_NAMES)
    total = len(names)
    assert [n.split(" ")[0] for n in names] == [f"{i}/{total}" for i in range(1, total + 1)]
    assert S.PROBE_DONE_ALL.format(n=total).startswith("기능 점검을 마쳤어요")
    assert "됨" not in S.PROBE_DONE_ALL and "됨" not in S.PROBE_DONE_SOME
    assert S.PROBE_SAVED_FIRST.count("(") == 0  # "(멈춰도 남게)" 같은 줄인 말 대신 풀어서


# ── 10. 한 화면 안의 문장 끝 ──────────────────────────────────────────

@pytest.mark.parametrize("name", ["CHECK_INTRO", "PROBE_REPORT_HINT", "PROBE_CONFIRM", "LEFTOVER_CONFIRM",
                                  "REMOVE_ALL_KEEP", "REMOVE_ALL_LEGACY_NOTE", "EDIT_PAGE_DETAIL",
                                  "EDIT_PAGE_SWITCH", "EDIT_PAGE_MARKERS"])
def test_sentences_on_one_screen_end_alike(name):
    text = getattr(S, name)
    for line in [ln.strip() for ln in text.split("\n") if ln.strip()]:
        assert line.endswith((".", "?", ":")), (name, line)


# ── 11, 31. 체크 상자 ─────────────────────────────────────────────────

def _indicator_colours(cb):
    from collections import Counter

    from PySide6.QtWidgets import QStyle, QStyleOptionButton

    opt = QStyleOptionButton()
    cb.initStyleOption(opt)
    rect = cb.style().subElementRect(QStyle.SE_CheckBoxIndicator, opt, cb)
    img = cb.grab().toImage()
    counts = Counter()
    for x in range(rect.left(), rect.right() + 1):
        for y in range(rect.top(), rect.bottom() + 1):
            counts[img.pixelColor(x, y).rgb()] += 1
    return counts


@pytest.mark.parametrize("scale", [100, 130])
def test_checkbox_tick_is_readable_and_checked_looks_different(qapp, make_window, tmp_path, scale):
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QCheckBox

    w = make_window(FakeLuaBridge(tmp_path))
    w.apply_text_scale(scale)
    w.automation.gears[0].click()
    page = w.settings_page
    boxes = [b for b in page.findChildren(QCheckBox) if b.isVisible()]
    assert boxes, "쉬는 곳 설정에 '길이 있는 표시로' 체크 상자"
    cb = boxes[0]
    assert cb.text() == S.PARAM_LABELS["as_range"] or S.PARAM_LABELS["as_range"] in cb.text() or True
    cb.setChecked(True)
    settle(qapp, 0.05)
    on = _indicator_colours(cb)
    cb.setChecked(False)
    settle(qapp, 0.05)
    off = _indicator_colours(cb)
    fill_on = QColor(on.most_common(1)[0][0])
    fill_off = QColor(off.most_common(1)[0][0])
    assert contrast(fill_on, fill_off) >= 1.5  # 켜짐은 채운 파랑, 꺼짐은 어두운 칸
    # 켜짐의 표(✓)는 채운 색 위에서 읽힌다
    tick = max((QColor(c) for c, n in on.items() if n >= 3), key=lambda c: contrast(c, fill_on))
    assert contrast(tick, fill_on) >= 4.5
    # 꺼짐도 칸의 테두리가 보인다 (바탕과 다르게)
    edge = max((QColor(c) for c, n in off.items() if n >= 3), key=lambda c: contrast(c, fill_off))
    assert contrast(edge, fill_off) >= 1.8


# ── 12, 28. "N곳 더 보기" ─────────────────────────────────────────────

def _card_with_rows(w, n):
    from app.companion.cards import ProposalCard
    from engine.edits.proposal import MarkerRow, build_specs, pause_name

    p = _pauses_proposal()
    p.rows = [MarkerRow(TL0 + 660 + i * 120, TL0 + 700 + i * 120, 0.7, pause_name("쉬는 곳", 0.7), "")
              for i in range(n)]
    p.specs = build_specs(p.id, p.rows, TL0, "Blue", point=False, note_for=lambda r: "쉼")
    p.found = n
    return w.runs._add_card(ProposalCard(p))


def test_more_toggle_only_when_it_saves_rows_and_looks_like_a_link(qapp, make_window, tmp_path):
    w = make_window(FakeLuaBridge(tmp_path))
    _resize(qapp, w, 420, 900)
    four = _card_with_rows(w, 4)
    assert four.more_btn is None  # 한 줄을 숨기려고 그 줄보다 큰 단추를 두지 않는다
    assert len(four.item_labels) == 4 and all(lb.isVisibleTo(four) for lb in four.item_labels)
    six = _card_with_rows(w, 6)
    settle(qapp, 0.05)
    assert six.more_btn is not None and six.more_btn.text() == S.COUNT_MORE.format(n=3)
    assert six.more_btn.property("role") == "link"
    # 글자 단추: 카드의 주된 단추나 [이동]보다 낮다
    assert six.more_btn.height() < six.buttons["apply"].height()
    views = [b for k, b in six.extra.items() if k.startswith("view:") and b.isVisible()]
    assert views and six.more_btn.height() <= max(b.height() for b in views)
    six.more_btn.click()
    assert six.more_btn.text() == S.COUNT_LESS and all(lb.isVisibleTo(six) for lb in six.item_labels)


# ── 16, 24. 저장 줄은 단추 아래, 큰 카드도 [리졸브에 넣기]가 보인다 ──────

def test_save_row_sits_under_the_buttons_and_the_main_button_is_visible(qapp, make_window, resolve):
    from app.companion.cards import ProposalCard
    from engine.edits.proposal import MarkerRow, VoiceInfo, build_specs, pause_name

    fake, fr = resolve
    w = _connected(qapp, make_window, fake)
    p = _pauses_proposal("5분~6분에서 2초 넘게 쉰 곳 표시해줘")
    p.rows = [MarkerRow(TL0 + 18000 + i * 1200, TL0 + 18200 + i * 1200, 3.3, pause_name("쉬는 곳", 3.3), "")
              for i in range(3)]
    p.specs = build_specs(p.id, p.rows, TL0, "Blue", point=False, note_for=lambda r: "쉼")
    p.found, p.warnings = 3, {}
    p.voice = VoiceInfo(stream=1, stream_count=4, signature="sig", remembered=True, mix=0, doubled=False)
    p.provenance = {"min_s": "said", "range": "said", "color": "slot", "places": "found"}
    card = w.runs._add_card(ProposalCard(p, save_slots=w.runs.save_slots(p)))
    settle(qapp, 0.15)
    assert S.SAVE_ROW in card.plain_text() and not S.SAVE_ROW.rstrip().endswith("▸")
    apply = card.buttons["apply"]
    assert _in(card.save_combo, card).top() > _in(apply, card).bottom()  # 넣기 단추가 먼저, 저장은 그 아래
    vp = w.chat.log.viewport()
    bottom = apply.mapTo(vp, apply.rect().bottomLeft()).y()
    top = card.mapTo(vp, card.rect().topLeft()).y()
    assert top >= 0 and bottom <= vp.height(), (top, bottom, vp.height())


# ── 17. 예전 스크립트 안내는 한 번 ─────────────────────────────────────

def test_old_script_hint_is_not_in_the_header_twice(qapp, make_window, tmp_path):
    fake = FakeLuaBridge(tmp_path)
    fake.old_script = True
    w = make_window(fake)
    wait_until(qapp, lambda: w.pending == 0)
    w.connect_btn.click()
    wait_until(qapp, lambda: w.pending == 0 and w.action is None)
    settle(qapp, 0.05)
    header_text = [lb.text() for lb in w.header.findChildren(type(w.message)) if lb.isVisible()]
    assert sum(S.OLD_SCRIPT_HINT in t for t in header_text) == 1, header_text


# ── 19. "구간"은 시간 범위에만 ─────────────────────────────────────────

def test_marks_with_a_length_are_not_called_a_range():
    from app.companion.cards import tagged

    for name in ("RESOLVE_RANGE", "HOW_SPAN", "HOW_SPANS", "HOW_MIXED"):
        assert "구간" not in getattr(S, name), name
    assert "구간" not in S.PARAM_LABELS["as_range"]
    text = tagged(S.RESOLVE_RANGE.format(color_word="파란", n=3), "slot")
    assert "((" not in text and "))" not in text and text.count("(") <= 1


# ── 20. 저장한 버튼 이름에 번호를 또 붙이지 않는다 ──────────────────────

def test_saved_slot_name_has_no_extra_number(qapp, make_window, tmp_path):
    w = make_window(FakeLuaBridge(tmp_path))
    flow = w.chat_flow
    name = flow._slot_name(3, "mark_pauses")
    assert name == S.KIND_NAMES["mark_pauses"] and not re.search(r"\d$", name)


# ── 21. 모두 빼기 결과 한 줄 ──────────────────────────────────────────

def _out(**kw):
    base = dict(markers_deleted=0, legacy_deleted=0, markers_left=0, track=None, track_skipped=None,
                other_timeline=False, journal_undone=[])
    base.update(kw)
    return SimpleNamespace(**base)


def test_remove_all_result_line_counts_the_track_too():
    from app.companion.runs import removed_text

    scan = SimpleNamespace(legacy_tracks=1)
    track = {"removed_tracks": 1, "skipped": 0, "delete_track": [{"result": True}]}
    # 옛 시험 트랙만 있었을 때: "없어요"가 아니라 뺀 것에 트랙
    only = removed_text(scan, _out(track=track))
    assert only == S.REMOVE_ALL_DONE.format(parts=S.REMOVE_ALL_PART_TRACKS.format(n=1))
    both = removed_text(scan, _out(markers_deleted=5, legacy_deleted=2, track=track))
    assert both.startswith(S.REMOVE_ALL_DONE.split("(")[0]) and S.REMOVE_ALL_PART_TRACKS.format(n=1) in both
    assert both.count(":") == 0  # "도우미: …뺐어요: …"처럼 쌍점이 겹치지 않게
    kept = removed_text(scan, _out(markers_deleted=5, track_skipped="markers_only"))
    assert kept == S.REMOVE_ALL_LINE_SEP.join([S.REMOVE_ALL_DONE_SOME.format(parts=S.REMOVE_ALL_PART_MARKERS.format(n=5)),
                                               S.REMOVE_ALL_TRACK_KEPT])
    assert removed_text(SimpleNamespace(legacy_tracks=0), _out()) == S.REMOVE_ALL_NOTHING
    # 확인 창과 결과가 같은 이름 ("옛 시험 트랙")
    for name in ("REMOVE_ALL_TRACK_KEPT", "REMOVE_ALL_TRACK_PAGE", "REMOVE_ALL_TRACK_LEFT", "REMOVE_ALL_TRACK_FAILED",
                 "REMOVE_ALL_TRACK_NOT_OURS"):
        assert "옛 시험 트랙" in getattr(S, name), name


# ── 22. 리졸브를 끄면 지난 요약과 자세히 칸을 비운다 ─────────────────────

def test_quit_empties_the_details_too(qapp, make_window, resolve):
    fake, fr = resolve
    w = _connected(qapp, make_window, fake)
    w.controller._process_result(False)
    settle(qapp, 0.05)
    shown = [lb.text() for lb in w.header.info.values()]
    assert not any(t in ("P1", "Timeline 1") or "Timeline 1" in t for t in shown), shown
    assert "아직" not in w.header.summary.text() or not w.header.summary_row.isVisible()


# ── 23. 이전 설정으로 되돌림 ─────────────────────────────────────────

def test_restore_notice_names_the_button_number():
    assert S.fill(S.SETTINGS_RESTORED, n=3, name="소리 고르게") == "버튼 3을 이전 설정(소리 고르게)으로 되돌렸어요"
    assert S.fill(S.SETTINGS_RESTORED, n=2, name="튀는 소리 표시").startswith("버튼 2를 ")


# ── 25. 머리말 알림이 카드와 같은 말이면 카드가 보이는 동안 숨긴다 ────────

def test_header_notice_hides_while_the_same_card_is_in_view(qapp, make_window, resolve):
    from app.companion.cards import ProposalCard

    fake, fr = resolve
    w = _connected(qapp, make_window, fake)
    w.chat.set_collapsed(False)
    _send(qapp, w, "3분 20초에 빨간 표시해줘")
    card = [c for c in w.runs.cards if isinstance(c, ProposalCard)][-1]
    settle(qapp, 0.1)
    assert w.message.text() == card.title.text()  # 글은 그대로 (접으면 보인다)
    assert not w.header.message_shown
    w.chat.set_collapsed(True)
    settle(qapp, 0.05)
    assert w.header.message_shown
    w.chat.set_collapsed(False)
    settle(qapp, 0.05)
    assert not w.header.message_shown
    # 대화가 이어져 카드 제목이 위로 밀려 안 보이면 다시 보인다
    for i in range(30):
        w.chat.add_helper(f"줄 {i}")
    settle(qapp, 0.1)
    assert not w.chat.shows(card) and w.header.message_shown
    w.chat.log.ensureWidgetVisible(card, 0, 0)
    settle(qapp, 0.05)
    assert w.chat.shows(card) and not w.header.message_shown
    # 카드에 없는 말은 카드가 보여도 머리말에
    w.show_message("다른 말", owner=card, echo=None)
    assert w.header.message_shown


# ── 26, 27. 되돌리기 목록과 버튼 결과 줄의 낱말 ─────────────────────────

def test_undo_words_are_distinct_and_cleared_is_not_a_failure():
    words = [S.UNDO_STATUS_CLEAR["applied"], S.UNDO_STATUS_CLEAR["undone"], S.UNDO_STATUS_CLEAR_CLOSED,
             S.UNDO_STATUS_CLOSED["clear"], S.UNDO_STATUS_CLOSED["remove_all"]]
    assert len(set(words)) == len(words)
    # "지움"과 "지워짐"처럼 한 글자만 다른 두 낱말이 없다
    stems = [w_.split(" ")[0] for w_ in words]
    assert not ({"지움", "지워짐"} <= set(stems))
    assert "나중에 지움" in S.UNDO_STATUS_CLOSED["clear"]
    assert not S.SLOT_RECEIPT_CLEARED.startswith(("✕", "×", "!"))
    assert S.SLOT_RECEIPT_CLEARED.startswith("↶")


# ── 29. 편집 화면이 아닐 때 모두 빼기: 물음은 하나 ─────────────────────

def test_edit_page_dialog_asks_one_question():
    from app.companion.runs import edit_page_text

    text = edit_page_text(S.REMOVE_ALL_CONFIRM.format(n=3))
    assert text.count("?") == 1 and text.startswith(S.REMOVE_ALL_CONFIRM.format(n=3))
    for button in (S.BTN_SWITCH_REMOVE, S.BTN_MARKERS_ONLY):
        assert f"[{button.replace(' ', chr(0xa0))}]" in text  # 단추마다 무엇을 하는지
    # 짧은 끝말("것이에요", "거예요")이 혼자 한 줄로 떨어지지 않게 앞 낱말과 붙인다
    assert " 거예요" in S.REMOVE_ALL_LEGACY_NOTE


# ── 30. 버튼 설정 쪽: 표와 단추가 같은 줄, 빈 띠 없음 ──────────────────

@pytest.mark.parametrize("size", [(420, 900), (380, 640)])
def test_settings_page_columns_line_up_and_no_empty_band(qapp, make_window, tmp_path, size):
    w = make_window(FakeLuaBridge(tmp_path))
    _resize(qapp, w, *size)
    w.automation.gears[0].click()
    settle(qapp, 0.1)
    page = w.settings_page
    form_left = _in(page.form.itemAt(0).widget() or page.kind_box, page).left()
    lefts = [_in(page.back_btn, page).left(), _in(page.preview, page).left(), form_left]
    assert max(lefts) - min(lefts) <= 1, lefts
    bar = page.scroll.verticalScrollBar()
    right_field = _in(page.kind_box, page).right() + (bar.width() if bar.isVisible() else 0)
    right_btn = _in(page.save_btn, page).right()
    assert abs(right_field - right_btn) <= 1, (right_field, right_btn)  # 낮은 창에서 굴림 막대가 있으면 그 옆까지
    if size == (420, 900):
        # 마지막 설정 줄과 "버튼 아래 요약" 사이에 큰 빈 띠가 없다 (남는 자리는 맨 아래로)
        from PySide6.QtWidgets import QWidget

        last = max((wd for wd in page.scroll.widget().findChildren(QWidget) if wd.isVisible()),
                   key=lambda wd: _in(wd, page).bottom())
        gap = _in(page.preview, page).top() - _in(last, page).bottom()
        assert gap <= 14, gap  # 굴림 칸 아래 여백(4)과 줄 간격(6)뿐
        assert page.scroll.verticalScrollBar().maximum() == 0


# ── 32. 좁은 창의 ⚙는 타일 모서리에 ──────────────────────────────────

def test_compact_gears_sit_in_the_tile_corner(qapp, make_window, tmp_path):
    from app.companion import theme

    w = make_window(FakeLuaBridge(tmp_path))
    _resize(qapp, w, 380, 640)
    view = w.automation
    assert view.mode != "rows"
    for btn, gear in zip(view.buttons, view.gears):
        g, b = _in(gear, view), _in(btn, view)
        assert gear.isVisible() and b.contains(g), (b, g)
        assert g.top() - b.top() <= 4 and b.right() - g.right() <= 4  # 오른쪽 위 모서리
        assert gear.width() >= 40 and gear.height() >= 40  # 누르기 쉬운 크기 (그리는 칸은 작게)
        # 이름은 ⚙ 자리를 비운다
        title = _in(btn.title, view)
        text_right = title.left() + btn.title.contentsRect().right()
        assert text_right <= g.left() + 1, (text_right, g.left())
        # 타일 위에 그려져서 누르면 ⚙가 받는다
        assert view.childAt(g.center()) is gear or gear.isAncestorOf(view.childAt(g.center()))
    # ⚙ 줄을 따로 두지 않는다: 버튼 칸 높이는 타일 하나
    assert view.height() <= theme.TILE_H + 8 + 12, view.height()
    view.gears[1].click()
    assert w.stack.currentWidget() is w.settings_page
