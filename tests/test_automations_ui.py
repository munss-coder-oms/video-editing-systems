"""자동화 버튼의 흐름을 창에서 (설계 B2.5, B2.3, B6.3, B7.3, B10, B11 UI). offscreen, 가짜 리졸브.

누름 → 목소리 고르기 → 카드 → [리졸브에 넣기] → 영수증 → 되돌리기. [멈추기], ⚙ 설정, 다시 누름의
[바꾸기]/[더하기], 모두 빼기(편집 화면 묻기), 확인 질문 M3 뒤 다시 세기, 좁은 창에서 카드.
리졸브에 바꾸는 요청은 [리졸브에 넣기]·되돌리기·모두 빼기 뒤에만 나가야 한다.
"""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("PySide6")

from app.companion import strings_ko as S  # noqa: E402
from engine.analysis_cache import AnalysisCache  # noqa: E402
from engine.edits.apply import LEGACY_TEST_TRACK  # noqa: E402
from engine.probe import probe  # noqa: E402
from engine.timeline.audio_map import layout_signature  # noqa: E402
from tests.conftest import requires_ffmpeg  # noqa: E402
from tests.fakes import FakeLuaBridge, FakeResolve, audio_item, obs_items, timeline_info  # noqa: E402
from tests.test_panel import make_window, qapp, resize, settle, wait_until  # noqa: E402,F401

TL0 = 108000
MUTATING = {"add_markers", "delete_markers", "remove_audio", "probe_copy", "switch_timeline"}

pytestmark = requires_ffmpeg


@pytest.fixture(scope="module")
def shared_cache(tmp_path_factory):
    return tmp_path_factory.mktemp("ui_analysis")


@pytest.fixture
def played(monkeypatch):
    from app.companion import listen

    out = []
    monkeypatch.setattr(listen, "PLAYER", lambda p: out.append(p) or True)
    return out


@pytest.fixture
def obs(tmp_path, obs_video):
    fake = FakeLuaBridge(tmp_path)
    fake.info = timeline_info(start_frame=TL0, end_frame=TL0 + 900, fps="30")
    fr = FakeResolve(fake.info, obs_items(str(obs_video), TL0, 900, clip_fps="30"))
    fr.add_user_marker(100, custom="", name="내 표시")
    fake.resolve = fr
    return fake, fr


def _window(qapp, make_window, fake, shared_cache, *, voice=None, path=None):
    w = make_window(fake)
    w.runs.cache = AnalysisCache(shared_cache)
    if voice is not None:
        w.settings.set_voice(layout_signature(probe(path)), voice, mix=0)
    wait_until(qapp, lambda: w.connected and w.pending == 0)
    return w


def _cards(w, cls):
    return [c for c in w.runs.cards if isinstance(c, cls)]


def _wait_card(qapp, w, cls, after=0, timeout=60.0):
    wait_until(qapp, lambda: len(_cards(w, cls)) > after and not w.runs.busy, timeout)
    return _cards(w, cls)[-1]


def _idle(qapp, w, timeout=60.0):
    wait_until(qapp, lambda: not w.busy and w.pending == 0, timeout)


def _ours(fr):
    return sorted(f for f, m in fr.markers.items() if str(m.get("custom")).startswith("aih:"))


def _mutating(fake, since=0):
    return [op for op in fake.requests[since:] if op in MUTATING]


def _pressed(card, key):
    card.buttons[key].click()


