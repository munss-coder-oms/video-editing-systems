"""연결 시험의 단계들. 화면(Qt)을 모르고 브리지만 부른다.

창은 이 함수들을 작업 스레드에서 돌린다 (리졸브의 답을 기다리는 동안 창이 멈추지 않게).
단계 함수는 (bridge, out)을 받아 out 사전에 리졸브의 원래 답을 차례로 채운다.
중간에 실패해도 거기까지 받은 답은 결과 파일에 남는다 (run_step → StepFailed.partial).
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from engine.resolve_link.bridge import BridgeCancelled, BridgeError, BridgeTimeout
from engine.resolve_link.testfiles import TEST_TONE_NAME, make_test_tone
from engine.resolve_link.timecode import is_drop_frame, parse_fps, tc_offset, tc_to_frames

TEST_NAME = "AI 도우미 시험"  # 표시 이름이자 오디오 트랙 이름
TEST_CUSTOM = "aih_test"  # 표시의 custom data. 지울 때 이것으로 찾는다
TEST_NOTE = "AI 도우미 연결 시험 표시입니다. [시험 흔적 지우기]로 지울 수 있습니다."
TONE_SECONDS = 3.0  # 타임라인에 올리는 길이
# 시험용 소리 파일의 길이. 올리는 길이보다 길게 만들어, 29.97처럼 딱 나누어떨어지지 않는 속도에서도
# 요청한 마지막 프레임(endFrame)이 파일 끝을 넘지 않게 한다.
TONE_FILE_SECONDS = 3.5
DEFAULT_FPS = 24.0  # 프레임 속도를 못 읽었을 때 가정하는 값

PING_TIMEOUT = 5.0  # 버튼을 누를 때 먼저 하는 연결 확인
AUTO_PING_TIMEOUT = 1.5  # 연결 전 2초마다 하는 확인

# ping에는 답했는데 그다음 작업의 답이 늦을 때 (Scripts 메뉴를 다시 누르라고 하면 안 된다)
SLOW_TEXT = (
    "리졸브와 연결은 되어 있지만 이 작업의 답이 늦습니다. 리졸브가 아직 일하는 중이거나 "
    "리졸브에 창(대화 상자)이 열려 있을 수 있습니다. 잠시 뒤 다시 눌러 주세요."
)

Out = Dict[str, Any]
Step = Callable[[Any, Out], None]


class StepProblem(Exception):
    """리졸브는 답했지만 이 단계를 할 수 없는 상태 (프로젝트·타임라인 없음 등). 한국어 안내문."""


class StepFailed(Exception):
    """단계 실패. cause는 원래 오류, partial은 실패하기 전까지 받은 답."""

    def __init__(self, cause: BaseException, partial: Out) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.partial = partial


def run_step(step: Step, bridge) -> Out:
    out: Out = {}
    try:
        step(bridge, out)
    except Exception as exc:  # 어떤 오류든 창에 알리고 결과 파일에 남긴다
        raise StepFailed(exc, out) from exc
    return out


# ── 오류를 쉬운 말로 ───────────────────────────────────────────────────

_ERROR_TEXT = {
    "no_project": "리졸브에서 프로젝트를 열어 주세요.",
    "no_timeline": "리졸브에서 타임라인을 열어 주세요 (편집 화면 아래에 타임라인이 보여야 합니다).",
    "no_resolve": "스크립트가 리졸브를 찾지 못했습니다. 리졸브의 Workspace → Scripts 메뉴에서 실행했는지 확인해 주세요.",
    "no_project_manager": "스크립트가 리졸브의 프로젝트 관리자를 열지 못했습니다.",
    "no_media_pool": "리졸브의 미디어 풀을 열지 못했습니다.",
    "no_root_folder": "리졸브 미디어 풀의 맨 위 폴더를 찾지 못했습니다.",
    "add_bin_failed": "미디어 풀에 'AI 도우미' 폴더를 만들지 못했습니다.",
    "import_failed": "리졸브가 시험용 소리 파일을 가져오지 못했습니다.",
    "add_track_failed": "리졸브가 오디오 트랙을 추가하지 못했습니다.",
    "track_count_failed": "리졸브가 오디오 트랙 수를 알려 주지 않았습니다.",
    "too_large": "리졸브의 답이 너무 커서 받지 못했습니다.",
}


def explain(exc: BaseException, answered: bool = False) -> str:
    """오류 → 화면과 결과 파일에 쓸 한국어 한두 줄.

    answered: 같은 단계에서 리졸브가 ping에는 답했다 (연결은 됨). 이때 답이 없으면 끊긴 것이 아니라 늦은 것이다.
    """
    if answered and is_disconnect(exc):
        return SLOW_TEXT
    if isinstance(exc, (BridgeTimeout, StepProblem)):
        return str(exc)  # BridgeCancelled 포함, 이미 한국어
    if isinstance(exc, BridgeError):
        if exc.error in _ERROR_TEXT:
            return _ERROR_TEXT[exc.error]
        if exc.error.startswith("bad_path"):
            return f"시험용 소리 파일 위치를 스크립트가 받아들이지 않았습니다 ({exc.error})."
        return str(exc)
    return f"예상하지 못한 오류: {type(exc).__name__}: {exc}"


def error_info(exc: BaseException, answered: bool = False) -> Dict[str, Any]:
    """결과 파일에 남길 오류 정보. Lua가 준 실패 답(calls: 함수마다 ok/err:내용, detail ...)도 함께."""
    info: Dict[str, Any] = {"type": type(exc).__name__, "message": explain(exc, answered)}
    for key in ("error", "func", "op", "req_id", "prefs_changed"):
        value = getattr(exc, key, None)
        if value:
            info[key] = value
    payload = getattr(exc, "payload", None)
    if isinstance(payload, dict):
        for key in ("calls", "detail", "size", "env"):
            if key in payload:
                info[key] = payload[key]
    if not isinstance(exc, (BridgeTimeout, BridgeError, StepProblem)):
        info["detail"] = repr(exc)
    return info


def is_disconnect(exc: BaseException) -> bool:
    """리졸브가 답하지 않은 경우 (연결이 끊김). 앱을 닫는 중인 것은 뺀다."""
    return isinstance(exc, BridgeTimeout) and not isinstance(exc, BridgeCancelled)


# ── 타임라인 값 계산 ───────────────────────────────────────────────────

def timeline_fps(state: Out) -> Tuple[float, bool]:
    """(프레임 속도, 추측했는지). 리졸브가 준 글자("29.97" 등)를 숫자로 바꾼다."""
    try:
        value, _ = parse_fps(state.get("fps"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_FPS, True
    return value, False


def _int(value) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def timeline_length(state: Out) -> Optional[int]:
    start, end = _int(state.get("start_frame")), _int(state.get("end_frame"))
    if start is None or end is None or end < start:
        return None
    return end - start


def timeline_start_frame(state: Out) -> int:
    """타임라인 첫 프레임의 절대 번호 (소리를 놓을 recordFrame의 기준)."""
    start = _int(state.get("start_frame"))
    if start is not None:
        return start
    try:
        return tc_to_frames(state.get("start_tc") or "", state.get("fps"), state.get("drop_frame"))
    except (TypeError, ValueError):
        raise StepProblem("리졸브가 타임라인 시작 위치를 알려 주지 않았습니다.") from None


def drop_frame_used(state: Out) -> bool:
    """재생 위치를 셀 때 쓴 드롭 프레임 여부 (두 타임코드를 같은 규칙으로 센다)."""
    return is_drop_frame(state.get("fps"), state.get("current_tc"), state.get("start_tc"),
                         flag=state.get("drop_frame"))


def playhead_offset(state: Out) -> Tuple[int, str]:
    """재생 위치가 타임라인 시작에서 몇 프레임인지.

    돌려주는 값: (프레임 수, "playhead"). 재생 위치를 못 읽으면 (시작 + 1초, "start_plus_1s").
    """
    fps, _ = timeline_fps(state)
    length = timeline_length(state)
    current, start_tc, fps_text = state.get("current_tc"), state.get("start_tc"), state.get("fps")
    if current and start_tc and fps_text:
        try:
            offset = tc_offset(current, start_tc, fps_text, drop_frame_used(state))
        except (TypeError, ValueError):
            offset = -1
        if offset >= 0 and (length is None or offset <= length):
            return offset, "playhead"
    one_second = max(1, int(round(fps)))
    if length is not None:
        one_second = min(one_second, length)
    return one_second, "start_plus_1s"


def need_timeline(state: Out) -> None:
    if state.get("project") is None:
        raise StepProblem("리졸브에서 프로젝트를 열어 주세요.")
    if state.get("timeline") is None:
        raise StepProblem("리졸브에서 타임라인을 열어 주세요 (편집 화면 아래에 타임라인이 보여야 합니다).")


# ── 단계 ─────────────────────────────────────────────────────────────

def connect_step(bridge, out: Out, ping_timeout: float = PING_TIMEOUT) -> None:
    """① 연결 확인: ping + state. state가 실패하거나 늦어도 ping에 답했으면 연결은 된 것이다."""
    out["ping"] = bridge.ping(timeout=ping_timeout)
    try:
        out["state"] = bridge.state()
    except (BridgeError, BridgeTimeout) as exc:
        out["state_error"] = error_info(exc, answered=True)


auto_connect_step = functools.partial(connect_step, ping_timeout=AUTO_PING_TIMEOUT)


def _ping_and_state(bridge, out: Out) -> Out:
    out["ping"] = bridge.ping(timeout=PING_TIMEOUT)
    state = out["state"] = bridge.state()
    need_timeline(state)
    return state


def _read_markers(bridge, out: Out, key: str) -> None:
    """표시 목록을 다시 읽어 out[key]에 둔다. 못 읽어도 단계는 계속한다 (이유는 key_error에)."""
    try:
        out[key] = bridge.get_markers()
    except (BridgeError, BridgeTimeout) as exc:
        out[key + "_error"] = error_info(exc, answered=True)


def marker_step(bridge, out: Out) -> None:
    """② 재생 위치에 노란 표시를 하나 찍는다."""
    state = _ping_and_state(bridge, out)
    offset, how = playhead_offset(state)
    out["offset"], out["offset_how"] = offset, how
    out["drop_frame_used"] = drop_frame_used(state)
    out["marker"] = bridge.add_marker(
        offset, color="Yellow", name=TEST_NAME, note=TEST_NOTE, custom=TEST_CUSTOM, duration=1
    )
    # 실제로 어디에 어떻게 들어갔는지 다시 읽는다 (이름·메모·custom data가 그대로인지, 프레임 기준이 맞는지)
    _read_markers(bridge, out, "markers_after")


def audio_step(bridge, out: Out) -> None:
    """③ 시험용 소리(1kHz 3초)를 새 오디오 트랙의 재생 위치에 놓는다."""
    state = _ping_and_state(bridge, out)
    offset, how = playhead_offset(state)
    start = timeline_start_frame(state)
    fps, guessed = timeline_fps(state)
    # 리졸브가 가져올 수 있는 곳은 우체통의 files 폴더뿐이다 (스크립트가 다른 경로는 거부한다).
    tone = make_test_tone(TONE_FILE_SECONDS, path=Path(bridge.mailbox) / "files" / TEST_TONE_NAME)
    # 버림: 29.97에서 3초는 89.91프레임이다. 반올림(90)하면 마지막 프레임이 3초짜리 소리 밖으로 나간다.
    frames = max(1, int(TONE_SECONDS * fps + 1e-6))
    out.update(offset=offset, offset_how=how, record_frame=start + offset, frames=frames,
               fps_guessed=guessed, drop_frame_used=drop_frame_used(state), tone=str(tone))
    out["audio"] = bridge.place_audio(tone, TEST_NAME, start + offset, frames)
    # 넣은 뒤의 모습: 리졸브가 파일 경로를 어떻게 적는지(짧은 이름 → 긴 이름?) 결과 파일에서 보려고
    try:
        out["state_after"] = bridge.state()
    except BridgeError as exc:
        out["state_after_error"] = error_info(exc)


def cleanup_step(bridge, out: Out) -> None:
    """시험 흔적 지우기: 시험 표시와 시험 오디오 트랙만 지운다 (다른 것은 건드리지 않음).

    지우기 앞뒤로 표시 목록과 타임라인을 다시 읽어, 정말 없어졌는지 결과에 남긴다.
    """
    out["ping"] = bridge.ping(timeout=PING_TIMEOUT)
    _read_markers(bridge, out, "markers_before")
    for key, call in (
        ("delete_markers", lambda: bridge.delete_markers(TEST_CUSTOM)),
        ("remove_audio", lambda: bridge.remove_audio(TEST_NAME)),
    ):
        try:
            out[key] = call()
        except BridgeError as exc:
            out[key + "_error"] = error_info(exc)
    _read_markers(bridge, out, "markers_after")
    try:
        out["state"] = bridge.state()
    except BridgeError as exc:
        out["state_error"] = error_info(exc)


# ── 결과를 한 줄로 ────────────────────────────────────────────────────

def _quote(value) -> str:
    return f'"{value}"' if isinstance(value, str) else "없음"


def describe_state(ping: Optional[Out], state: Optional[Out]) -> str:
    parts = []
    if ping:
        product = ping.get("product") or "리졸브"
        version = ping.get("resolve_version") or "판 모름"
        parts.append(f"{product} {version}")
    if state:
        if state.get("project") is None:
            parts.append("열린 프로젝트 없음")
        else:
            parts.append(f"프로젝트 {_quote(state.get('project'))}")
            if state.get("timeline") is None:
                parts.append("열린 타임라인 없음")
            else:
                parts.append(f"타임라인 {_quote(state.get('timeline'))}")
    return " / ".join(parts)


def _test_markers(listing: Optional[Out]) -> Optional[List[Out]]:
    """get_markers 답에서 시험 표시(custom data가 aih_test)만. 목록을 못 읽었으면 None."""
    if not isinstance(listing, dict) or not isinstance(listing.get("markers"), list):
        return None
    return [m for m in listing["markers"] if isinstance(m, dict) and m.get("custom") == TEST_CUSTOM]


def marker_problems(out: Out) -> Optional[List[str]]:
    """넣은 시험 표시를 다시 읽어 본 결과. 다시 못 읽었으면 None, 그대로 들어갔으면 빈 목록."""
    listing = out.get("markers_after")
    ours = _test_markers(listing)
    if ours is None:
        return None
    frame = (out.get("marker") or {}).get("frame")
    here = [m for m in ours if m.get("frame") == frame]
    if not here:
        same_name = [m for m in listing["markers"] if isinstance(m, dict) and m.get("frame") == frame
                     and m.get("name") == TEST_NAME]
        if same_name:
            return ["custom data가 저장되지 않았습니다"]
        if ours:
            frames = ", ".join(str(m.get("frame")) for m in ours)
            return [f"표시가 {frame}프레임이 아니라 {frames}프레임에 있습니다"]
        return [f"{frame}프레임에서 시험 표시를 다시 찾지 못했습니다"]
    problems = []
    if here[0].get("name") != TEST_NAME:
        problems.append("이름이 다르게 저장되었습니다")
    if here[0].get("note") != TEST_NOTE:
        problems.append("메모가 다르게 저장되었습니다")
    return problems


def _is_test_tone(item: Any) -> bool:
    path = item.get("path") if isinstance(item, dict) else None
    return isinstance(path, str) and path.replace("/", "\\").split("\\")[-1].lower() == TEST_TONE_NAME


def summarize(name: str, out: Out) -> Tuple[bool, str]:
    """단계 결과 → (성공 여부, 쉬운 말 한 줄)."""
    if name in ("connect", "auto"):
        text = describe_state(out.get("ping"), out.get("state"))
        if "state_error" in out:
            text += f" (타임라인 정보는 못 읽음: {out['state_error']['message']})"
        return True, text
    if name == "marker":
        marker = out.get("marker") or {}
        where = "재생 위치" if out.get("offset_how") == "playhead" else "재생 위치를 못 읽어 시작 + 1초"
        if not marker.get("added"):
            return False, "리졸브가 표시를 넣지 않았습니다 (AddMarker가 거절)."
        text = f"타임라인 시작에서 {marker.get('frame')}프레임({where})에 노란 표시를 넣었습니다."
        problems = marker_problems(out)
        if problems is None:
            return True, text + " (넣은 표시를 다시 읽어 확인하지는 못했습니다)"
        if problems:
            return False, text + " 그런데 다시 읽어 보니 " + ", ".join(problems) + "."
        return True, text
    if name == "audio":
        audio = out.get("audio") or {}
        if (audio.get("appended") or 0) < 1:
            return False, "트랙은 만들었지만 소리가 타임라인에 올라가지 않았습니다 (AppendToTimeline이 빈 답)."
        text = (f"오디오 트랙 {audio.get('track_index')}번(\"{TEST_NAME}\")에 "
                f"{TONE_SECONDS:g}초짜리 시험 소리를 넣었습니다.")
        if audio.get("track_named") is False:
            # 이름이 없으면 [시험 흔적 지우기]가 이 트랙을 찾지 못한다
            return False, text + (f" 그런데 트랙 이름을 \"{TEST_NAME}\"로 바꾸지 못해 [시험 흔적 지우기]로는 "
                                  "지워지지 않습니다. 리졸브에서 직접 지워 주세요.")
        return True, text
    if name == "cleanup":
        problems = [out[k]["message"] for k in ("delete_markers_error", "remove_audio_error") if k in out]
        removed = (out.get("remove_audio") or {}).get("removed_tracks", 0)
        skipped = (out.get("remove_audio") or {}).get("skipped", 0)
        deleted_answer = out.get("delete_markers") or {}
        deleted = deleted_answer.get("deleted_count")
        if deleted is None:
            deleted = 1 if deleted_answer.get("deleted") else 0
        text = f"{'시험 표시 %d개 지움' % deleted if deleted else '시험 표시 없음'}, 시험 트랙 {removed}개 지움"
        # 지운 뒤 다시 읽은 표시 목록 (못 읽었으면 스크립트가 센 남은 수)
        left_markers = _test_markers(out.get("markers_after"))
        left = len(left_markers) if left_markers is not None else deleted_answer.get("remaining")
        if isinstance(left, int) and left > 0:
            problems.append(f"시험 표시 {left}개가 아직 남아 있습니다. 리졸브에서 직접 지워 주세요")
        if skipped:
            # 이 창이 넣은 소리인지 확인할 수 없는 클립이 있으면 지우지 않는다 (사용자 것일 수 있음)
            problems.append(f"'{TEST_NAME}' 트랙 {skipped}개는 이 창이 넣은 것인지 확인하지 못해 남겨 두었습니다. "
                            "필요하면 리졸브에서 직접 지워 주세요")
        else:
            audio_left = [i for i in ((out.get("state") or {}).get("items") or {}).get("audio") or []
                          if _is_test_tone(i)]
            if audio_left:
                problems.append(f"시험 소리 {len(audio_left)}개가 타임라인에 아직 남아 있습니다. "
                                "리졸브에서 직접 지워 주세요")
        if problems:
            return False, text + " (" + " / ".join(problems) + ")"
        return True, text
    return True, ""
