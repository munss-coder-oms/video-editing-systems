"""창을 써 보며 찾은 문제(1차)가 다시 생기지 않게 (offscreen, 가짜 리졸브).

머리말 알림(누가 띄웠고 언제 지우는지), 점검 도구의 기록과 결과 파일, 버튼 높이와 번호, 진행 줄,
되돌리기 목록의 낱말, 모두 빼기 글, 시각·길이 쓰기, 쉬는 곳 설정의 [−][+], 대화 뒤 칩.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from app.companion import strings_ko as S  # noqa: E402
from tests.fakes import FakeLuaBridge, FakeResolve, timeline_info  # noqa: E402
from tests.test_panel import connected_window, fake, make_window, qapp, resize, settle, wait_until  # noqa: E402,F401

TL0 = 216000
FPS = 60


def _idle(qapp, w, timeout=30.0):
    wait_until(qapp, lambda: not w.busy and w.pending == 0 and not w.chat_flow.active, timeout)


def _send(qapp, w, text):
    w.chat.input.setPlainText(text)
    w.chat.send_btn.click()
    _idle(qapp, w)


@pytest.fixture
def resolve(tmp_path):
    bridge = FakeLuaBridge(tmp_path)
    fr = FakeResolve(timeline_info())
    bridge.resolve = fr
    return bridge, fr


# ── 시각과 길이 ───────────────────────────────────────────────────────

def test_clock_time_and_length_words():
    from app.companion import fmt

    assert fmt.clock_time("14:02") == "오후 2:02"
    assert fmt.clock_time("09:05:33") == "오전 9:05"
    assert fmt.clock_time("2026-09-26T00:10:00") == "오전 12:10"
    assert fmt.clock_time("12:00") == "오후 12:00"
    assert fmt.clock_time("오후 2:02") == "오후 2:02"  # 두 번 불러도 그대로
    assert fmt.clock_time("") == ""
    assert fmt.length(120) == S.LEN_MIN.format(m=2)
    assert fmt.length(3600) == S.LEN_HOUR.format(h=1)
    assert fmt.length(1126) == "18분 46초"


# ── 머리말 알림 ───────────────────────────────────────────────────────

def test_background_checks_do_not_put_raw_lines_in_the_header(qapp, make_window, resolve):
    fake, fr = resolve
    w = make_window(fake)
    wait_until(qapp, lambda: w.connected and w.pending == 0)
    raw = re.compile(r"DaVinci Resolve|timeline_uid|fp=|[0-9a-f]{12}")
    assert not raw.search(w.message.text())  # 자동 확인은 기록에만
    w.connect_btn.click()
    wait_until(qapp, lambda: w.pending == 0 and w.action is None)
    assert w.message.text() == S.CONNECT_OK.format(timeline="Timeline 1")
    # 대화 전 확인(ping)도 알림을 바꾸지 않는다
    _send(qapp, w, "3분 20초로 가줘")
    assert not raw.search(w.message.text())
    # 리졸브가 꺼짐: "연결돼 있어요"를 지우고 지난 타임라인 요약도 지운다
    w.connect_btn.click()
    wait_until(qapp, lambda: w.pending == 0 and w.action is None)
    assert w.message.text() == S.CONNECT_OK.format(timeline="Timeline 1")
    w.controller._process_result(False)
    settle(qapp, 0.05)
    assert w.message.text() == "" and w.header.summary.text() == S.SUMMARY_NONE
    # 요약 줄과 자세히 칸은 숨기고 비운다 (리졸브가 꺼졌는데 지난 프로젝트·타임라인이 보이지 않게)
    assert not w.header.summary_row.isVisible() and not w.header.details.isVisible()
    assert all(label.text() == S.INFO_EMPTY for label in w.header.info.values())


def test_old_script_notice_says_what_to_click(qapp, make_window, fake):
    fake.old_script = True
    w = connected_window(qapp, make_window, fake)
    w.connect_btn.click()
    wait_until(qapp, lambda: w.pending == 0 and w.action is None)
    # 답은 했지만 예전 스크립트: "연결돼 있어요"가 아니라 무엇을 누를지. 머리말 안내 한 곳에만 (두 번 적지 않는다)
    assert w.header.hint.text() == S.OLD_SCRIPT_HINT and w.header.hint.isVisible()
    assert w.message.text() != S.OLD_SCRIPT_HINT and not w.message.isVisible()
    assert w.session.steps["connect"].ok is False
    # 다른 곳에서 같은 말을 알림으로 띄워도 안내와 같으면 숨긴다
    w.show_message(S.OLD_SCRIPT_HINT)
    assert not w.header.message_shown
    assert "Workspace → Scripts →" in S.OLD_SCRIPT_HINT and "Workspace(워크스페이스)" in S.CONNECT_HINT


def test_notice_from_a_card_goes_away_when_the_card_is_answered(qapp, make_window, fake):
    from app.companion.cards import QuestionCard

    w = connected_window(qapp, make_window, fake)
    card = w.runs._add_card(QuestionCard("바꿀까요?", [], [("yes", "예", True), ("no", "아니요", False)]))
    w.show_message("바꿀까요?", owner=card)
    other = w.runs._add_card(QuestionCard("다른 질문", [], [("ok", "예", True)]))
    other.answered("다른 답")
    assert w.message.text() == "바꿀까요?"  # 다른 카드가 닫혀도 그대로
    card.answered(S.MANUAL_ANSWERED.format(answer="예"))
    assert w.message.text() == S.MANUAL_ANSWERED.format(answer="예")
    card2 = w.runs._add_card(QuestionCard("또 물음", [], [("ok", "예", True)]))
    w.show_message("또 물음", owner=card2)
    card2.close_card()
    assert w.message.text() == ""


# ── 점검 도구 ─────────────────────────────────────────────────────────

def test_probe_log_is_plain_and_the_report_is_saved_again(qapp, make_window, fake):
    def probe_copy(args):
        stage = args.get("stage")
        if stage == "C8":
            return {"ok": True, "stage": "C8", "detail": {"copy_found": False}, "calls": {}}
        return {"ok": True, "stage": stage, "detail": {"fingerprint": "a1b2c3d4e5f6a7b8"}, "calls": {}}

    fake.handlers.update(probe_copy=probe_copy)
    w = connected_window(qapp, make_window, fake)
    w.confirm = lambda *a: True
    w.show_check_page()
    w.probe_btn.click()
    wait_until(qapp, lambda: w.action is None and w.pending == 0 and not w.report_pending, 30)
    log = w.check_page.log_view.toPlainText()
    assert "a1b2c3d4e5f6" not in log and "fingerprint" not in log
    assert not any(line.rstrip().endswith((" -", " ·", ":")) for line in log.splitlines())
    first = [line for line in log.splitlines() if line.startswith(S.PROBE_SAVED_FIRST.split("{")[0])]
    assert len(first) == 1
    run = w.session.probe_runs[-1]
    stages = len([r for r in run["stages"].values() if isinstance(r, dict)]) + 1  # + 정리
    total = stages + (1 if isinstance(run.get("read"), dict) else 0)
    # 알림은 점검 도구 쪽 알림 줄에 (본 쪽 머리말이 아니라). 단계 번호의 끝(n/9)과 마친 뒤의 수가 같다
    notice = w.check_page.notice.text()
    assert S.PROBE_DONE_ALL.format(n=total) in notice and w.check_page.notice.isVisible()
    assert S.PROBE_DONE_ALL.format(n=total) not in w.message.text()
    assert total == len(S.PROBE_STAGE_NAMES) and all(f"/{total} " in v for v in S.PROBE_STAGE_NAMES.values())
    # 마친 줄은 "기능 점검: 됨 · 기능 점검을 마쳤어요…"처럼 일 이름을 되풀이하지 않는다
    assert S.PROBE_DONE_ALL.format(n=total) in log.splitlines()
    # 점검 전에 저장한 파일을 점검 결과로 덮어쓰고 위치를 알린다 (보내 달라는 말과 함께)
    path = w.last_report
    assert path is not None and str(path) in notice and S.PROBE_REPORT_SAVED.split("{")[0] in notice
    assert "[기능 점검]" in path.read_text(encoding="utf-8-sig")
    assert str(path) in first[0]
    # 결과를 보내는 곳: 점검 도구에 [결과 저장]과 받는 사람
    page = w.check_page
    assert page.report_btn.text() == S.BTN_REPORT and " " in page.report_hint.text()
    assert page.old_tools.text() == S.CHECK_OLD_TOOLS


def test_step_line_without_summary_has_no_dangling_dash(qapp, make_window, fake):
    w = make_window(fake)
    assert w._step_line("connect", True, "") == S.STEP_LINE_BARE.format(title=S.STEP_TITLES["connect"],
                                                                         result=S.RESULT_OK)
    assert not w._step_line("connect", False, "").endswith(("-", "·", " "))


# ── 자동화 버튼 ───────────────────────────────────────────────────────

def test_slot_buttons_are_numbered_and_keep_their_height_after_save(qapp, make_window, fake):
    w = connected_window(qapp, make_window, fake)
    resize(qapp, w, 420, 900)
    assert w.layout_name == "full"
    first = w.automation.buttons[0]
    assert first.title.text() == S.SLOT_TITLE.format(n=1, name=first.name)
    assert all(b.height() >= 60 for b in w.automation.buttons)
    w.automation.gears[0].click()
    page = w.settings_page
    assert page.title.text().startswith("1 · ")
    page.name_edit.setText("긴 쉼")
    page.save_btn.click()
    settle(qapp, 0.1)
    assert w.automation.buttons[0].title.text() == "1 · 긴 쉼"
    assert all(b.height() >= 60 for b in w.automation.buttons)
    three = w.automation.buttons[2]
    assert not three.isEnabled() and three.property("kind") == "slot"


def test_progress_row_has_a_background_and_the_free_line_apart(qapp, make_window, fake):
    from PySide6.QtCore import Qt

    w = connected_window(qapp, make_window, fake)
    line = S.PROGRESS_SLOT.format(name="쉬는 곳 표시", line=S.PROGRESS_LINE.format(i=1, n=3, stage="소리 꺼내는 중"))
    w.automation.show_progress(1, line, 0.3, True, note=S.PROGRESS_FREE)
    settle(qapp)
    row = w.automation.progress
    assert row.testAttribute(Qt.WA_StyledBackground) and row.isVisible()
    assert row.label.text() == "쉬는 곳 표시 · 1/3 단계: 소리 꺼내는 중"
    assert row.note.text() == S.PROGRESS_FREE and row.note.isVisible()
    w.automation.hide_progress()


def test_settings_page_steppers_and_colour_names(qapp, make_window, fake):
    from PySide6.QtWidgets import QScrollArea

    w = connected_window(qapp, make_window, fake)
    w.automation.gears[0].click()
    page = w.settings_page
    spin = page.fields["min_s"]
    host = spin.steppers
    # [−] 값 [+]: 단추는 40×40, 값 칸도 40 높이, 셋 다 줄 안에 다 들어간다 (아래 테두리가 잘리지 않게)
    for part in (host.minus, spin, host.plus):
        assert part.height() == 40 and part.y() >= 0 and part.y() + part.height() <= host.height(), part
    assert host.minus.width() == 40 and host.plus.width() == 40
    before = page.values()["min_s"]
    host.plus.click()
    assert page.values()["min_s"] > before
    host.minus.click()
    assert page.values()["min_s"] == before
    colors = page.fields["color"]
    assert [colors.itemText(i) for i in range(colors.count())][:2] == [S.COLOR_NAMES["Blue"], S.COLOR_NAMES["Red"]] \
        or S.COLOR_NAMES.get(colors.currentData()) == colors.currentText()
    assert page.findChild(QScrollArea).frameShape() == 0  # 테두리 없음


# ── 되돌리기 목록 ─────────────────────────────────────────────────────

def test_undo_list_words_follow_the_job_and_the_reason():
    from app.companion.undo_view import status_word

    assert status_word({"status": "applied"}) == S.UNDO_STATUS["applied"]
    assert status_word({"status": "undone"}) == S.UNDO_STATUS["undone"]
    assert status_word({"status": "applied", "op": "clear_marks"}) == "지움"
    assert status_word({"status": "undone", "op": "clear_marks"}) == "지운 것 다시 넣음"
    # 넣은 일이 다른 일로 빠짐: 지우기 요청 줄의 "지움"과 헷갈리지 않는 말
    assert status_word({"status": "undone", "closed_by": "remove_all"}) == "넣었다가 모두 빼기로 뺌"
    assert status_word({"status": "undone", "closed_by": "clear:Pabc"}) == "넣었다가 나중에 지움"
    # 지우기 요청을 모두 빼기가 닫음: 모두 빼기가 뺀 것이 아니라 이제 되돌릴 수 없을 뿐
    assert status_word({"status": "undone", "op": "clear_marks", "closed_by": "remove_all"}) == \
        "지움 · 이제 되돌릴 수 없음"
    words = [status_word(e) for e in (
        {"status": "applied", "op": "clear_marks"}, {"status": "undone", "closed_by": "clear:P"},
        {"status": "undone", "closed_by": "remove_all"}, {"status": "undone", "op": "clear_marks"})]
    assert len(set(words)) == len(words)


def test_undo_footer_buttons_are_40_high_without_menu_arrow(qapp, make_window, fake):
    w = connected_window(qapp, make_window, fake)
    resize(qapp, w, 420, 900)
    heights = {b.height() for b in (w.footer.undo_btn, w.footer.report_btn, w.header.check_btn, w.header.more_btn)}
    assert len(heights) == 1 and 40 <= heights.pop() <= 44  # 머리말·아래쪽 단추는 모두 같은 높이
    assert "menu-indicator" in w.styleSheet()


def test_remove_all_text_leaves_out_zero_counts():
    from app.companion.runs import _parts, remove_all_text

    assert _parts(3, 0, 0) == S.REMOVE_ALL_PART_MARKERS.format(n=3)
    assert _parts(0, 2, 1) == S.REMOVE_ALL_PART_LEGACY.format(n=2) + S.REMOVE_ALL_PART_SEP + \
        S.REMOVE_ALL_PART_TRACKS.format(n=1)
    text = remove_all_text(SimpleNamespace(total=3, markers=3, legacy_markers=0, legacy_tracks=[]))
    assert "0개" not in text and S.REMOVE_ALL_LEGACY_NOTE not in text and text.endswith(S.REMOVE_ALL_KEEP)
    assert text.splitlines()[0] == S.REMOVE_ALL_CONFIRM.format(n=3)


# ── 대화 ──────────────────────────────────────────────────────────────

def test_after_apply_chips_once_and_undo_note_on_the_question(qapp, make_window, resolve):
    from app.companion.cards import ProposalCard, QuestionCard

    fake, fr = resolve
    w = make_window(fake)
    wait_until(qapp, lambda: w.connected and w.pending == 0)
    for text in ("1분에 빨간 표시해줘", "2분에 파란 표시해줘"):
        _send(qapp, w, text)
        card = [c for c in w.runs.cards if isinstance(c, ProposalCard)][-1]
        card.buttons["apply"].click()
        wait_until(qapp, lambda: card.state == "receipt" and not w.busy, 20)
    log = w.chat.log.toPlainText()
    assert log.count(S.CHAT_TRY_AFTER_APPLY) == 1 and S.CHIPS_AFTER_APPLY[-1] == "도우미가 넣은 표시 다 지워줘"
    _send(qapp, w, "방금 거 취소")
    q = [c for c in w.runs.cards if isinstance(c, QuestionCard) and c.title.text() == S.CARD_TITLE_UNDO][-1]
    assert "'2분에 파란 표시해줘'로 넣은 파란 표시 1개를 빼요" in q.plain_text()
    w.show_message(q.title.text(), owner=q)
    q.buttons["remove"].click()
    assert S.UNDO_STARTED in q.plain_text()
    wait_until(qapp, lambda: card.state == "undone" and not w.busy, 20)
    assert card.title.text() in q.plain_text()  # 물었던 카드 아래에 뺀 결과
    assert S.UNDO_STARTED not in q.plain_text()  # "빼는 중이에요" 줄이 결과로 바뀐다 (끝난 뒤에 남지 않게)
    assert w.message.text() != S.CARD_TITLE_UNDO


def test_save_slot_choice_follows_renamed_buttons(qapp, make_window, fake):
    from app.companion.cards import ProposalCard
    from tests.test_card_fit import _pauses_proposal

    w = connected_window(qapp, make_window, fake)
    p = _pauses_proposal()
    card = w.runs._add_card(ProposalCard(p, save_slots=w.runs.save_slots(p)))
    card.choose_save_slot(3)
    w.automation.gears[0].click()
    w.settings_page.name_edit.setText("긴 쉼")
    w.settings_page.save_btn.click()
    settle(qapp, 0.05)
    labels = [card.save_combo.itemText(i) for i in range(card.save_combo.count())]
    assert any("긴 쉼" in label for label in labels)
    assert card.save_combo.currentData() == 3


def test_first_screen_words():
    assert S.DETAILS_SHOW == "자세히 ▸" and S.CHAT_BRAIN_LINE == "기본 도우미가 답해요 (AI 아님 · 무료)"
    assert S.CHAT_TRY_CONNECTED.endswith(":") and "2초 넘게 쉰 곳 표시해줘" in S.CHIPS_AFTER_CONNECT
    assert "LUFS" not in S.SLOT_SUMMARY["balance_voice"]
    assert S.MENU_CHECK_PAGE == S.CHECK_TITLE == "점검 도구"
    assert "도우미 창" in S.UPDATE_CONFIRM and (" " in S.UPDATE_CONFIRM or "\n" in S.UPDATE_CONFIRM)
    assert S.UNDO_HINT == "도우미가 넣은 것은 리졸브의 Ctrl+Z 말고 여기서 빼 주세요"
    assert S.REMOVE_ALL_CONFIRM.format(n=4) == "이 타임라인에서 도우미가 넣은 것 4개를 모두 뺄까요?"
    for text in (S.REPORT_SAVED, S.REPORT_FAILED, S.NON_ASCII_MAILBOX, S.UNREAD_TEXT, S.SLOW_ANSWER_TEXT,
                 S.OLD_SCRIPT_LOG, S.PARAM_HELP["merge_s"], S.CHECK_INTRO):
        assert "습니다" not in text and "십시오" not in text


def test_clear_that_empties_a_card_says_so_everywhere(qapp, make_window, resolve):
    """대화로 넣은 표시를 대화로 모두 지움: 넣은 카드는 "지워짐", 지우기 카드는 몇 개의 일이 끝났는지,
    되돌리기 목록은 넣은 줄 "지워짐" · 지운 줄 "지움"."""
    from app.companion.cards import ClearCard, ProposalCard

    fake, fr = resolve
    w = make_window(fake)
    wait_until(qapp, lambda: w.connected and w.pending == 0)
    _send(qapp, w, "1분에 파란 표시해줘")
    mark = [c for c in w.runs.cards if isinstance(c, ProposalCard)][-1]
    mark.buttons["apply"].click()
    wait_until(qapp, lambda: mark.state == "receipt" and not w.busy, 20)
    _send(qapp, w, "도우미가 넣은 파란 표시 지워줘")
    clear = [c for c in w.runs.cards if isinstance(c, ClearCard)][-1]
    clear.buttons["apply"].click()
    wait_until(qapp, lambda: clear.state == "receipt" and not w.busy, 20)
    assert S.CLEAR_CLOSED.format(n=1) in clear.plain_text()
    assert mark.state == "undone" and S.CARD_CLEARED in mark.plain_text() and mark.buttons == {}
    entries = w.footer.entry_texts()
    assert entries[0].endswith(f"({S.UNDO_STATUS_CLEAR['applied']})")
    assert entries[1].endswith(f"({S.UNDO_STATUS_CLOSED['clear']})")
    assert re.search(r"^오[전후] \d{1,2}:\d\d ", entries[0])  # 목록의 시각도 오전·오후