def test_slot_voice_card_apply_receipt_undo(qapp, make_window, obs, shared_cache, played, obs_video):
    from app.companion.cards import ProposalCard, QuestionCard
    from app.companion.voice_picker import VoicePickerCard

    fake, fr = obs
    w = _window(qapp, make_window, fake, shared_cache)
    w.automation.buttons[0].click()
    vc = _wait_card(qapp, w, VoicePickerCard)
    text = vc.plain_text()
    assert S.VOICE_TITLE in text and S.VOICE_INTRO.format(n=4) in text and S.VOICE_MIX.format(n=1) in text
    assert S.DOUBLED_VOICE in text and sorted(vc.pick_buttons) == [0, 1, 2, 3]
    assert w.automation.buttons[0].receipt.text() == S.SLOT_RECEIPT_WAITING
    assert _mutating(fake) == [] and fr.mutations == []

    # [▶ 3초 듣기]는 이 PC에서 틀기만 한다 (리졸브에 묻지 않는다)
    before = len(fake.requests)
    vc.listen_buttons[1].click()
    wait_until(qapp, lambda: played)
    wait_until(qapp, lambda: S.LISTENING.format(n=2) in w.message.text())
    assert played[0].suffix == ".wav" and played[0].is_file() and len(fake.requests) == before

    vc.pick_buttons[1].click()
    pc = _wait_card(qapp, w, ProposalCard)
    assert not vc.pick_buttons[0].isEnabled()  # 고른 카드는 닫혔다
    sig = layout_signature(probe(str(obs_video)))
    assert w.settings.voice_choice(sig)["stream"] == 1
    text = pc.plain_text()
    assert pc.title.text() == S.CARD_TITLE_FOUND
    assert S.VOICE_ROW.format(n=2, why=S.VOICE_JUST_PICKED) in text and pc.voice_btn.text() == S.BTN_CHANGE_VOICE
    assert S.DOUBLED_VOICE in text  # 소리 1(전체)과 소리 2가 함께 켜져 있다
    assert pc.buttons["apply"].isEnabled() and pc.proposal.count == 2
    assert _mutating(fake) == [] and fr.mutations == []  # 누르기 전에는 리졸브에 아무것도 넣지 않는다

    _pressed(pc, "apply")
    wait_until(qapp, lambda: pc.state == "receipt" and not w.runs.busy, 30)
    assert len(_ours(fr)) == 2 and fr.markers[100]["name"] == "내 표시"
    assert pc.title.text().startswith("✓") and "파란 표시 2개" in pc.title.text()
    assert pc.buttons["undo"].isEnabled()
    receipt = w.automation.buttons[0].receipt.text()
    assert receipt.startswith("✓") and "파란 표시 2개" in receipt
    entries = w.footer.entry_texts()
    assert len(entries) == 1 and "쉬는 곳 표시" in entries[0]
    m3 = [c for c in _cards(w, QuestionCard) if c.title.text() == S.M3_QUESTION]
    assert len(m3) == 1 and S.M3_STEP in m3[0].plain_text()

    # 카드의 [되돌리기]
    _pressed(pc, "undo")
    wait_until(qapp, lambda: pc.state == "undone" and not w.runs.busy, 30)
    assert _ours(fr) == [] and 100 in fr.markers
    assert pc.title.text().startswith("↶") and pc.buttons == {}
    assert w.automation.buttons[0].receipt.text().startswith("↶")
    report = w.report_text({})
    assert "[자동화 버튼 실행]" in report and "[목소리 고르기]" in report

    # 한가하면 아무것도 묻지 않는다
    count = len(fake.requests)
    settle(qapp, 0.3)
    assert len(fake.requests) == count


