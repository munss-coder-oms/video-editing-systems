"""리졸브 연결(engine.resolve_link): 우체통 글 형식, 타임코드, 경로, 앱 쪽 창구, 설치, 시험용 소리.

리졸브 대신 가짜 응답기(스레드)가 request.lua를 읽고 Fusion.prefs 비슷한 파일에 답을 적는다.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import wave
from array import array
from pathlib import Path

import pytest

from engine.resolve_link import SCRIPT_FILENAME, SCRIPT_VERSION
from engine.resolve_link import bridge as bridge_mod
from engine.resolve_link import install as install_mod
from engine.resolve_link import paths
from engine.resolve_link.bridge import BridgeCancelled, BridgeError, BridgeTimeout, LuaBridge
from engine.resolve_link.protocol import (
    OPS,
    IdGenerator,
    encode_request,
    from_hex,
    parse_responses,
    to_hex,
)
from engine.resolve_link.testfiles import TEST_TONE_NAME, make_test_tone
from engine.resolve_link.timecode import frames_to_tc, is_drop_frame, parse_fps, tc_offset, tc_to_frames

from .conftest import requires_ffmpeg

ROOT = Path(__file__).resolve().parent.parent

HOSTILE = [
    "",
    "한글 표시 이름",
    '"',
    "]]",
    "[[",
    "\\",
    "a\r\nb\n",
    "\x00nul\x00",
    '"}) resolve:GetProjectManager() --',
    "]] .. os.execute('calc') .. [[",
    "끝🎬",
]


# ── Lua 표 읽기 (테스트용) ─────────────────────────────────────────────


class _MiniLua:
    """우리 요청 형식(return {...}, 16진수 문자열, 숫자, true/false, 중첩 표)만 읽는 작은 해석기.

    이 밖의 문법(이스케이프, 함수 호출, 연결 연산 등)이 나오면 실패한다.
    """

    _NUM = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")
    _IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

    def __init__(self, text: str) -> None:
        self.s = text
        self.i = 0

    def parse(self):
        self._ws()
        assert self.s.startswith("return", self.i), "return으로 시작해야 함"
        self.i += len("return")
        value = self._value()
        self._ws()
        assert self.i == len(self.s), f"남은 글자: {self.s[self.i:]!r}"
        return value

    def _ws(self) -> None:
        while self.i < len(self.s) and self.s[self.i] in " \t\r\n":
            self.i += 1

    def _eat(self, ch: str) -> None:
        self._ws()
        assert self.s[self.i] == ch, f"{ch!r} 자리에 {self.s[self.i:self.i + 10]!r}"
        self.i += 1

    def _string(self) -> str:
        self._eat('"')
        end = self.s.index('"', self.i)
        body = self.s[self.i:end]
        assert re.fullmatch(r"[0-9A-Za-z_]*", body), f"16진수/이름이 아닌 문자열: {body!r}"
        self.i = end + 1
        return body

    def _value(self):
        self._ws()
        ch = self.s[self.i]
        if ch == "{":
            return self._table()
        if ch == '"':
            return self._string()
        m = self._IDENT.match(self.s, self.i)
        if m and m.group() in ("true", "false"):
            self.i = m.end()
            return m.group() == "true"
        m = self._NUM.match(self.s, self.i)
        assert m, f"값이 아님: {self.s[self.i:self.i + 10]!r}"
        self.i = m.end()
        text = m.group()
        return float(text) if any(c in text for c in ".eE") else int(text)

    def _table(self):
        self._eat("{")
        keyed, items = {}, []
        while True:
            self._ws()
            if self.s[self.i] == "}":
                self.i += 1
                break
            if self.s[self.i] == "[":
                self.i += 1
                key = self._string()
                self._eat("]")
                self._eat("=")
                keyed[key] = self._value()
            else:
                m = self._IDENT.match(self.s, self.i)
                rest = self.s[m.end():].lstrip() if m else ""
                if m and m.group() not in ("true", "false") and rest.startswith("="):
                    self.i = m.end()
                    self._eat("=")
                    keyed[m.group()] = self._value()
                else:
                    items.append(self._value())
            self._ws()
            if self.s[self.i] == ",":
                self.i += 1
        assert not (keyed and items), "이름 있는 칸과 순서 칸이 섞임"
        return items if items else keyed


def mini_lua(text: str):
    return _MiniLua(text).parse()


def _lupa_runtime():
    try:
        import lupa.luajit21 as luajit
    except ImportError:
        return None
    return luajit.LuaRuntime()


def _lua_to_py(lupa_mod, value):
    if lupa_mod.lua_type(value) != "table":
        return value
    keys = list(value.keys())
    if keys and all(isinstance(k, int) for k in keys):
        return [_lua_to_py(lupa_mod, value[k]) for k in sorted(keys)]
    return {k: _lua_to_py(lupa_mod, value[k]) for k in keys}


def lupa_load(lua, text: str):
    """리졸브처럼 빈 환경(setfenv)에서 요청 파일을 실행해 표를 얻는다."""
    import lupa.luajit21 as luajit

    load = lua.eval(
        "function(src) local f, err = loadstring(src, 'request.lua'); "
        "if not f then error(err) end; setfenv(f, {}); return f() end"
    )
    return _lua_to_py(luajit, load(text))


# ── 가짜 리졸브 (응답기) ──────────────────────────────────────────────


def prefs_text(owner: str, claim: int, response: str) -> str:
    """Fusion.prefs처럼 생긴 Lua 표 글."""
    return (
        "{\n"
        "\tGlobal = {\n"
        "\t\tAIHelper = {\n"
        f'\t\t\tOwner = "{owner}",\n'
        f"\t\t\tClaim = {claim},\n"
        f'\t\t\tResponse = "{response}",\n'
        "\t\t},\n"
        "\t\tPaths = {\n"
        "\t\t\tMap = {\n"
        '\t\t\t\t["Profile:"] = "C:\\\\Users\\\\홍길동\\\\Fusion\\\\",\n'
        "\t\t\t},\n"
        "\t\t},\n"
        "\t},\n"
        "}\n"
    )


def response_line(owner: str, req_id: int, payload: dict) -> str:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"AIH1:{owner}:{req_id}:{to_hex(body)}"


class FakeResolve(threading.Thread):
    """request.lua를 읽어 handler의 답을 Fusion.prefs에 적는 가짜 Lua 루프."""

    def __init__(self, mailbox: Path, prefs: Path, handler=None, owner: str = "o1700000000_4242", writer=None):
        super().__init__(daemon=True)
        self.mailbox = mailbox
        self.prefs = prefs
        self.handler = handler or (lambda req: {"ok": True, "sv": SCRIPT_VERSION, "result": {"op": req["op"]}})
        self.owner = owner
        self.writer = writer  # (prefs 경로, 완성된 글, 요청) → 직접 쓰기 (반쯤 쓰기 흉내 등)
        self.requests = []
        self.halt = threading.Event()
        self.last_id = 0

    def run(self) -> None:
        lua = _lupa_runtime()
        req_path = self.mailbox / "request.lua"
        while not self.halt.is_set():
            try:
                text = req_path.read_text(encoding="ascii")
            except (OSError, UnicodeDecodeError):
                time.sleep(0.01)
                continue
            req = lupa_load(lua, text) if lua is not None else mini_lua(text)
            if req["id"] <= self.last_id:
                time.sleep(0.01)
                continue
            self.last_id = req["id"]
            self.requests.append(req)
            payload = self.handler(req)
            full = prefs_text(self.owner, req["id"], response_line(self.owner, req["id"], payload))
            if self.writer:
                self.writer(self.prefs, full, req)
            else:
                self.prefs.write_text(full, encoding="utf-8")

    def stop(self) -> None:
        self.halt.set()
        self.join(timeout=5)


@pytest.fixture
def link_env(tmp_path, monkeypatch):
    """우체통·Fusion.prefs·앱 기록 폴더를 모두 임시 폴더로."""
    mailbox = tmp_path / "mailbox"
    prefs = tmp_path / "profile" / "Fusion.prefs"
    prefs.parent.mkdir(parents=True)
    prefs.write_text("{\n\tGlobal = {\n\t\tUser = \"original\",\n\t},\n}\n", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "state"))
    monkeypatch.setenv("AIH_MAILBOX_DIR", str(mailbox))
    monkeypatch.setenv("AIH_PREFS_FILE", str(prefs))
    responders = []

    def start(**kw) -> FakeResolve:
        r = FakeResolve(mailbox, prefs, **kw)
        r.start()
        responders.append(r)
        return r

    yield {"mailbox": mailbox, "prefs": prefs, "tmp": tmp_path, "start": start}
    for r in responders:
        r.stop()


# ── 글 형식 (protocol) ───────────────────────────────────────────────


@pytest.mark.parametrize("text", HOSTILE)
def test_hex_round_trip(text):
    h = to_hex(text)
    assert re.fullmatch(r"[0-9a-f]*", h)
    assert from_hex(h) == text
    assert bytes.fromhex(h) == text.encode("utf-8")


def test_from_hex_rejects_broken_input():
    with pytest.raises(ValueError):
        from_hex("abc")  # 홀수 길이 (반쯤 쓰인 답)
    with pytest.raises(ValueError):
        from_hex("zz")


def test_request_is_one_plain_statement():
    text = encode_request(5, "ping", t=1700000000)
    assert text == 'return {v=1,id=5,t=1700000000,op="ping",a={}}'


def test_request_strings_are_hex_only():
    args = {"name": HOSTILE[8], "note": "]]\r\n\\\"", "custom": "", "frame": 12, "duration": 1,
            "ratio": 0.5, "flag": False, "nested": {"x": "한글", "list": ["a", 1, True]}}
    text = encode_request(1700000000123, "add_marker", args, t=1700000000)
    text.encode("ascii")  # 한 글자도 ASCII 밖으로 나가지 않는다
    # 따옴표 사이에는 16진수와 op 이름만 있다
    for literal in re.findall(r'"([^"]*)"', text):
        assert re.fullmatch(r"[0-9a-f]*|add_marker", literal)
    assert "]]" not in text and "\\" not in text and "\n" not in text
    req = mini_lua(text)
    assert req["v"] == 1 and req["id"] == 1700000000123 and req["op"] == "add_marker"
    a = req["a"]
    assert from_hex(a["name"]) == HOSTILE[8]
    assert from_hex(a["note"]) == "]]\r\n\\\""
    assert a["custom"] == ""
    assert a["frame"] == 12 and a["ratio"] == 0.5 and a["flag"] is False
    assert from_hex(a["nested"]["x"]) == "한글"
    assert [from_hex(a["nested"]["list"][0]), a["nested"]["list"][1], a["nested"]["list"][2]] == ["a", 1, True]


def test_request_loads_in_luajit_sandbox():
    pytest.importorskip("lupa")
    lua = _lupa_runtime()
    if lua is None:
        pytest.skip("lupa.luajit21 없음")
    args = {f"s{i}": s for i, s in enumerate(HOSTILE)}
    args.update({"end": 7, "big": 2 ** 53, "neg": -3, "tiny": 1e-300, "f": 0.1, "empty": {}, "arr": [1, 2, 3]})
    text = encode_request(1700000000999, "state", args, t=123)
    req = lupa_load(lua, text)
    assert req["id"] == 1700000000999 and req["t"] == 123 and req["op"] == "state"
    a = req["a"]
    for i, s in enumerate(HOSTILE):
        assert bytes.fromhex(a[f"s{i}"]) == s.encode("utf-8")  # 바이트 그대로
    assert a["end"] == 7  # Lua 예약어 이름도 ["end"]=로 적혀 문법 오류가 없다
    assert a["big"] == 2 ** 53 and a["neg"] == -3 and a["tiny"] == 1e-300 and a["f"] == 0.1
    assert a["arr"] == [1, 2, 3]
    assert mini_lua(text)["a"]["s1"] == a["s1"]  # 테스트용 해석기와 LuaJIT가 같게 읽는다


@pytest.mark.parametrize(
    "op, args",
    [
        ("ping", {"x": float("nan")}),
        ("ping", {"x": float("inf")}),
        ("ping", {"x": -float("inf")}),
        ("ping", {"bad-key": 1}),
        ("ping", {"1x": 1}),
        ("ping", {"": 1}),
        ("ping", {"한글": 1}),
        ("ping", {"a b": 1}),
        ("ping", {1: 1}),
        ("ping", {"x": {"y": {"bad key": 1}}}),
        ("ping", {"x": [1, None]}),
        ("ping", {"x": 2 ** 60}),
        ("format_disk", {}),
        ("ping(); os.exit()", {}),
    ],
)
def test_request_refuses_bad_values(op, args):
    with pytest.raises(ValueError):
        encode_request(1, op, args)


def test_request_refuses_unknown_types_and_ids():
    with pytest.raises(TypeError):
        encode_request(1, "ping", {"x": b"bytes"})
    with pytest.raises(TypeError):
        encode_request(1, "ping", {"x": {1, 2}})
    for bad_id in (0, -1, True, 1.5):
        with pytest.raises(ValueError):
            encode_request(bad_id, "ping")


def test_none_value_is_left_out():
    assert encode_request(1, "ping", {"a": None, "b": 1}, t=1).endswith("a={b=1}}")


def test_all_ops_encode():
    for op in OPS:
        assert f'op="{op}"' in encode_request(1, op, t=1)


def test_parse_responses_picks_the_right_id():
    good = {"ok": True, "sv": "1.0.0", "result": {"project": "한글 \"프로젝트\"", "n": 3}}
    other = {"ok": False, "sv": "1.0.0", "error": "no_timeline", "func": "GetCurrentTimeline"}
    text = "\n".join([
        "garbage AIH1:: AIH1:o1:x:00 AIH1:o_1:12:zz",
        prefs_text("o1_1", 11, response_line("o1_1", 11, other)),
        response_line("o2_2", 12, good),
        "AIH1:o3_3:13:" + to_hex('{"ok":true}')[:-1],  # 홀수 길이 (쓰는 중)
        "AIH1:o3_3:14:" + to_hex('{"ok":true,"res')[:],  # JSON이 끊김
        "AIH1:o3_3:15:" + to_hex("[1,2]"),  # 객체가 아님
    ])
    found = parse_responses(text)
    assert [r.id for r in found] == [11, 12]
    by_id = {r.id: r for r in found}
    assert by_id[12].data == good and by_id[12].owner == "o2_2"
    assert by_id[11].data["error"] == "no_timeline"
    assert parse_responses("") == []
    assert parse_responses("아무 글 AIH2:o:1:7b7d") == []


def test_ids_strictly_increase():
    now = {"ns": 1_700_000_000_000 * 1_000_000}
    gen = IdGenerator(clock_ns=lambda: now["ns"])
    ids = [gen.next() for _ in range(5)]
    assert ids == [1_700_000_000_000 + i for i in range(5)]  # 같은 밀리초 안에서도 커진다
    now["ns"] -= 60 * 1_000_000_000  # 시계가 1분 뒤로 가도
    assert gen.next() == ids[-1] + 1
    now["ns"] += 3600 * 1_000_000_000
    assert gen.next() == now["ns"] // 1_000_000  # 시간이 흐르면 시각을 따른다
    # 앱을 다시 켜도(새 생성기) 이전 번호보다 크다
    assert IdGenerator().next() > 1_700_000_000_000
    assert IdGenerator(last=10 ** 15).next() == 10 ** 15 + 1


def test_ids_unique_across_threads():
    gen = IdGenerator()
    out = []
    lock = threading.Lock()

    def take():
        got = [gen.next() for _ in range(200)]
        with lock:
            out.extend(got)

    threads = [threading.Thread(target=take) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(out)) == 800


# ── 타임코드 ─────────────────────────────────────────────────────────


def test_parse_fps():
    assert parse_fps("29.97") == (29.97, 30)
    assert parse_fps("23.976") == (23.976, 24)
    assert parse_fps("59.94 DF") == (59.94, 60)
    assert parse_fps(25) == (25.0, 25)
    with pytest.raises(ValueError):
        parse_fps("abc")
    with pytest.raises(ValueError):
        parse_fps("0")


def test_non_drop_timecode():
    assert tc_to_frames("00:00:01:00", "25") == 25
    assert tc_to_frames("01:00:00:00", "24") == 86400
    assert tc_to_frames("00:01:00:00", "23.976") == 1440  # 23.976은 드롭 프레임이 없다
    assert tc_to_frames("00:01:00:00", "23.976", drop_frame=True) == 1440
    assert tc_to_frames("00:01:00:00", "29.97") == 1800  # ':'이면 논드롭
    assert tc_to_frames("00:00:10:15", "30") == 315
    assert tc_to_frames("00:00:01;00", "25") == 25  # 25fps에는 드롭 프레임이 없다
    assert frames_to_tc(86400, "24") == "01:00:00:00"
    assert frames_to_tc(1440, "23.976") == "00:01:00:00"


def test_drop_frame_2997():
    assert tc_to_frames("00:01:00;02", "29.97") == 1800
    assert tc_to_frames("00:00:59;29", "29.97") == 1799
    assert tc_to_frames("00:10:00;00", "29.97") == 17982
    assert tc_to_frames("01:00:00;00", "29.97") == 107892
    assert tc_to_frames("00:01:00:02", "29.97", drop_frame="1") == 1800  # 설정값 "1"
    assert tc_to_frames("00:01:00;02", "29.97", drop_frame="0") == 1802
    assert frames_to_tc(1800, "29.97", drop_frame=True) == "00:01:00;02"
    assert frames_to_tc(17982, "29.97", True) == "00:10:00;00"
    assert frames_to_tc(107892, "29.97", True) == "01:00:00;00"


def test_drop_frame_5994():
    assert tc_to_frames("00:01:00;04", "59.94") == 3600
    assert tc_to_frames("00:10:00;00", "59.94") == 35964
    assert tc_to_frames("01:00:00;00", "59.94") == 215784
    assert frames_to_tc(3600, "59.94", True) == "00:01:00;04"


@pytest.mark.parametrize("fps, drop", [("29.97", True), ("59.94", True), ("23.976", False), ("25", False), ("30", False)])
def test_timecode_round_trip(fps, drop):
    for n in list(range(0, 5000, 7)) + [17981, 17982, 17983, 35963, 35964, 107891, 107892, 215784, 400000]:
        assert tc_to_frames(frames_to_tc(n, fps, drop), fps) == n


def test_timecode_offset_uses_one_rule_for_both_timecodes():
    """시작은 ':'로, 재생 위치는 ';'로 받아도 두 값을 같은 규칙(드롭 프레임)으로 센다."""
    assert tc_offset("01:00:10;00", "01:00:00:00", "29.97") == 300
    assert tc_offset("01:00:10:00", "01:00:00;00", "29.97") == 300
    # 설정값은 논드롭이라는데 리졸브가 ';'로 보여 주면 보여 준 대로 센다
    assert tc_offset("00:10:00;00", "00:00:00;00", "29.97", drop_frame="0") == 17982
    assert tc_offset("00:10:00:00", "00:00:00:00", "29.97", drop_frame="1") == 17982
    assert tc_offset("00:10:00:00", "00:00:00:00", "29.97", drop_frame=False) == 18000
    assert is_drop_frame("29.97 DF") and not is_drop_frame("29.97 NDF") and not is_drop_frame("29.97")
    assert is_drop_frame("29.97", "01:00:00:00", "01:00:00;00") and not is_drop_frame(25, "01:00:00:00", flag=None)


def test_timecode_offset_and_errors():
    assert tc_offset("01:00:10:00", "01:00:00:00", "24") == 240
    assert tc_offset("01:01:00;02", "01:00:00;00", "29.97") == 1800
    for bad in ("abc", "", "00:00:00", "00:00:00:30", "00:61:00:00", None):
        with pytest.raises(ValueError):
            tc_to_frames(bad, "30")
    with pytest.raises(ValueError):
        frames_to_tc(-1, "30")


# ── 경로 ─────────────────────────────────────────────────────────────


def _ascii_after(prefix: Path):
    """임시 폴더(prefix) 뒤쪽만 보고 영문인지 판단한다.

    윈도우에서 pytest의 임시 폴더에는 사용자 이름이 들어가므로, 한글 사용자 PC(바로 이 앱을 쓰는 PC)에서도
    이 시험들이 임시 폴더 이름 때문에 실패하지 않게 한다.
    """
    head = str(prefix)
    return lambda text: (text[len(head):] if text.startswith(head) else text).isascii()


@pytest.fixture
def clean_paths_env(tmp_path, monkeypatch):
    for var in ("AIH_MAILBOX_DIR", "AIH_PREFS_FILE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "pd"))
    monkeypatch.setenv("PUBLIC", str(tmp_path / "public"))
    monkeypatch.setattr(paths, "_is_ascii", _ascii_after(tmp_path))
    return tmp_path


def test_mailbox_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("AIH_MAILBOX_DIR", str(tmp_path / "mb"))
    assert paths.mailbox_dir() == tmp_path / "mb"
    assert paths.files_dir() == tmp_path / "mb" / "files"
    assert (tmp_path / "mb" / "files").is_dir()


def test_mailbox_default_is_saved(clean_paths_env):
    tmp = clean_paths_env
    expected = tmp / "local" / "video-editing-systems" / "bridge"
    assert paths.mailbox_dir() == expected
    saved = tmp / "local" / "video-editing-systems" / "bridge_path.txt"
    assert saved.read_text(encoding="utf-8") == str(expected)
    assert paths.files_dir() == expected / "files"


def test_mailbox_non_ascii_user_uses_short_name(clean_paths_env, monkeypatch):
    tmp = clean_paths_env
    monkeypatch.setenv("LOCALAPPDATA", str(tmp / "홍길동" / "AppData" / "Local"))
    short = tmp / "HONGGI~1"
    short.mkdir()
    monkeypatch.setattr(paths, "_short_path", lambda p: str(short))
    assert paths.mailbox_dir() == short


def test_mailbox_non_ascii_user_falls_back_to_programdata(clean_paths_env, monkeypatch):
    tmp = clean_paths_env
    local = tmp / "홍길동" / "AppData" / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("USERNAME", "홍길동")
    # 짧은 이름이 꺼져 있으면 GetShortPathNameW가 긴(한글) 경로를 그대로 준다
    monkeypatch.setattr(paths, "_short_path", lambda p: str(p))
    tag = hashlib.sha1("홍길동".encode("utf-8")).hexdigest()[:8]
    expected = tmp / "pd" / "video-editing-systems" / f"bridge-{tag}"
    got = paths.mailbox_dir()
    assert got == expected and got.is_dir()
    str(got)[len(str(tmp)):].encode("ascii")
    # 한 번 고른 경로를 기억한다 (앱과 설치 프로그램이 같은 곳을 쓰도록)
    saved = local / "video-editing-systems" / "bridge_path.txt"
    assert saved.read_text(encoding="utf-8") == str(expected)
    monkeypatch.setattr(paths, "_short_path", lambda p: pytest.fail("기억한 경로가 있으면 다시 고르지 않는다"))
    assert paths.mailbox_dir() == expected


def test_mailbox_when_programdata_is_locked(clean_paths_env, monkeypatch):
    """ProgramData에 쓸 수 없는 PC (회사 PC 등): %PUBLIC%에, 거기도 안 되면 한글 경로라도 쓰고 오류는 내지 않는다."""
    tmp = clean_paths_env
    monkeypatch.setenv("LOCALAPPDATA", str(tmp / "홍길동" / "AppData" / "Local"))
    monkeypatch.setenv("USERNAME", "홍길동")
    monkeypatch.setattr(paths, "_short_path", lambda p: None)
    (tmp / "pd").write_text("폴더가 아니라 파일이라 그 아래에 만들 수 없음", encoding="utf-8")
    tag = hashlib.sha1("홍길동".encode("utf-8")).hexdigest()[:8]
    assert paths.mailbox_dir() == tmp / "public" / "video-editing-systems" / f"bridge-{tag}"

    (tmp / "홍길동" / "AppData" / "Local" / "video-editing-systems" / "bridge_path.txt").unlink()
    import shutil
    shutil.rmtree(tmp / "public")
    (tmp / "public").write_text("x", encoding="utf-8")
    got = paths.mailbox_dir()  # 오류 없이 창을 띄울 수 있어야 한다
    assert got == tmp / "홍길동" / "AppData" / "Local" / "video-editing-systems" / "bridge"


def test_long_path_only_when_different(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "_long_path", lambda p: "C:\\Users\\홍길동\\bridge")
    assert paths.long_path(Path("C:/Users/HONGGI~1/bridge")) == "C:\\Users\\홍길동\\bridge"
    monkeypatch.setattr(paths, "_long_path", lambda p: str(p))
    assert paths.long_path(tmp_path) is None
    monkeypatch.setattr(paths, "_long_path", lambda p: None)
    assert paths.long_path(tmp_path) is None
    if sys.platform != "win32":
        monkeypatch.undo()
        assert paths.long_path(tmp_path) is None and paths.resolve_exe_versions() == []


def test_saved_mailbox_recomputed_when_gone(clean_paths_env):
    tmp = clean_paths_env
    state = tmp / "local" / "video-editing-systems"
    state.mkdir(parents=True)
    (state / "bridge_path.txt").write_text(str(tmp / "사라진 폴더"), encoding="utf-8")
    assert paths.mailbox_dir() == state / "bridge"
    (state / "bridge_path.txt").write_text(str(tmp / "gone"), encoding="utf-8")
    assert paths.mailbox_dir() == state / "bridge"
    assert (state / "bridge_path.txt").read_text(encoding="utf-8") == str(state / "bridge")


def test_utility_dirs_and_installed_scripts(clean_paths_env, monkeypatch):
    tmp = clean_paths_env
    monkeypatch.setenv("APPDATA", str(tmp / "roaming"))
    dirs = paths.resolve_utility_dirs()
    assert dirs[0] == tmp / "roaming" / "Blackmagic Design" / "DaVinci Resolve" / "Support" / "Fusion" / "Scripts" / "Utility"
    assert tmp / "pd" / "Blackmagic Design" / "DaVinci Resolve" / "Fusion" / "Scripts" / "Utility" in dirs
    assert paths.installed_scripts() == []
    dirs[0].mkdir(parents=True)
    (dirs[0] / SCRIPT_FILENAME).write_text("--", encoding="utf-8")
    assert paths.installed_scripts() == [dirs[0] / SCRIPT_FILENAME]


def test_find_fusion_prefs(clean_paths_env, monkeypatch):
    tmp = clean_paths_env
    monkeypatch.setenv("APPDATA", str(tmp / "roaming"))
    monkeypatch.setenv("HOME", str(tmp / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp / "home"))
    near = tmp / "roaming" / "Blackmagic Design" / "DaVinci Resolve" / "Support" / "Fusion" / "Profiles" / "Default" / "Fusion.prefs"
    other = tmp / "pd" / "Blackmagic Design" / "DaVinci Resolve" / "Fusion" / "Fusion.prefs"
    too_deep = tmp / "local" / "Blackmagic Design" / "a" / "b" / "c" / "d" / "e" / "f" / "g" / "h" / "i" / "Fusion.prefs"
    for p in (near, other, too_deep):
        p.parent.mkdir(parents=True)
        p.write_text("{}", encoding="utf-8")
    os.utime(other, (time.time() - 100, time.time() - 100))
    found = paths.find_fusion_prefs()
    assert found[:2] == [near, other]  # 최근에 바뀐 것부터
    assert too_deep not in found
    monkeypatch.setenv("AIH_PREFS_FILE", str(tmp / "x" / "Fusion.prefs"))
    assert paths.find_fusion_prefs() == [tmp / "x" / "Fusion.prefs"]


# ── 앱 쪽 창구 (LuaBridge) ────────────────────────────────────────────


def test_ping_round_trip(link_env):
    fake = link_env["start"](handler=lambda req: {
        "ok": True, "sv": SCRIPT_VERSION,
        "result": {"script_version": SCRIPT_VERSION, "owner": "o1700000000_4242",
                   "product": "DaVinci Resolve", "resolve_version": "21.1.0.5",
                   "env": {"loadfile": True, "io": False}, "note": "한글 \"따옴표\" \\ ]]\n"},
    })
    b = LuaBridge(poll_interval=0.02)
    before = int(time.time())
    result = b.ping(timeout=5)
    assert result["product"] == "DaVinci Resolve"
    assert result["env"] == {"loadfile": True, "io": False}
    assert result["note"] == "한글 \"따옴표\" \\ ]]\n"
    assert b.connected and b.prefs_path == link_env["prefs"]
    assert b.owner == "o1700000000_4242" and b.script_version == SCRIPT_VERSION
    assert b.last_response["ok"] is True
    req = fake.requests[0]
    assert req["v"] == 1 and req["op"] == "ping" and req["a"] == {}
    assert abs(req["t"] - before) <= 2


def test_arguments_arrive_byte_exact(link_env):
    fake = link_env["start"]()
    b = LuaBridge(poll_interval=0.02)
    name = '"}) resolve:GetProjectManager() -- ]] 한글'
    b.add_marker(120, color="Yellow", name=name, note="줄1\r\n줄2", custom="aih_test", duration=1, timeout=5)
    b.place_audio(Path("C:/x/files/aih_test_tone.wav"), "AI 도우미 시험", 86520, 90, timeout=5)
    a1, a2 = fake.requests[0]["a"], fake.requests[1]["a"]
    assert fake.requests[0]["op"] == "add_marker"
    assert bytes.fromhex(a1["name"]) == name.encode("utf-8")
    assert from_hex(a1["note"]) == "줄1\r\n줄2" and from_hex(a1["color"]) == "Yellow"
    assert from_hex(a1["custom"]) == "aih_test" and a1["frame"] == 120 and a1["duration"] == 1
    assert fake.requests[1]["op"] == "place_audio"
    assert from_hex(a2["path"]) == str(Path("C:/x/files/aih_test_tone.wav"))
    assert from_hex(a2["track_name"]) == "AI 도우미 시험"
    assert a2["record_frame"] == 86520 and a2["frames"] == 90


def test_several_requests_in_a_row(link_env):
    fake = link_env["start"]()
    b = LuaBridge(poll_interval=0.02)
    assert b.ping(timeout=5) == {"op": "ping"}
    assert b.state(timeout=5) == {"op": "state"}
    assert b.get_markers(timeout=5) == {"op": "get_markers"}
    assert b.delete_markers("aih_test", timeout=5) == {"op": "delete_markers"}
    assert b.remove_audio("AI 도우미 시험", timeout=5) == {"op": "remove_audio"}
    assert b.stop(timeout=5) == {"op": "stop"}
    ids = [r["id"] for r in fake.requests]
    assert ids == sorted(ids) and len(set(ids)) == 6


def test_requests_from_two_threads_are_serialised(link_env):
    fake = link_env["start"]()
    b = LuaBridge(poll_interval=0.02)
    results, errors = [], []

    def worker():
        try:
            for _ in range(3):
                results.append(b.ping(timeout=5))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and len(results) == 6 and len(fake.requests) == 6


def test_timeout_says_what_to_click(link_env):
    b = LuaBridge(poll_interval=0.02)
    start = time.monotonic()
    with pytest.raises(BridgeTimeout) as info:
        b.ping(timeout=0.3)
    assert 0.25 <= time.monotonic() - start < 3
    msg = str(info.value)
    assert "Scripts" in msg and "AI_Helper_Connect" in msg and "창" in msg
    assert info.value.op == "ping"
    assert not b.connected
    # 답이 없던 요청은 지운다 (나중에 메뉴를 눌렀을 때 뒤늦게 실행되지 않게)
    assert not (link_env["mailbox"] / "request.lua").exists()


def test_timeout_uses_injected_clock(link_env):
    ticks = {"t": 0.0}

    def clock():
        ticks["t"] += 1.0  # 부를 때마다 1초씩 흐르는 가짜 시계
        return ticks["t"]

    b = LuaBridge(clock=clock, poll_interval=0.001)
    with pytest.raises(BridgeTimeout):
        b.ping(timeout=5)
    assert ticks["t"] < 20


def test_lua_failure_raises_bridge_error(link_env):
    calls = {"MediaPool.ImportMedia": "err:bad argument #1 to 'ImportMedia'", "Folder.GetClipList": "ok"}
    link_env["start"](handler=lambda req: {
        "ok": False, "sv": SCRIPT_VERSION, "error": "no_timeline", "func": "GetCurrentTimeline", "calls": calls})
    b = LuaBridge(poll_interval=0.02)
    with pytest.raises(BridgeError) as info:
        b.state(timeout=5)
    err = info.value
    assert err.error == "no_timeline" and err.func == "GetCurrentTimeline" and err.op == "state"
    assert "no_timeline" in str(err) and "실패" in str(err)
    assert err.payload["calls"] == calls  # 어떤 함수가 어떤 오류를 냈는지 결과 파일에 남길 수 있다
    assert b.connected  # Lua가 대답은 했다


def test_ids_start_above_claim_saved_before_clock_went_back(link_env):
    """PC 시계가 뒤로 가도 새 요청 번호는 Fusion.prefs에 저장된 번호(Claim, 답)보다 크다."""
    future = int(time.time() * 1000) + 9 * 3600 * 1000
    link_env["prefs"].write_text(
        prefs_text("o_old", future, response_line("o_old", future - 5, {"ok": True, "sv": "1.0.0", "result": {}}))
        .replace(f"Claim = {future}", f'Claim = "{future}"'), encoding="utf-8")
    fake = link_env["start"]()
    b = LuaBridge(poll_interval=0.02)
    assert b.ping(timeout=5) == {"op": "ping"}
    assert fake.requests[0]["id"] == future + 1


def test_late_answer_is_remembered(link_env):
    """답을 기다리다 그만둔 뒤에 온 답은 따로 기록한다 (스크립트는 도는데 답이 늦은 것)."""
    delays = [0.3, 0.6]  # 첫 요청은 0.6초 뒤에, 다음 요청은 0.3초 뒤에 답한다 (늦은 답이 잠시 파일에 남게)

    def handler(req):
        if delays:
            time.sleep(delays.pop())
        return {"ok": True, "sv": SCRIPT_VERSION, "result": {"op": req["op"]}}

    link_env["start"](handler=handler)
    b = LuaBridge(poll_interval=0.02)
    with pytest.raises(BridgeTimeout) as info:
        b.ping(timeout=0.2)
    first = info.value.req_id
    assert b.timed_out == {first: "ping"}
    assert b.ping(timeout=5) == {"op": "ping"}
    assert [(a["id"], a["op"], a["ok"]) for a in b.late_answers] == [(first, "ping", True)]


def test_timeout_says_when_prefs_changed_without_an_answer(link_env):
    """Fusion.prefs는 바뀌었는데 읽을 수 있는 답이 없으면 알려 준다 (답을 못 읽는 문제)."""
    b = LuaBridge(poll_interval=0.02)
    with pytest.raises(BridgeTimeout) as info:
        b.ping(timeout=0.2)
    assert info.value.prefs_changed == []  # 아무도 답하지 않음 (스크립트를 아직 안 누름)

    def writer(prefs: Path, full: str, req: dict) -> None:
        prefs.write_text(full.replace("AIH1:", "AIH1:?"), encoding="utf-8")  # 알아볼 수 없는 모양

    link_env["start"](writer=writer)
    with pytest.raises(BridgeTimeout) as info:
        b.ping(timeout=0.5)
    assert info.value.prefs_changed == [str(link_env["prefs"])]


def _partial_writer(cut_to_odd: bool):
    def write(prefs: Path, full: str, req: dict) -> None:
        m = re.search(r"(AIH1:[^:]+:\d+:)([0-9a-f]+)", full)
        hex_part = m.group(2)
        cut = len(hex_part) // 2 + (1 if cut_to_odd and (len(hex_part) // 2) % 2 == 0 else 0)
        if not cut_to_odd:
            cut -= cut % 2
        partial = full[:m.start(2)] + hex_part[:cut]
        # 크기와 수정 시각까지 완성본과 똑같게 만들어, 시각만 보는 방식으로는 다시 읽지 않게 한다
        partial = partial + " " * (len(full) - len(partial))
        stamp = time.time_ns()
        prefs.write_text(partial, encoding="utf-8")
        os.utime(prefs, ns=(stamp, stamp))
        time.sleep(0.4)
        prefs.write_text(full, encoding="utf-8")
        os.utime(prefs, ns=(stamp, stamp))

    return write


@pytest.mark.parametrize("cut_to_odd", [True, False])
def test_half_written_prefs_are_retried(link_env, cut_to_odd):
    link_env["start"](writer=_partial_writer(cut_to_odd), handler=lambda req: {
        "ok": True, "sv": SCRIPT_VERSION, "result": {"project": "시험 프로젝트", "pad": "x" * 200}})
    b = LuaBridge(poll_interval=0.02)
    result = b.state(timeout=5)
    assert result["project"] == "시험 프로젝트"


def test_other_ids_in_prefs_are_ignored(link_env):
    def writer(prefs: Path, full: str, req: dict) -> None:
        stale = response_line("o_old", req["id"] - 1, {"ok": True, "sv": "0.9", "result": {"which": "old"}})
        prefs.write_text(prefs_text("o_old", req["id"] - 1, stale), encoding="utf-8")
        time.sleep(0.2)
        prefs.write_text(full, encoding="utf-8")

    link_env["start"](writer=writer, handler=lambda req: {"ok": True, "sv": SCRIPT_VERSION, "result": {"which": "new"}})
    b = LuaBridge(poll_interval=0.02)
    assert b.ping(timeout=5) == {"which": "new"}


def test_request_file_replace_retries_while_locked(link_env, monkeypatch):
    link_env["start"]()
    real_replace = os.replace
    calls = {"n": 0}

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] <= 3:
            raise PermissionError(13, "Lua가 파일을 읽는 중")
        return real_replace(src, dst)

    monkeypatch.setattr(bridge_mod.os, "replace", flaky_replace)
    b = LuaBridge(poll_interval=0.02)
    assert b.ping(timeout=5) == {"op": "ping"}
    assert calls["n"] == 4


def test_request_file_locked_too_long(link_env, monkeypatch):
    def always_locked(src, dst):
        raise PermissionError(13, "잠김")

    monkeypatch.setattr(bridge_mod.os, "replace", always_locked)
    monkeypatch.setattr(bridge_mod, "_REPLACE_RETRY_SECONDS", 0.2)
    with pytest.raises(BridgeError) as info:
        LuaBridge(poll_interval=0.02).ping(timeout=1)
    assert info.value.error == "write_failed"


def test_unknown_op_is_not_sent(link_env):
    b = LuaBridge(poll_interval=0.02)
    with pytest.raises(ValueError):
        b.request("format_disk", timeout=0.1)
    assert not (link_env["mailbox"] / "request.lua").exists()


def test_close_stops_waiting(link_env):
    b = LuaBridge(poll_interval=0.02)
    errors = []

    def waiting():
        try:
            b.ping(timeout=30)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=waiting)
    start = time.monotonic()
    t.start()
    time.sleep(0.2)
    b.close()
    t.join(timeout=5)
    assert time.monotonic() - start < 3
    assert len(errors) == 1 and isinstance(errors[0], BridgeCancelled)
    with pytest.raises(BridgeCancelled):
        b.ping(timeout=1)


def test_fusion_prefs_backed_up_once(link_env):
    link_env["start"]()
    backup = link_env["tmp"] / "state" / "video-editing-systems" / "backup"
    original = link_env["prefs"].read_text(encoding="utf-8")
    b = LuaBridge(poll_interval=0.02)
    b.ping(timeout=5)
    b.ping(timeout=5)
    assert link_env["prefs"].read_text(encoding="utf-8") != original  # 응답기가 파일을 바꿨다
    LuaBridge(poll_interval=0.02).ping(timeout=5)  # 앱을 다시 켜도 다시 백업하지 않는다
    baks = sorted(backup.glob("Fusion.prefs.*.bak"))
    assert [p.name for p in baks] == ["Fusion.prefs.1.bak"]
    assert baks[0].read_text(encoding="utf-8") == original
    index = (backup / "Fusion.prefs.index.txt").read_text(encoding="utf-8")
    assert str(link_env["prefs"]) in index
    assert list(bridge_mod.prefs_backups(backup)) == baks


def test_backup_waits_until_prefs_exist(link_env, tmp_path):
    missing = tmp_path / "later" / "Fusion.prefs"
    b = LuaBridge(prefs_files=[missing], backup_dir=tmp_path / "bk", poll_interval=0.02)
    with pytest.raises(BridgeTimeout):
        b.ping(timeout=0.1)
    assert not (tmp_path / "bk").exists()
    missing.parent.mkdir()
    missing.write_text("x", encoding="utf-8")
    with pytest.raises(BridgeTimeout):
        b.ping(timeout=0.1)
    assert [p.name for p in (tmp_path / "bk").glob("*.bak")] == ["Fusion.prefs.1.bak"]


# ── 설치 ─────────────────────────────────────────────────────────────

FAKE_TEMPLATE = (
    "-- AI 도우미 연결 (시험용 가짜 틀)\n"
    'local MAILBOX_HEX = "@@MAILBOX_HEX@@"\n'
    'local SCRIPT_VERSION = "@@SCRIPT_VERSION@@"\n'
    "return MAILBOX_HEX, SCRIPT_VERSION\n"
)


@pytest.fixture
def install_env(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "pd"))
    monkeypatch.setenv("AIH_MAILBOX_DIR", str(tmp_path / "mailbox"))
    template = tmp_path / "template.lua"
    template.write_bytes(b"\xef\xbb\xbf" + FAKE_TEMPLATE.encode("utf-8"))
    monkeypatch.setattr(install_mod, "default_template", lambda: template)
    return tmp_path


def test_install_fills_placeholders_and_is_idempotent(install_env):
    tmp = install_env
    target_dir = tmp / "roaming" / "Blackmagic Design" / "DaVinci Resolve" / "Support" / "Fusion" / "Scripts" / "Utility"
    first = install_mod.install_script()
    target = target_dir / SCRIPT_FILENAME
    assert first.paths_written == [target] and first.unchanged == [] and first.paths == [target]
    assert first.mailbox == tmp / "mailbox" and first.version == SCRIPT_VERSION
    data = target.read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf")  # BOM이 있으면 LuaJIT가 문법 오류를 낸다
    text = data.decode("utf-8")
    assert "@@" not in text
    assert f'local MAILBOX_HEX = "{install_mod.mailbox_hex(tmp / "mailbox")}"' in text
    assert f'local SCRIPT_VERSION = "{SCRIPT_VERSION}"' in text
    assert "AI 도우미" in text
    assert (tmp / "mailbox" / "files").is_dir()
    assert "설치했습니다" in first.message

    stamp = target.stat().st_mtime_ns
    time.sleep(0.02)
    second = install_mod.install_script()
    assert second.paths_written == [] and second.unchanged == [target]
    assert target.stat().st_mtime_ns == stamp and target.read_bytes() == data
    assert "이미" in second.message
    assert not list(target_dir.glob("*.tmp"))


def test_install_rewrites_old_script(install_env):
    result = install_mod.install_script()
    target = result.paths[0]
    target.write_text("-- 예전 버전", encoding="utf-8")
    again = install_mod.install_script()
    assert again.paths_written == [target]
    assert "@@" not in target.read_text(encoding="utf-8")


def test_install_template_hex_is_readable_by_lua(install_env):
    try:
        import lupa.luajit21 as luajit
    except ImportError:
        pytest.skip("lupa.luajit21 없음")
    lua = luajit.LuaRuntime(encoding=None)  # Lua 글자를 바이트 그대로 받는다
    target = install_mod.install_script().paths[0]
    hex_path, version = lua.execute(target.read_bytes())
    decoded = lua.eval(b"function(h) return (h:gsub('..', function(c) return string.char(tonumber(c, 16)) end)) end")(hex_path)
    # LuaJIT가 윈도우에서 파일을 여는 인코딩(ANSI 코드 페이지) 그대로
    encoding = "mbcs" if sys.platform == "win32" else "utf-8"
    assert decoded == str(install_env / "mailbox").encode(encoding) and version == SCRIPT_VERSION.encode()


def test_install_embeds_long_mailbox_for_comparison(install_env, monkeypatch, tmp_path):
    template = tmp_path / "long.lua"
    template.write_text('local A = "@@MAILBOX_HEX@@"\nlocal B = "@@MAILBOX_LONG_HEX@@"\nlocal V = "@@SCRIPT_VERSION@@"\n',
                        encoding="utf-8")
    long = "C:\\Users\\홍길동\\AppData\\Local\\video-editing-systems\\bridge"
    monkeypatch.setattr(paths, "_long_path", lambda p: long)
    text = install_mod.install_script(template=template).paths[0].read_text(encoding="utf-8")
    assert f'local B = "{long.encode("utf-8").hex()}"' in text  # 비교용이라 UTF-8 (리졸브가 주는 경로와 같은 인코딩)
    monkeypatch.setattr(paths, "_long_path", lambda p: None)
    text = install_mod.install_script(template=template).paths[0].read_text(encoding="utf-8")
    assert 'local B = ""' in text and "@@" not in text


def test_install_refuses_template_without_placeholders(install_env, tmp_path):
    bad = tmp_path / "bad.lua"
    bad.write_text("return 1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        install_mod.install_script(template=bad)


def test_install_cli_without_resolve(install_env, monkeypatch, capsys):
    monkeypatch.setattr(install_mod, "resolve_installed", lambda: False)
    assert install_mod.main([]) == 0
    out = capsys.readouterr().out
    assert "설치했습니다" in out and "리졸브를 설치하면" in out
    monkeypatch.setattr(install_mod, "resolve_installed", lambda: True)
    assert install_mod.main([]) == 0
    assert "Workspace" in capsys.readouterr().out


def test_install_cli_reports_failure(install_env, monkeypatch, capsys):
    monkeypatch.setattr(install_mod, "default_template", lambda: install_env / "없는 파일.lua")
    assert install_mod.main([]) == 1
    assert "설치하지 못했습니다" in capsys.readouterr().out


def test_install_module_runs_as_main():
    proc = subprocess.run(
        [sys.executable, "-W", "error::RuntimeWarning", "-m", "engine.resolve_link.install", "--help"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=dict(os.environ, PYTHONIOENCODING="utf-8"),
    )
    assert proc.returncode == 0, proc.stderr
    assert "AI_Helper_Connect" in proc.stdout


def test_real_template_renders(tmp_path):
    template = install_mod.default_template()
    if not template.is_file():
        pytest.skip("resolve_scripts/AI_Helper_Connect.lua가 아직 없음")
    target_dir = tmp_path / "Utility"
    result = install_mod.install_script(target_dir=target_dir, mailbox=tmp_path / "mb")
    text = result.paths[0].read_text(encoding="utf-8")
    assert "@@MAILBOX_HEX@@" not in text and "@@SCRIPT_VERSION@@" not in text
    assert install_mod.mailbox_hex(tmp_path / "mb") in text and "@@MAILBOX_LONG_HEX@@" not in text


# ── 시험용 소리 ───────────────────────────────────────────────────────


def _check_tone(path: Path, seconds: float = 3.0) -> None:
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == 48000
        assert w.getnchannels() == 2
        assert w.getsampwidth() == 2
        assert w.getnframes() == int(seconds * 48000)
        raw = w.readframes(w.getnframes())
    samples = array("h")
    samples.frombytes(raw)
    if sys.byteorder == "big":
        samples.byteswap()
    left, right = samples[0::2], samples[1::2]
    assert left == right
    peak = max(abs(v) for v in left)
    assert 3200 <= peak <= 3330  # -20 dBFS = 0.1 × 최대값
    rms = math.sqrt(sum(v * v for v in left[:48000]) / 48000) / 32768
    assert abs(20 * math.log10(rms) - (-23.0)) < 0.3  # 사인파 RMS는 최대값보다 3dB 작다
    # 1kHz: 1초 동안 0을 위로 지나는 횟수가 1000번쯤
    ups = sum(1 for a, b in zip(left[:48000], left[1:48001]) if a < 0 <= b)
    assert 995 <= ups <= 1005


@requires_ffmpeg
def test_test_tone_with_ffmpeg(tmp_path, monkeypatch):
    from engine.resolve_link import testfiles

    monkeypatch.setattr(testfiles, "_write_with_wave", lambda *a: pytest.fail("FFmpeg로 만들어야 함"))
    path = make_test_tone(path=tmp_path / "tone.wav")
    _check_tone(path)
    assert not list(tmp_path.glob("*.part.wav"))


def test_test_tone_without_ffmpeg(tmp_path):
    path = make_test_tone(path=tmp_path / "tone.wav", use_ffmpeg=False)
    _check_tone(path)


def test_test_tone_default_place_and_reuse(tmp_path, monkeypatch):
    monkeypatch.setenv("AIH_MAILBOX_DIR", str(tmp_path / "mb"))
    path = make_test_tone(use_ffmpeg=False)
    assert path == tmp_path / "mb" / "files" / TEST_TONE_NAME
    stamp = path.stat().st_mtime_ns
    time.sleep(0.02)
    assert make_test_tone(use_ffmpeg=False) == path
    assert path.stat().st_mtime_ns == stamp  # 리졸브가 쥐고 있을 수 있어 같은 파일은 다시 쓰지 않는다
    path.write_bytes(b"broken")
    make_test_tone(use_ffmpeg=False)
    _check_tone(path)
    make_test_tone(seconds=1.0, use_ffmpeg=False)
    _check_tone(path, seconds=1.0)
