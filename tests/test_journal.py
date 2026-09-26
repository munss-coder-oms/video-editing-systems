"""도우미 일지 (engine/edits/journal.py): 앞서 쓰기, 연결할 때 맞춰 보기, 다른 타임라인에서 되돌리기 거절."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from engine.edits.journal import Journal, all_journals, key_for, timeline_key


def _info(uid="tl-1", name="Timeline 1", project_uid="proj-1"):
    return SimpleNamespace(project_uid=project_uid, timeline_uid=uid, project="시험", timeline=name,
                           start_frame=216000, fps="60")


def test_timeline_key_uses_uids_or_names():
    a = timeline_key("p", "t")
    assert len(a) == 10 and a == timeline_key("p", "t", "다른 이름")
    b = timeline_key(None, None, "프로젝트", "Timeline 1", 216000, "60")
    assert b != a and b == timeline_key(None, "t", "프로젝트", "Timeline 1", 216000, "60")
    assert key_for(_info()) == timeline_key("proj-1", "tl-1")


def test_begin_is_written_before_apply_and_finish(tmp_path):
    j = Journal.for_timeline(_info(), tmp_path)
    j.begin("P1", origin="button:1", request="쉬는 곳 표시", commands=[{"op": "mark_pauses"}],
            expected_markers=[{"frame": 10, "custom": "aih:P1:1"}, {"frame": 20, "custom": "aih:P1:2"}])
    on_disk = json.loads(j.path.read_text(encoding="utf-8"))
    assert on_disk["entries"][0]["status"] == "applying"
    assert on_disk["timeline"]["timeline_uid"] == "tl-1"
    e = j.finish("P1", created_markers=[{"frame": 10, "custom": "aih:P1:1"}], receipt={"placed": 1})
    assert e["status"] == "partial"
    with pytest.raises(ValueError):
        j.begin("P2", origin="chat", request="x", commands=[], expected_markers=[{"custom": "aih:P9:1"}])
    with pytest.raises(ValueError):
        j.begin("P1", origin="chat", request="x", commands=[])


@pytest.mark.parametrize("present, status", [
    (["aih:P7:1", "aih:P7:2", "aih:P7:3"], "applied"),
    (["aih:P7:2"], "partial"),
    ([], "undone"),
])
def test_startup_reconciliation(tmp_path, present, status):
    j = Journal.for_timeline(_info(), tmp_path)
    j.begin("P7", origin="button:1", request="쉬는 곳 표시", commands=[],
            expected_markers=[{"frame": i, "custom": f"aih:P7:{i}"} for i in (1, 2, 3)])
    # 앱이 꺼졌다가 다시 켜짐: 파일에서 다시 읽는다
    again = Journal.for_timeline(_info(), tmp_path)
    assert [e["proposal_id"] for e in again.pending()] == ["P7"]
    markers = [{"frame": 5, "custom": c} for c in present] + [{"frame": 9, "custom": "user"}]
    [r] = again.reconcile(markers)
    assert (r.proposal_id, r.status, r.expected, r.found) == ("P7", status, 3, len(present))
    if status == "undone":
        assert r.message == "넣다가 멈춘 것은 들어가지 않았어요"
    assert Journal.for_timeline(_info(), tmp_path).entry("P7")["status"] == status
    assert again.reconcile(markers) == []  # 한 번 맞춘 것은 다시 보지 않는다


def test_undo_refused_on_another_timeline(tmp_path):
    j = Journal.for_timeline(_info(), tmp_path)
    assert j.undo_blocker(_info()) is None
    assert j.undo_blocker(_info(uid="tl-2", name="Timeline 2")) == "이 되돌리기는 Timeline 1에서 할 수 있어요"
    # 번호가 없는 판: 이름·시작·속도로 만든 열쇠로 비교
    no_uid = _info(uid=None, project_uid=None)
    j2 = Journal.for_timeline(no_uid, tmp_path)
    assert j2.undo_blocker(no_uid) is None
    assert j2.undo_blocker(_info(uid=None, project_uid=None, name="다른 것")) is not None


def test_corrupt_journal_is_moved_aside(tmp_path):
    j = Journal.for_timeline(_info(), tmp_path)
    j.path.parent.mkdir(parents=True, exist_ok=True)
    j.path.write_text("{broken", encoding="utf-8")
    again = Journal.for_timeline(_info(), tmp_path)
    assert again.entries == [] and again.moved_bad is not None


def test_summaries_and_all_journals(tmp_path):
    j = Journal.for_timeline(_info(), tmp_path)
    for i in range(25):
        j.begin(f"P{i}", origin="button:1", request="표시", commands=[])
        j.finish(f"P{i}", receipt={"calls": {"Timeline.AddMarker": "ok"}})
    s = j.summaries()
    assert len(s) == 20 and s[-1]["proposal_id"] == "P24" and s[-1]["calls"] == {"Timeline.AddMarker": "ok"}
    assert [x.key for x in all_journals(tmp_path)] == [j.key]