def test_stop_during_plan_keeps_resolve_untouched(qapp, make_window, obs, shared_cache, obs_video):
    fake, fr = obs
    w = _window(qapp, make_window, fake, shared_cache, voice=1, path=str(obs_video))
    gate, entered = threading.Event(), threading.Event()

    def slow_probe(path):
        entered.set()
        gate.wait(10)
        return probe(path)

    w.runs.probe_file = slow_probe
    w.automation.buttons[0].click()
    wait_until(qapp, lambda: entered.is_set() and w.runs.busy)
    row = w.automation.progress
    assert row.isVisible() and row.stop_btn.isVisible() and row.stop_btn.isEnabled()
    assert S.STAGE_NAMES[0] in row.label.text()
    # 일하는 동안: 다른 버튼·되돌리기는 꺼지고 [연결 확인]·[결과 저장]·대화·⚙는 그대로
    assert not any(b.isEnabled() for b in w.automation.buttons)
    assert w.automation.buttons[1].receipt.text() == S.SLOT_DISABLED_BUSY
    assert not w.footer.undo_btn.isEnabled()
    assert w.report_btn.isEnabled() and w.connect_btn.isEnabled() and w.chat.input.isEnabled()
    assert all(g.isEnabled() for g in w.automation.gears)
    row.stop_btn.click()
    gate.set()
    _idle(qapp, w)
    assert S.STOPPED in w.chat.log.toPlainText()
    assert w.automation.buttons[0].receipt.text() == S.SLOT_RECEIPT_STOPPED
    assert not row.isVisible() and w.automation.buttons[0].isEnabled()
    assert _mutating(fake) == [] and fr.mutations == []
    assert w.session.runs[-1]["status"] == "stopped"


def test_settings_then_rerun_offers_replace_or_add(qapp, make_window, obs, shared_cache, obs_video):
    from app.companion.cards import ProposalCard, QuestionCard

    fake, fr = obs
    w = _window(qapp, make_window, fake, shared_cache, voice=1, path=str(obs_video))
    w.automation.gears[0].click()
    assert w.stack.currentWidget() is w.settings_page
    page = w.settings_page
    assert page.kind_box.currentData() == "mark_pauses"
    page.set_value("min_s", 2.0)
    assert page.values()["min_s"] == 2.0
    page.save_btn.click()
    assert w.stack.currentWidget() is w.panel
    assert "2초 넘게" in w.automation.buttons[0].summary.text()
    assert w.settings.slot(1)["params"]["min_s"] == 2.0
    assert S.SETTINGS_SAVED.format(name="쉬는 곳 표시") in w.message.text()

    w.automation.buttons[0].click()
    first = _wait_card(qapp, w, ProposalCard)
    assert first.proposal.params["min_s"] == 2.0
    assert S.VOICE_ROW.format(n=2, why=S.VOICE_REMEMBERED) in first.plain_text()
    _pressed(first, "apply")
    wait_until(qapp, lambda: first.state == "receipt" and not w.runs.busy, 30)
    assert len(_ours(fr)) == 2

    # 다시 누르면: 바꿀까요? → [바꾸기]
    asked = len(_cards(w, QuestionCard))
    w.automation.buttons[0].click()
    wait_until(qapp, lambda: any(c.title.text() == S.RERUN_QUESTION.format(color_word="파란", n=2)
                                 for c in _cards(w, QuestionCard)[asked:]) and not w.runs.busy, 60)
    q = _cards(w, QuestionCard)[-1]
    assert set(q.buttons) == {"replace", "add"}
    q.buttons["replace"].click()
    second = _cards(w, ProposalCard)[-1]
    assert second is not first and S.RESOLVE_REPLACE.format(color_word="파란", n=2) in second.plain_text()
    _pressed(second, "apply")
    wait_until(qapp, lambda: second.state == "receipt" and not w.runs.busy, 30)
    assert len(_ours(fr)) == 2 and all(str(fr.markers[f]["custom"]).startswith(f"aih:{second.proposal.id}:")
                                       for f in _ours(fr))
    assert first.state == "undone" and S.CARD_REPLACED in first.plain_text() and first.buttons == {}

    # 한 번 더: [더하기]면 이전 것은 그대로 두고 더 넣는다 (자리가 차 있으면 한 프레임 뒤로)
    asked = len(_cards(w, QuestionCard))
    w.automation.buttons[0].click()
    wait_until(qapp, lambda: len(_cards(w, QuestionCard)) > asked and not w.runs.busy, 60)
    _cards(w, QuestionCard)[-1].buttons["add"].click()
    third = _cards(w, ProposalCard)[-1]
    _pressed(third, "apply")
    wait_until(qapp, lambda: third.state == "receipt" and not w.runs.busy, 30)
    assert len(_ours(fr)) == 4 and second.state == "receipt"
    assert len(w.footer.entry_texts()) == 3


