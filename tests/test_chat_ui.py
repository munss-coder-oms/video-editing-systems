"""대화 칸의 흐름을 창에서 (설계 B4, B11 UI "Chat send → card → inline edit → apply"). offscreen, 가짜 리졸브.

- 보냄 → (시간을 풀어야 하면 ping + timeline_info) → 확인 카드 → [−][+] → [리졸브에 넣기] → 영수증 → 방금 거 취소.
- 리졸브를 바꾸는 요청(add_markers, delete_markers)은 카드의 단추를 누른 뒤에만 나간다.
  재생 위치 옮기기(jump_to)만 확인 없이 한다 (편집이 아니다).
- 칩은 입력 칸을 채우기만 하고 보내지 않는다. 못 알아들은 부분은 카드에 주황 줄로, [나머지도 다시 말하기]로 채운다.
- 도우미 표시 지우기는 도우미가 넣은 것만, 카드에 보인 것만 지우고 되돌리면 다시 넣는다.
- 대화의 쉬는 곳 표시는 말한 범위 안쪽만 넣고, 영수증 아래 "이대로 자동화 버튼에 저장 ▸"은 구간을 버리고 저장한다.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from app.companion import strings_ko as S  # noqa: E402
from engine.analysis_cache import AnalysisCache  # noqa: E402
from engine.edits.journal import Journal  # noqa: E402
from engine.probe import probe  # noqa: E402
from engine.timeline.audio_map import layout_signature  # noqa: E402
from tests.conftest import requires_ffmpeg  # noqa: E402
from tests.fakes import FakeLuaBridge, FakeResolve, obs_items, timeline_info  # noqa: E402
from tests.test_panel import make_window, qapp, settle, wait_until  # noqa: E402,F401

TL0 = 216000
FPS = 60
MUTATING = {"add_markers", "delete_markers", "remove_audio", "probe_copy", "switch_timeline"}


@pytest.fixture
def resolve(tmp_path):
    fake = FakeLuaBridge(tmp_path)
    fr = FakeResolve(timeline_info())
    fr.add_user_marker(600, custom="", color="Blue", name="내 파란 표시")
    fake.resolve = fr
    return fake, fr


def _window(qapp, make_window, fake):
    w = make_window(fake)
    wait_until(qapp, lambda: w.connected and w.pending == 0)
    return w


def _send(qapp, w, text, timeout=30.0):
    w.chat.input.setPlainText(text)
    w.chat.send_btn.click()
    _idle(qapp, w, timeout)


def _idle(qapp, w, timeout=30.0):
    wait_until(qapp, lambda: not w.busy and w.pending == 0 and not w.chat_flow.active, timeout)


def _cards(w, cls):
    return [c for c in w.runs.cards if isinstance(c, cls)]


def _mutating(fake, since=0):
    return [op for op in fake.requests[since:] if op in MUTATING]


def _ours(fr):
    return {f: m for f, m in fr.markers.items() if str(m.get("custom")).startswith("aih:")}


def test_mark_card_inline_edit_apply_then_undo_last(qapp, make_window, resolve):
    from app.companion.cards import ProposalCard, QuestionCard

    fake, fr = resolve
    w = _window(qapp, make_window, fake)
    before = len(fake.requests)
    _send(qapp, w, "3분 20초에 빨간 표시해줘, 메모는 '자막 확인'")
    assert fake.requests[before:] == ["ping", "timeline_info"]  # 타임라인만 읽었다
    card = _cards(w, ProposalCard)[-1]
    p = card.proposal
    assert p.kind == "mark" and p.count == 1 and p.specs[0].color == "Red" and p.specs[0].name == "자막 확인"
    assert p.rows[0].start == TL0 + 200 * FPS
    text = card.plain_text()
    assert card.title.text() == S.CARD_TITLE_PROPOSE
    assert S.TAGGED.format(value="3:20.0", source=S.PROVENANCE["said"]) in text  # 언제 · 말씀하신 값
    assert S.ITEM_SOURCE.format(at=S.PROVENANCE["said"], color=S.PROVENANCE["said"], name=S.PROVENANCE["said"]) in text
    assert "01:03:20:00" in text and S.KEEP_MARKERS in text
    assert card.buttons["apply"].isEnabled() and not card.blocked

    # [+] 0.1초: 두뇌도 리졸브도 다시 부르지 않고 고친다
    n = len(fake.requests)
    card.extra["inc:at:0"].click()
    card.extra["inc:at:0"].click()
    card.extra["dec:at:0"].click()
    assert len(fake.requests) == n
    assert card.proposal.rows[0].start == TL0 + 200 * FPS + 6 and card.proposal.id == p.id
    assert "3:20.1" in card.plain_text() and not card.blocked
    assert _mutating(fake) == [] and _ours(fr) == {}

    card.buttons["apply"].click()
    wait_until(qapp, lambda: card.state == "receipt" and not w.busy, 20)
    ours = _ours(fr)
    assert list(ours) == [200 * FPS + 6]
    m = ours[200 * FPS + 6]
    assert m["color"] == "Red" and m["name"] == "자막 확인" and m["note"].endswith("· AI 도우미")
    assert card.title.text().startswith("✓") and "빨간 표시 1개" in card.title.text()
    assert 600 in fr.markers  # 내 표시는 그대로
    assert len(w.footer.entries) == 1

    # 방금 거 취소 → 확인 카드 → [빼기]
    since = len(fake.requests)
    _send(qapp, w, "방금 거 취소")
    assert _mutating(fake, since) == []  # 묻기 전에는 빼지 않는다
    q = [c for c in _cards(w, QuestionCard) if c.title.text() == S.CARD_TITLE_UNDO][-1]
    assert S.UNDO_WHAT_MARKS.format(request="3분 20초에 빨간 표시해줘, 메모는 '자막 확인'", n=1) in q.plain_text()
    q.buttons["remove"].click()
    wait_until(qapp, lambda: card.state == "undone" and not w.busy, 20)
    assert _ours(fr) == {} and 600 in fr.markers
    report = w.report_text({})
    assert "[대화]" in report and S.CHAT_BRAIN_LINE in report and '"event": "undo"' in report
    assert "[대화]\n" + S.CHAT_BRAIN_LINE + "\n" in report  # 한 번만 ("답하는 쪽: 답하는 쪽: …"이 아니게)


def test_chip_fills_the_input_but_does_not_send(qapp, make_window, resolve):
    fake, fr = resolve
    w = _window(qapp, make_window, fake)
    before = len(fake.requests)
    _send(qapp, w, "음 그거 있잖아")
    row = w.chat.last_chips
    assert row is not None and len(row.chips) == 3
    sent = []
    w.chat.sent.connect(sent.append)
    lines = w.chat.log.toPlainText().count(f"{S.CHAT_ME}:")
    row.chips[0].click()
    settle(qapp, 0.1)
    assert w.chat.input.toPlainText() == S.CHIPS["example:mark_time"].format(time="3분 20초")
    assert sent == [] and w.chat.log.toPlainText().count(f"{S.CHAT_ME}:") == lines
    assert len(fake.requests) == before  # 못 알아들은 말에는 리졸브에 묻지 않는다


def test_leftover_line_and_retell_chip(qapp, make_window, resolve):
    from app.companion.cards import ProposalCard

    fake, fr = resolve
    w = _window(qapp, make_window, fake)
    _send(qapp, w, "3분 20초에 빨간 표시하고 5분에 주황 표시")
    card = _cards(w, ProposalCard)[-1]
    assert card.proposal.count == 1
    text = card.plain_text()
    assert S.GUARD["leftover"].format(text="5분에 주황 표시") in text
    assert card.buttons["apply"].isEnabled()  # 알아들은 줄만 넣을 수 있다
    card.extra["leftover"].click()
    assert w.chat.input.toPlainText() == "5분에 주황 표시"


def test_jump_moves_the_playhead_without_a_card(qapp, make_window, resolve):
    from app.companion.cards import Card

    fake, fr = resolve
    w = _window(qapp, make_window, fake)
    cards = len(w.runs.cards)
    _send(qapp, w, "3분 20초로 가줘")
    assert fr.jumps == [(TL0 + 200 * FPS, "01:03:20:00")]
    assert len(w.runs.cards) == cards and _mutating(fake) == []
    assert S.CHAT_JUMP_DONE.format(at="3:20.0", tc="01:03:20:00") in w.chat.log.toPlainText()
    fr.info["page"] = "media"
    _send(qapp, w, "처음으로 가줘")
    assert len(fr.jumps) == 1 and S.CHAT_JUMP_PAGE in w.chat.log.toPlainText()
    assert all(isinstance(c, Card) for c in w.runs.cards)
    report = w.report_text({})
    assert '"readback_tc": "01:03:20:00"' in report and '"reason": "page"' in report


def test_clear_blue_removes_only_ours_and_undo_puts_them_back(qapp, make_window, resolve):
    from app.companion.cards import ClearCard, ProposalCard

    fake, fr = resolve
    w = _window(qapp, make_window, fake)
    _send(qapp, w, "1분에 파란 표시, 2분에 파란 표시, 3분에 빨간 표시")
    mark = _cards(w, ProposalCard)[-1]
    assert mark.proposal.count == 3
    mark.buttons["apply"].click()
    wait_until(qapp, lambda: mark.state == "receipt" and not w.busy, 20)
    assert len(_ours(fr)) == 3
    fr.markers[5000] = {"color": "Blue", "name": "자막 도구", "note": "", "duration": 1, "custom": "autosubs_1"}

    since = len(fake.requests)
    _send(qapp, w, "도우미가 넣은 파란 표시 지워줘")
    assert _mutating(fake, since) == []
    card = _cards(w, ClearCard)[-1]
    assert card.proposal.count == 2 and set(card.proposal.colors) == {"Blue"}
    text = card.plain_text()
    assert card.title.text() == S.CARD_TITLE_CLEAR and S.CLEAR_RESOLVE.format(n=2) in text
    # 범위를 말하지 않은 지우기는 타임라인 전체에 적용된다: 설계 B4의 "전체" 경고 (막지는 않는다)
    assert S.GUARD["whole"].format(length="18분 46초") in text and card.buttons["apply"].isEnabled()
    card.buttons["apply"].click()
    wait_until(qapp, lambda: card.state == "receipt" and not w.busy, 20)
    ours = _ours(fr)
    assert [m["color"] for m in ours.values()] == ["Red"]
    assert fr.markers[600]["name"] == "내 파란 표시" and fr.markers[5000]["custom"] == "autosubs_1"
    assert card.title.text().startswith("✓") and S.CLEAR_RECEIPT.split("·")[1].strip().format(n=2) in card.title.text()
    args = [a for op, a in zip(fake.requests, fake.args) if op == "delete_markers"][-1]
    assert args["prefix"] == "aih:" and len(args["customs"]) == 2 and args["snapshot"] is True

    card.buttons["undo"].click()
    wait_until(qapp, lambda: card.state == "undone" and not w.busy, 20)
    ours = _ours(fr)
    assert sorted(m["color"] for m in ours.values()) == ["Blue", "Blue", "Red"]
    assert sorted(ours) == [60 * FPS, 120 * FPS, 180 * FPS]
    info = w._timeline_info
    entries = Journal.for_timeline(info, w.state_root).entries
    assert [e["status"] for e in entries] == ["applied", "undone"]


@requires_ffmpeg
def test_range_pauses_card_clips_saves_slot_and_view_jumps(qapp, make_window, tmp_path, obs_video):
    from app.companion.cards import ProposalCard

    fake = FakeLuaBridge(tmp_path)
    info = timeline_info(start_frame=108000, end_frame=108000 + 900, fps="30", current_tc="01:00:00:00")
    fr = FakeResolve(info, obs_items(str(obs_video), 108000, 900, clip_fps="30"))
    fake.resolve = fr
    w = make_window(fake)
    w.runs.cache = AnalysisCache(tmp_path / "cache")
    w.settings.set_voice(layout_signature(probe(str(obs_video))), 1, mix=0)
    wait_until(qapp, lambda: w.connected and w.pending == 0)
    _send(qapp, w, "11초~21.5초에서 0.5초 넘게 쉰 곳 표시해줘", timeout=90)
    card = _cards(w, ProposalCard)[-1]
    p = card.proposal
    assert p.kind == "mark_pauses" and p.from_chat and p.params["min_s"] == 0.5
    assert p.scope["kind"] == "range" and p.scope["lo"] == 108000 + 330 and p.scope["hi"] == 108000 + 645
    assert len(p.rows) == 2 and p.rows[0].start == 108000 + 330 and p.rows[1].end == 108000 + 645
    text = card.plain_text()
    assert S.TAGGED.format(value=S.WHEN_RANGE.format(a="0:11.0", b="0:21.5"), source=S.PROVENANCE["said"]) in text
    assert S.TAGGED.format(value=S.COUNT_LINE.format(n=2), source=S.PROVENANCE["found"]) in text
    assert not card.blocked and _mutating(fake) == []

    # [+] 쉰 길이: 다시 계산 (리졸브는 읽기만), 같은 카드가 바뀐다
    card.extra["inc:min_s"].click()
    wait_until(qapp, lambda: not w.busy and card.proposal.params["min_s"] == 0.6, 60)
    assert card.proposal.from_chat and card.proposal.scope["kind"] == "range" and _mutating(fake) == []
    card.extra["dec:min_s"].click()
    wait_until(qapp, lambda: not w.busy and card.proposal.params["min_s"] == 0.5, 60)

    card.buttons["apply"].click()
    wait_until(qapp, lambda: card.state == "receipt" and not w.busy, 30)
    frames = sorted(f + 108000 for f in _ours(fr))
    assert len(frames) == 2 and all(108000 + 330 <= f < 108000 + 645 for f in frames)
    run = [r for r in w.session.runs if r.get("stage") == "apply"][-1]
    assert run["outside_range"] == 0 and run["chat"] is True

    # 영수증의 줄을 누르면 재생 위치가 그곳으로 (편집 아님)
    card.item_labels[1].clicked.emit()
    wait_until(qapp, lambda: fr.jumps, 10)
    assert fr.jumps[-1][0] == card.proposal.rows[1].start

    # 이대로 자동화 버튼에 저장 ▸ 3 → [저장]: 구간은 버린다
    card.choose_save_slot(3)
    card.extra["save"].click()
    slot = w.settings.slot(3)
    assert slot["kind"] == "mark_pauses" and slot["params"]["min_s"] == 0.5 and "range" not in slot["params"]
    assert slot["previous"]["kind"] == "balance_voice"
    log = w.chat.log.toPlainText()
    assert S.SAVE_DONE.format(n=3) in log and S.SAVE_RANGE_DROPPED.format(a="0:11.0", b="0:21.5") in log
    assert w.automation.buttons[2].name == S.KIND_NAMES["mark_pauses"]
    assert w.settings.restore_previous(3) and w.settings.slot(3)["kind"] == "balance_voice"


def test_cut_offer_is_a_purple_marker_card_and_can_be_declined(qapp, make_window, resolve):
    from app.companion.cards import ProposalCard

    fake, fr = resolve
    w = _window(qapp, make_window, fake)
    _send(qapp, w, "여기서 잘라줘")
    card = _cards(w, ProposalCard)[-1]
    assert card.title.text() == S.OFFER_TITLES["cut"]
    assert card.buttons["apply"].text() == S.BTN_OFFER_YES and card.buttons["cancel"].text() == S.BTN_OFFER_NO
    p = card.proposal
    assert p.offer == "cut" and p.specs[0].color == "Purple" and p.rows[0].start == TL0 + 10 * FPS  # 재생 위치
    card.buttons["cancel"].click()
    assert S.CHAT_OFFER_DECLINED in card.plain_text() and _mutating(fake) == []


def test_audio_soft_refusal_offers_a_yellow_marker_with_db_steps(qapp, make_window, resolve):
    from app.companion.cards import ProposalCard

    fake, fr = resolve
    w = _window(qapp, make_window, fake)
    _send(qapp, w, "3분 15초~3분 25초 6dB 줄여줘")
    card = _cards(w, ProposalCard)[-1]
    p = card.proposal
    assert card.title.text() == S.OFFER_TITLES["audio"] and p.offer == "audio"
    assert p.specs[0].color == "Yellow" and p.specs[0].name == "여기 6dB 줄이기"
    card.extra["dec:db"].click()
    assert card.proposal.specs[0].name == "여기 5dB 줄이기" and card.proposal.id == p.id
    card.buttons["apply"].click()
    wait_until(qapp, lambda: card.state == "receipt" and not w.busy, 20)
    (m,) = _ours(fr).values()
    assert m["color"] == "Yellow" and m["name"] == "여기 5dB 줄이기"


def test_over_12db_asks_first(qapp, make_window, resolve):
    from app.companion.cards import ProposalCard, QuestionCard

    fake, fr = resolve
    w = _window(qapp, make_window, fake)
    _send(qapp, w, "3분에 20dB 키워줘")
    q = _cards(w, QuestionCard)[-1]
    assert q.title.text() == S.CHAT_QUESTIONS["over_gain"]
    q.buttons["yes"].click()
    settle(qapp, 0.1)
    card = _cards(w, ProposalCard)[-1]
    assert card.proposal.specs[0].name == "여기 12dB 키우기" and _mutating(fake) == []


def test_status_help_and_nothing_to_undo(qapp, make_window, resolve):
    fake, fr = resolve
    w = _window(qapp, make_window, fake)
    _send(qapp, w, "지금 타임라인 몇 분이야?")
    log = w.chat.log.toPlainText()
    assert "Timeline 1 · 길이 18분 46초 · 60fps · 재생 위치 0:10.0 (01:00:10:00)" in log
    _send(qapp, w, "뭐 할 수 있어?")
    assert S.CHAT_HELP in w.chat.log.toPlainText()
    _send(qapp, w, "방금 거 취소")
    assert S.CHAT_UNDO_NONE in w.chat.log.toPlainText() and _mutating(fake) == []


def test_messages_sent_while_busy_wait_their_turn(qapp, make_window, resolve):
    from app.companion.cards import ProposalCard

    fake, fr = resolve
    w = _window(qapp, make_window, fake)
    w.action = "slot"  # 다른 일을 하는 중
    w.chat.input.setPlainText("1분에 표시해줘")
    w.chat.send_btn.click()
    settle(qapp, 0.1)
    assert S.CHAT_BUSY in w.chat.log.toPlainText() and w.chat_flow.queue == ["1분에 표시해줘"]
    w.action = None
    w._refresh()
    _idle(qapp, w)
    assert w.chat_flow.queue == [] and _cards(w, ProposalCard)[-1].proposal.rows[0].start == TL0 + 60 * FPS


def test_script_without_jump_to_asks_for_one_more_click(qapp, make_window, resolve):
    from engine.resolve_link.protocol import OPS

    fake, fr = resolve
    w = _window(qapp, make_window, fake)
    fake.handlers["ping"] = lambda a: {"script_version": "1.1.0", "ops": [o for o in OPS if o != "jump_to"],
                                       "calls": {}}
    fake.known_ops = frozenset(o for o in OPS if o != "jump_to")
    _send(qapp, w, "3분 20초로 가줘")
    assert S.CHAT_OLD_SCRIPT in w.chat.log.toPlainText() and fr.jumps == []


def test_lost_answer_shows_checking_then_recheck_turns_it_into_a_receipt(qapp, make_window, resolve, monkeypatch):
    """[리졸브에 넣기] 뒤 답이 끊김: 실패로 닫지 않고 "들어갔는지 아직 몰라요" + [다시 확인].
    다시 확인(연결 확인)이 꼬리표로 맞춰 보면 영수증과 [되돌리기]가 나오고, 대화 줄은 쉬운 우리말이다."""
    import re

    from app.companion.cards import ProposalCard
    from engine.resolve_link.bridge import BridgeTimeout

    fake, fr = resolve
    w = _window(qapp, make_window, fake)
    _send(qapp, w, "3분 20초에 빨간 표시해줘")
    card = _cards(w, ProposalCard)[-1]
    real_add, real_get = fr._op_add_markers, fr._op_get_markers
    lost = {"on": True}

    def add_then_lose(a):
        real_add(a)
        raise BridgeTimeout("add_markers")

    def get(a):
        if lost["on"]:
            raise BridgeTimeout("get_markers")
        return real_get(a)

    monkeypatch.setattr(fr, "_op_add_markers", add_then_lose)
    monkeypatch.setattr(fr, "_op_get_markers", get)
    card.buttons["apply"].click()
    wait_until(qapp, lambda: card.state == "checking" and not w.busy and w.pending == 0, 20)
    assert card.title.text() == S.RECEIPT_UNKNOWN and S.RECEIPT_UNKNOWN_DETAIL in card.plain_text()
    assert list(card.buttons) == ["recheck"] and len(_ours(fr)) == 1
    assert Journal.for_timeline(w._timeline_info, w.state_root).entry(card.proposal.id)["status"] == "applying"

    lost["on"] = False
    card.buttons["recheck"].click()
    wait_until(qapp, lambda: card.state == "receipt" and w.pending == 0, 20)
    assert card.title.text().startswith("✓") and "undo" in card.buttons
    line = S.RECONCILED.format(summary=S.RECONCILED_APPLIED.format(found=1, expected=1))
    log = w.chat.log.toPlainText()
    assert line in log
    reconciled = [x for x in log.splitlines() if x.startswith(f"{S.CHAT_HELPER}: {S.RECONCILED.split('{')[0]}")]
    assert reconciled and not any(re.search(r"\b(applied|partial|undone|applying)\b", x) for x in reconciled)
    assert Journal.for_timeline(w._timeline_info, w.state_root).entry(card.proposal.id)["status"] == "applied"
    assert [e["status"] for e in w.footer.entries] == ["applied"]


def test_apply_pings_first_and_a_lost_connection_fails_fast(qapp, make_window, resolve):
    """[리졸브에 넣기]는 먼저 짧게 ping: 끊겼으면 넣기 요청을 보내지 않고 바로 알린다. 끊긴 동안 카드 단추는 꺼진다."""
    from app.companion import connection as conn
    from app.companion.cards import ProposalCard

    fake, fr = resolve
    w = _window(qapp, make_window, fake)
    _send(qapp, w, "3분 20초에 빨간 표시해줘")
    card = _cards(w, ProposalCard)[-1]
    assert card.buttons["apply"].isEnabled()
    fake.online = False
    before = len(fake.requests)
    card.buttons["apply"].click()
    wait_until(qapp, lambda: not w.busy and w.pending == 0, 20)
    assert fake.requests[before:] == ["ping"] and _ours(fr) == {}
    assert w.controller.status == conn.NOT_CONNECTED
    assert S.APPLY_FAILED.split("{")[0] in card.plain_text() and card.state == "proposal"
    assert not card.buttons["apply"].isEnabled()  # 끊긴 동안은 누를 수 없다
    fake.online = True
    w.controller.check_now("connect")
    wait_until(qapp, lambda: w.connected and w.pending == 0, 20)
    assert card.buttons["apply"].isEnabled()
    since = len(fake.requests)
    card.buttons["apply"].click()
    wait_until(qapp, lambda: card.state == "receipt" and not w.busy, 20)
    assert fake.requests[since] == "ping" and "add_markers" in fake.requests[since:]
    assert len(_ours(fr)) == 1


def test_view_on_a_card_from_another_timeline_does_not_move_the_playhead(qapp, make_window, resolve):
    """[보기]·영수증 줄: 카드를 만든 타임라인이 열려 있지 않으면 옮기지 않고 어디에 있는지 알린다."""
    from app.companion.cards import ProposalCard

    fake, fr = resolve
    w = _window(qapp, make_window, fake)
    _send(qapp, w, "3분 20초에 빨간 표시해줘")
    card = _cards(w, ProposalCard)[-1]
    fr.info.update(timeline="Timeline 2", timeline_uid="tl-2")
    card.extra["view:0"].click()
    _idle(qapp, w)
    assert fr.jumps == [] and _mutating(fake) == []
    assert S.CHAT_JUMP_OTHER_TIMELINE.format(name="Timeline 1") in w.chat.log.toPlainText()
    fr.info.update(timeline="Timeline 1", timeline_uid="tl-1")
    card.item_labels[0].clicked.emit()
    _idle(qapp, w)
    assert fr.jumps == [(TL0 + 200 * FPS, "01:03:20:00")]


def test_context_chips_after_connect_and_after_apply(qapp, make_window, resolve):
    """설계 B1.5: 연결된 뒤 (여기 표시해줘)(2초 넘게 쉰 곳 표시)(튀는 소리 표시해줘), 넣은 뒤 (방금 거 취소)(표시만 다 지워줘).
    칩은 입력 칸만 채우고 보내지 않는다."""
    from app.companion.cards import ProposalCard
    from app.companion.chat_view import ChipRow

    fake, fr = resolve
    w = _window(qapp, make_window, fake)
    rows = [x for x in w.chat.log.items if isinstance(x, ChipRow)]
    assert [[c.text() for c in r.chips] for r in rows] == [list(S.CHIPS_AFTER_CONNECT)]
    w.controller.check_now("connect")
    _idle(qapp, w)
    assert len([x for x in w.chat.log.items if isinstance(x, ChipRow)]) == 1  # 연결할 때마다가 아니라 처음 한 번
    _send(qapp, w, "3분 20초에 빨간 표시해줘")
    card = _cards(w, ProposalCard)[-1]
    card.buttons["apply"].click()
    wait_until(qapp, lambda: card.state == "receipt" and not w.busy, 20)
    row = w.chat.last_chips
    assert [c.text() for c in row.chips] == list(S.CHIPS_AFTER_APPLY)
    sent = []
    w.chat.sent.connect(sent.append)
    before = len(fake.requests)
    row.chips[0].click()
    assert w.chat.input.toPlainText() == "방금 거 취소" and sent == [] and len(fake.requests) == before


def test_reply_with_another_request_shows_the_leftover_line(qapp, make_window, resolve):
    """"도움말 3분에 표시": 도움말을 보여 주고, 섞인 "3분에 표시"는 한 것처럼 보이지 않게 주황 줄과 칩으로 알린다."""
    fake, fr = resolve
    w = _window(qapp, make_window, fake)
    before = len(fake.requests)
    _send(qapp, w, "도움말 3분에 표시")
    log = w.chat.log.toPlainText()
    assert S.CHAT_HELP in log and S.GUARD["leftover"].format(text="3분에 표시") in log
    row = w.chat.last_chips
    assert [c.text() for c in row.chips] == [S.CHIP_LEFTOVER]
    row.chips[0].click()
    assert w.chat.input.toPlainText() == "3분에 표시"
    assert _mutating(fake, before) == []


def test_timed_cancel_is_a_clear_card_and_own_markers_are_refused(qapp, make_window, resolve):
    """"3분에 표시 취소"는 방금 것 되돌리기가 아니라 그 자리의 도우미 표시 지우기 카드 (범위 지킴이를 거침).
    "내가 찍은 표시 지워줘"는 도우미 표시 모두 지우기 카드로 바꾸지 않고 못 한다고 알린다."""
    from app.companion.cards import ClearCard, QuestionCard

    fake, fr = resolve
    w = _window(qapp, make_window, fake)
    before = len(fake.requests)
    _send(qapp, w, "10초에 표시 취소")
    cards = _cards(w, ClearCard)
    assert len(cards) == 1 and not _cards(w, QuestionCard)
    assert cards[0].proposal.params.get("lo") is not None  # 말한 자리만
    _send(qapp, w, "내가 찍은 표시 지워줘")
    assert S.CHAT_REPLIES["own_markers"] in w.chat.log.toPlainText()
    assert len(_cards(w, ClearCard)) == 1
    _send(qapp, w, "다 취소해줘")
    assert S.CHAT_QUESTIONS["undo_all"] in w.chat.log.toPlainText()
    assert [c.text() for c in w.chat.last_chips.chips] == [S.CHIPS["example:undo"], S.CHIPS["example:remove_all"]]
    assert _mutating(fake, before) == [] and any(m.get("name") == "내 파란 표시" for m in fr.markers.values())


def test_clamped_value_note_reads_in_plain_korean():
    from app.companion.cards import note_lines

    lines = note_lines([("min_s_clamped", {"said": 10.0, "used": 5.0, "lo": 0.5, "hi": 5.0}),
                        ("above_clamped", {"said": 30.0, "used": 20.0, "lo": 4.0, "hi": 20.0})])
    assert lines == [S.CARD_NOTES["min_s_clamped"].format(said="10", used="5", lo="0.5", hi="5"),
                     S.CARD_NOTES["above_clamped"].format(said="30", used="20", lo="4", hi="20")]
    assert "말씀하신 10초 대신 5초" in lines[0]