def test_voice_row_change_opens_the_picker(qapp, make_window, obs, shared_cache, obs_video):
    from app.companion.cards import ProposalCard
    from app.companion.voice_picker import VoicePickerCard

    fake, fr = obs
    w = _window(qapp, make_window, fake, shared_cache, voice=1, path=str(obs_video))
    w.automation.buttons[0].click()
    pc = _wait_card(qapp, w, ProposalCard)
    assert _cards(w, VoicePickerCard) == []
    pc.voice_btn.click()
    vc = _wait_card(qapp, w, VoicePickerCard)
    assert vc.question.reason == "change" and vc.question.previous == 1
    assert S.VOICE_CHANGE in vc.plain_text() and S.VOICE_PREVIOUS_MARK in vc.plain_text()
    vc.pick_buttons[2].click()
    new = _wait_card(qapp, w, ProposalCard, after=1)
    assert new.proposal.voice.stream == 2 and pc.state == "closed"  # 이전 카드는 "바뀜"
    assert S.VOICE_ROW.format(n=3, why=S.VOICE_JUST_PICKED) in new.plain_text()
    assert fr.mutations == []


def test_slot_two_marks_spikes_and_asks_m2(qapp, make_window, obs, shared_cache, obs_video):
    from app.companion.cards import ProposalCard, QuestionCard

    fake, fr = obs
    w = _window(qapp, make_window, fake, shared_cache, voice=1, path=str(obs_video))
    w.automation.buttons[1].click()
    pc = _wait_card(qapp, w, ProposalCard)
    assert pc.proposal.kind == "mark_spikes" and pc.proposal.count >= 1
    _pressed(pc, "apply")
    wait_until(qapp, lambda: pc.state == "receipt" and not w.runs.busy, 30)
    assert all(fr.markers[f]["color"] == "Red" for f in _ours(fr))
    titles = [c.title.text() for c in _cards(w, QuestionCard)]
    assert S.M3_QUESTION in titles and S.M2_QUESTION in titles


def test_m3_answer_is_saved_and_recounts(qapp, make_window, obs, shared_cache, obs_video):
    from app.companion.cards import ProposalCard, QuestionCard

    fake, fr = obs
    w = _window(qapp, make_window, fake, shared_cache, voice=1, path=str(obs_video))
    w.automation.buttons[0].click()
    pc = _wait_card(qapp, w, ProposalCard)
    _pressed(pc, "apply")
    wait_until(qapp, lambda: pc.state == "receipt" and not w.runs.busy, 30)
    m3 = next(c for c in _cards(w, QuestionCard) if c.title.text() == S.M3_QUESTION)
    before = len(fake.requests)
    m3.buttons["other"].click()
    wait_until(qapp, lambda: S.MANUAL_COUNTS.format(found=2, expected=2) in w.chat.log.toPlainText())
    assert fake.requests[before:] == ["get_markers"]  # 다시 세기 (읽기 한 번)
    rec = w.session.manual["M3"]
    assert rec["answer"] == "other" and rec["found"] == 2 and rec["expected"] == 2
    assert w.caps.manual["M3"]["answer"]["answer"] == "other"
    assert w.footer.warn_ctrl_z is True
    assert any(S.UNDO_HINT_WARN in a.text() for a in w.footer.menu.actions())
    assert "[확인 질문]" in w.report_text({})
    # 한 번 답하면 다시 넣어도 저절로 묻지 않는다. 연결 점검의 [확인 질문 다시 보기]로만.
    n = len(_cards(w, QuestionCard))
    w.check_page.manual_btn.click()
    assert len(_cards(w, QuestionCard)) == n + 2


def test_footer_undo_list_asks_first(qapp, make_window, obs, shared_cache, obs_video):
    from app.companion.cards import ProposalCard

    fake, fr = obs
    w = _window(qapp, make_window, fake, shared_cache, voice=1, path=str(obs_video))
    w.automation.buttons[0].click()
    pc = _wait_card(qapp, w, ProposalCard)
    _pressed(pc, "apply")
    wait_until(qapp, lambda: pc.state == "receipt" and not w.runs.busy, 30)
    asked = []
    w.choose = lambda title, text, options: asked.append((title, text, options)) or None
    act = w.footer.action_for(pc.proposal.id)
    assert act is not None and act.isEnabled()
    act.trigger()
    settle(qapp, 0.1)
    assert asked[0][2] == [S.BTN_REMOVE, S.BTN_CANCEL] and len(_ours(fr)) == 2  # 취소하면 그대로
    w.choose = lambda title, text, options: 0
    w.runs.by_pid.clear()  # 앱을 다시 켠 뒤처럼: 카드 없이 일지에만 있는 줄
    w.footer.action_for(pc.proposal.id).trigger()
    _idle(qapp, w)
    assert _ours(fr) == [] and 100 in fr.markers
    assert S.UNDO_DONE_LINE.format(request="쉬는 곳 표시", n=2) in w.chat.log.toPlainText()
    assert not w.footer.action_for(pc.proposal.id).isEnabled()  # 뺀 줄은 다시 누를 수 없다


@pytest.mark.parametrize("answer", [0, 1, None])
def test_remove_all_asks_about_the_edit_page(qapp, make_window, tmp_path, shared_cache, answer):
    fake = FakeLuaBridge(tmp_path)
    fake.info = timeline_info(page="color")
    fr = FakeResolve(fake.info, [audio_item("a", 1, 216000, 600, "C:/a.wav")])
    fr.add_user_marker(100, custom="", name="내 표시")
    fr.add_user_marker(200, custom="aih:Pold:1", color="Blue", name="쉼")
    fr.add_user_marker(300, custom="aih_test", color="Green", name="시험")
    fr.add_user_marker(400, custom="aih_test", color="Green", name="시험")
    fake.info["tracks"]["audio"].append({"index": 5, "name": LEGACY_TEST_TRACK, "enabled": True, "locked": False,
                                         "subtype": "stereo", "count": 1})
    fr.items.append(audio_item("t", 5, 216000, 300, "C:/state/test_tone.wav"))
    fake.resolve = fr
    w = _window(qapp, make_window, fake, shared_cache)
    asked = []
    w.choose = lambda title, text, options: asked.append((title, text, options)) or answer
    act = w.footer.remove_all_action()
    assert act.isEnabled()
    before = len(fake.requests)
    act.trigger()
    _idle(qapp, w)
    title, text, options = asked[0]
    assert title == S.EDIT_PAGE_QUESTION and options == [S.BTN_SWITCH_REMOVE, S.BTN_MARKERS_ONLY, S.BTN_CANCEL]
    assert S.REMOVE_ALL_CONFIRM.format(n=4) in text
    tracks = [t["name"] for t in fake.info["tracks"]["audio"]]
    if answer is None:
        assert _mutating(fake, before) == [] and len(fr.markers) == 4 and LEGACY_TEST_TRACK in tracks
        return
    assert sorted(fr.markers) == [100]  # 사용자 표시만 남는다
    log = w.chat.log.toPlainText()
    assert S.REMOVE_ALL_DONE.format(markers=1, legacy=2) in log
    if answer == 0:
        assert LEGACY_TEST_TRACK not in tracks and fr.opened_pages == ["edit", "color"]
        assert S.REMOVE_ALL_TRACK_DONE in log
    else:
        assert LEGACY_TEST_TRACK in tracks and "remove_audio" not in fake.requests
        assert S.REMOVE_ALL_TRACK_KEPT in log


def test_remove_all_on_the_edit_page_asks_plainly(qapp, make_window, tmp_path, shared_cache):
    fake = FakeLuaBridge(tmp_path)
    fr = FakeResolve(fake.info, [])
    fake.resolve = fr
    w = _window(qapp, make_window, fake, shared_cache)
    w.choose = lambda *a: pytest.fail("찾은 것이 없으면 묻지 않는다")
    w.footer.remove_all_action().trigger()
    _idle(qapp, w)
    assert S.REMOVE_ALL_NOTHING in w.chat.log.toPlainText()
    fr.add_user_marker(200, custom="aih:Pold:1")
    asked = []
    w.choose = lambda title, text, options: asked.append(options) or 0
    w.footer.remove_all_action().trigger()
    _idle(qapp, w)
    assert asked == [[S.BTN_REMOVE, S.BTN_CANCEL]] and fr.markers == {}


def test_slot_order_and_restore_previous(qapp, make_window, tmp_path, shared_cache):
    fake = FakeLuaBridge(tmp_path)
    w = _window(qapp, make_window, fake, shared_cache)
    three = w.automation.buttons[2]
    assert not three.isEnabled() and three.receipt.text() == S.SLOT_DISABLED_NOT_READY
    assert three.toolTip() == S.TIP_SLOT_NOT_READY.format(name="소리 고르게")
    w.automation.gears[2].click()
    page = w.settings_page
    assert page.kind_box.currentData() == "balance_voice" and page.not_ready.isVisible()
    page.kind_box.setCurrentIndex(page.kind_box.findData("mark_spikes"))
    assert "above_lu" in page.fields and not page.not_ready.isVisible()
    page.name_edit.setText("큰 소리")
    page.save_btn.click()
    three = w.automation.buttons[2]
    assert three.name == "큰 소리" and three.isEnabled() and "dB 넘게" in three.summary.text()
    # 순서 바꾸기: 3번을 맨 위로 두 번
    w.automation.gears[2].click()
    page.up_btn.click()
    page.up_btn.click()
    assert [b.name for b in w.automation.buttons] == ["큰 소리", "쉬는 곳 표시", "튀는 소리 표시"]
    assert not page.up_btn.isEnabled() and page.down_btn.isEnabled()
    assert [s["slot"] for s in w.settings.slots] == [3, 1, 2]
    # [이전 설정으로 되돌리기]: 준비 중인 "소리 고르게"로
    assert page.restore_btn.isEnabled()
    page.restore_btn.click()
    btn = w.automation.button(3)
    assert btn.name == "소리 고르게" and not btn.isEnabled() and btn.receipt.text() == S.SLOT_DISABLED_NOT_READY
    assert w.stack.currentWidget() is w.panel
    assert S.SETTINGS_RESTORED.format(name="소리 고르게") in w.message.text()
    # 설정 바꾸기는 리졸브에 묻지 않는다
    assert _mutating(fake) == []


@pytest.mark.parametrize("has_in_out", [True, False])
def test_settings_scope_in_out_needs_the_check(qapp, make_window, tmp_path, shared_cache, has_in_out):
    """In~Out만은 읽기 점검에서 GetMarkInOut이 있다고 확인된 뒤에만 고를 수 있다."""
    fake = FakeLuaBridge(tmp_path)
    if not has_in_out:
        fake.handlers["probe_read"] = lambda a: {"exists": {}, "existence_reliable": True, "calls": {}}
    w = _window(qapp, make_window, fake, shared_cache)
    w.automation.gears[0].click()
    box = w.settings_page.fields["scope"]
    idx = box.findData("in_out")
    assert idx >= 0 and box.model().item(idx).isEnabled() is has_in_out
    assert (S.IN_OUT_LOCKED in box.itemText(idx)) is not has_in_out
    if has_in_out:
        w.settings_page.set_value("scope", "in_out")
        w.settings_page.save_btn.click()
        assert w.settings.slot(1)["params"]["scope"] == "in_out"
        assert w.automation.buttons[0].summary.text().endswith(S.SLOT_SCOPE_IN_OUT)


def test_card_fits_the_compact_window(qapp, make_window, obs, shared_cache, obs_video):
    from app.companion.cards import ProposalCard

    fake, fr = obs
    w = _window(qapp, make_window, fake, shared_cache, voice=1, path=str(obs_video))
    resize(qapp, w, 380, 640)
    w.automation.buttons[0].click()
    pc = _wait_card(qapp, w, ProposalCard)
    settle(qapp, 0.1)
    assert w.layout_name == "tight" and w.smoke_check() == []
    assert pc.buttons["apply"].isVisible() and pc.width() <= w.width()
    assert w.chat.log.isVisible()
    resize(qapp, w, 420, 900)
    assert w.smoke_check() == []


def test_apply_waits_while_another_job_runs(qapp, make_window, obs, shared_cache, obs_video):
    from app.companion.cards import ProposalCard

    fake, fr = obs
    w = _window(qapp, make_window, fake, shared_cache, voice=1, path=str(obs_video))
    w.automation.buttons[0].click()
    pc = _wait_card(qapp, w, ProposalCard)
    assert pc.buttons["apply"].isEnabled()
    gate, entered = threading.Event(), threading.Event()

    def slow_probe(path):
        entered.set()
        gate.wait(10)
        return probe(path)

    w.runs.probe_file = slow_probe
    w.automation.buttons[1].click()
    wait_until(qapp, lambda: entered.is_set())
    assert not pc.buttons["apply"].isEnabled() and pc.buttons["cancel"].isEnabled()
    assert not pc.voice_btn.isEnabled()  # 목소리 [바꾸기]도 다시 계산이라 일이 끝난 뒤에
    pc.buttons["apply"].click()  # 꺼져 있어서 아무 일도 없다
    gate.set()
    _idle(qapp, w)
    assert fr.mutations == [] and pc.state == "proposal" and pc.buttons["apply"].isEnabled()
    assert pc.voice_btn.isEnabled()


def test_unmapped_tracks_say_why_without_a_pick_again_button(qapp, make_window, obs, shared_cache, obs_video):
    """트랙이 원본의 몇 번째 소리인지 모름: 목소리 고르기를 되풀이하지 않고 까닭만 알린다."""
    from app.companion.cards import QuestionCard
    from app.companion.voice_picker import VoicePickerCard

    fake, fr = obs
    fr.items = [it for it in fr.items if it["track"] != 1]
    w = _window(qapp, make_window, fake, shared_cache)
    w.automation.buttons[0].click()
    wait_until(qapp, lambda: not w.runs.busy and w.pending == 0, 60)
    assert S.PLAN_REFUSED["unmapped_tracks"].format(n=3) in w.chat.log.toPlainText()
    assert _cards(w, VoicePickerCard) == []
    assert not any("again" in c.buttons for c in _cards(w, QuestionCard))
    assert _mutating(fake) == []


def test_voice_pick_waits_while_another_job_runs(qapp, make_window, obs, shared_cache, obs_video):
    """다른 일이 도는 동안 [이걸로]는 꺼져 있고, 눌려도 말없이 사라지지 않고 까닭을 알린다."""
    from app.companion.voice_picker import VoicePickerCard

    fake, fr = obs
    w = _window(qapp, make_window, fake, shared_cache)
    w.automation.buttons[0].click()
    vc = _wait_card(qapp, w, VoicePickerCard)
    assert all(b.isEnabled() for b in vc.pick_buttons.values())
    gate, entered = threading.Event(), threading.Event()

    def slow_probe(path):
        entered.set()
        gate.wait(10)
        return probe(path)

    w.runs.probe_file = slow_probe
    w.automation.buttons[1].click()
    wait_until(qapp, lambda: entered.is_set())
    assert not any(b.isEnabled() for b in vc.pick_buttons.values())
    assert all(b.isEnabled() for b in vc.listen_buttons.values())  # 3초 듣기는 이 PC에서만이라 그대로
    vc.picked.emit(1)  # 눌렸다 해도
    assert w.message.text() == S.SLOT_DISABLED_BUSY and not vc.locked
    gate.set()
    _idle(qapp, w)
    assert all(b.isEnabled() for b in vc.pick_buttons.values())
