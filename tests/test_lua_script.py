"""리졸브 연결 스크립트(resolve_scripts/AI_Helper_Connect.lua) 시험.

리졸브 없이 LuaJIT(lupa)에서 실제 스크립트를 돌린다. 리졸브 21 무료판 스크립트 메뉴처럼
io, require, package, ffi, debug, os.execute, os.remove를 없앤 상태에서,
가짜 resolve / fusion / bmd / os를 넣어 준다.

- 가짜 fusion:SavePrefs()는 Fusion.prefs와 비슷한 글(Lua 표)로 파일을 쓰고,
  시험은 AI 도우미 창처럼 그 파일에서 답을 찾아 읽는다.
- 가짜 bmd.wait()는 코루틴을 잠깐 멈춘다. 스크립트 메뉴를 누를 때마다 코루틴 하나가 생기고,
  시험이 한 바퀴씩 돌려 준다 (두 번 누른 경우도 흉내 낼 수 있다).
- 요청 파일은 정해진 형식(return {v=1,id=..,t=..,op="..",a={..}}, 글자는 16진수)으로
  여기서 직접 만든다 (engine.resolve_link.protocol과 따로 확인하기 위해).

사용자 PC에는 lupa가 없으므로 이 시험은 건너뛴다.
"""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path

import pytest

pytest.importorskip("lupa")
lupa_jit = pytest.importorskip("lupa.luajit21")

SCRIPT = Path(__file__).resolve().parent.parent / "resolve_scripts" / "AI_Helper_Connect.lua"
TRACK_NAME = "AI 도우미 시험"
NOW = 1_759_000_000

# 리졸브 스크립트 메뉴에서 없는 것들
SANDBOX = """
io = nil
require = nil
package = nil
debug = nil
ffi = nil
python = nil
os = nil
"""

# 가짜 리졸브. make(save_cb) → S (시험에서 상태를 보고 바꾸는 표)
FAKE_LUA = r'''
return function(save_cb)
  local S = {
    prefs = {}, log = {}, now = 1759000000, clock = 0, saves = 0, setprefs = 0, waits = 0, pm_calls = 0,
    product = "DaVinci Resolve", version = "21.1.0.0",
    project_open = true, timeline_open = true, append_rejects_media_type = false, time_stuck = false,
    -- append_answer: nil = 넣은 클립 목록, "true" = 넣고 true만, "empty" = 넣고 빈 표 (판마다 다른 답 흉내)
    append_answer = nil,
    -- media_frames(path): 가져온 파일의 길이(타임라인 프레임). 없으면 90
    media_frames = nil,
    -- 리졸브가 파일 경로를 다르게(긴 이름으로) 알려 주는 것 흉내: rewrite_from으로 시작하면 rewrite_to로
    rewrite_from = nil, rewrite_to = nil,
    -- 이미 저장소에 있는 파일을 다시 가져오면 빈 목록을 주는 판 흉내
    import_empty_if_present = false,
    profile = "C:\\Users\\문성\\AppData\\Roaming\\Blackmagic Design\\DaVinci Resolve\\Support\\Fusion\\Profiles\\Default\\",
  }
  local function log(name, ...)
    S.log[#S.log + 1] = { name = name, n = select("#", ...), args = { ... } }
  end

  local function new_mpi(name, path)
    local frames = S.media_frames and S.media_frames(path) or 90
    local m = { name = name, props = { ["File Path"] = path, FPS = "29.97", Frames = tostring(frames) } }
    function m:GetName() return self.name end
    function m:GetClipProperty(k)
      if k == nil then return self.props end
      return self.props[k]
    end
    return m
  end

  local function new_item(name, start, len, mpi)
    local it = { name = name, start = start, len = len, mpi = mpi, left = 12, src = 34 }
    function it:GetName() return self.name end
    function it:GetStart() return self.start end
    function it:GetEnd() return self.start + self.len end
    function it:GetDuration() return self.len end
    function it:GetLeftOffset() return self.left end
    function it:GetSourceStartFrame() return self.src end
    function it:GetMediaPoolItem() return self.mpi end
    function it:GetTrackTypeAndIndex() return self.where end
    return it
  end

  local tl = {
    name = "타임라인 1", start = 86400, len = 9000, start_tc = "01:00:00:00", current_tc = "01:00:10:00",
    settings = { timelineFrameRate = "29.97", timelineDropFrameTimecode = "0" },
    tracks = { video = {}, audio = {}, subtitle = {} }, markers = {},
  }
  S.timeline = tl
  local TRACK_WORD = { video = "Video ", audio = "Audio ", subtitle = "Subtitle " }
  local function ensure_track(kind, index)
    local list = tl.tracks[kind]
    while #list < index do
      list[#list + 1] = { name = TRACK_WORD[kind] .. (#list + 1), items = {} }
    end
    return list[index]
  end
  function S.add_clip(kind, track, name, start, len, path)
    local t = ensure_track(kind, track)
    local it = new_item(name, start, len, path and new_mpi(name, path) or nil)
    t.items[#t.items + 1] = it
    return it
  end
  function S.set_track_name(kind, track, name) ensure_track(kind, track).name = name end
  function S.track_names(kind)
    local out = {}
    for i, t in ipairs(tl.tracks[kind]) do out[i] = t.name end
    return out
  end

  function tl:GetName() return self.name end
  function tl:GetStartFrame() return self.start end
  function tl:GetEndFrame() return self.start + self.len - 1 end
  function tl:GetStartTimecode() return self.start_tc end
  function tl:GetCurrentTimecode() return self.current_tc end
  function tl:GetSetting(k) return self.settings[k] end
  function tl:GetTrackCount(kind) return #self.tracks[kind] end
  function tl:GetItemListInTrack(kind, i)
    local out = {}
    local t = self.tracks[kind][i]
    if t then for j, it in ipairs(t.items) do out[j] = it end end
    return out
  end
  function tl:GetTrackName(kind, i)
    local t = self.tracks[kind][i]
    return t and t.name or ""
  end
  function tl:SetTrackName(kind, i, name)
    log("SetTrackName", kind, i, name)
    local t = self.tracks[kind][i]
    if not t then return false end
    t.name = name
    return true
  end
  function tl:AddTrack(kind, sub)
    log("AddTrack", kind, sub)
    local list = self.tracks[kind]
    list[#list + 1] = { name = TRACK_WORD[kind] .. (#list + 1), sub = sub, items = {} }
    return true
  end
  function tl:DeleteTrack(kind, i)
    local t = self.tracks[kind][i]
    log("DeleteTrack", kind, i, t and t.name)
    if not t then return false end
    table.remove(self.tracks[kind], i)
    return true
  end
  function tl:DeleteClips(items)
    local names = {}
    for _, it in ipairs(items) do
      names[#names + 1] = it.name
      for _, kind in ipairs({ "video", "audio" }) do
        for _, t in ipairs(self.tracks[kind]) do
          for j = #t.items, 1, -1 do
            if t.items[j] == it then table.remove(t.items, j) end
          end
        end
      end
    end
    log("DeleteClips", names)
    return true
  end
  function tl:AddMarker(frame, color, name, note, duration, custom)
    log("AddMarker", frame, color, name, note, duration, custom)
    if self.markers[frame] then return false end
    self.markers[frame] = { color = color, name = name, note = note, duration = duration, customData = custom }
    return true
  end
  function tl:GetMarkers()
    local out = {}
    for f, m in pairs(self.markers) do
      out[f] = { color = m.color, name = m.name, note = m.note, duration = m.duration, customData = m.customData }
    end
    return out
  end
  -- 리졸브 설명서대로 맞는 표시 가운데 첫 하나(가장 앞 프레임)만 지운다
  function tl:DeleteMarkerByCustomData(c)
    log("DeleteMarkerByCustomData", c)
    local first = nil
    for f, m in pairs(self.markers) do
      if m.customData == c and (first == nil or f < first) then first = f end
    end
    if first == nil then return false end
    self.markers[first] = nil
    return true
  end
  function tl:DeleteMarkerAtFrame(f)
    log("DeleteMarkerAtFrame", f)
    if self.markers[f] == nil then return false end
    self.markers[f] = nil
    return true
  end

  local function new_folder(name)
    local f = { name = name, subs = {}, clips = {} }
    function f:GetName() return self.name end
    function f:GetSubFolderList()
      local o = {}
      for i, x in ipairs(self.subs) do o[i] = x end
      return o
    end
    function f:GetClipList()
      local o = {}
      for i, x in ipairs(self.clips) do o[i] = x end
      return o
    end
    return f
  end
  local root = new_folder("Master")
  local pool = { root = root, current = root }
  S.pool = pool
  function pool:GetRootFolder() return self.root end
  function pool:GetCurrentFolder() return self.current end
  function pool:SetCurrentFolder(f)
    log("SetCurrentFolder", f.name)
    self.current = f
    return true
  end
  function pool:AddSubFolder(parent, name)
    log("AddSubFolder", parent.name, name)
    local f = new_folder(name)
    parent.subs[#parent.subs + 1] = f
    return f
  end
  local function reported(p)
    if S.rewrite_from and string.sub(p, 1, #S.rewrite_from) == S.rewrite_from then
      return S.rewrite_to .. string.sub(p, #S.rewrite_from + 1)
    end
    return p
  end
  function pool:ImportMedia(paths)
    log("ImportMedia", paths[1], #paths, self.current.name)
    local out = {}
    for i, p in ipairs(paths) do
      local name = string.match(p, "[^\\/]+$")
      if S.import_empty_if_present then
        for _, c in ipairs(self.current.clips) do
          if c.name == name then return {} end
        end
      end
      local m = new_mpi(name, reported(p))
      self.current.clips[#self.current.clips + 1] = m
      out[i] = m
    end
    return out
  end
  function pool:AppendToTimeline(infos)
    local info = infos[1]
    local keys = {}
    for k in pairs(info) do keys[#keys + 1] = k end
    table.sort(keys)
    log("AppendToTimeline", {
      count = #infos, keys = table.concat(keys, ","), item = info.mediaPoolItem and info.mediaPoolItem.name,
      startFrame = info.startFrame, endFrame = info.endFrame, mediaType = info.mediaType,
      trackIndex = info.trackIndex, recordFrame = info.recordFrame,
    })
    if S.append_rejects_media_type and info.mediaType ~= nil then return {} end
    local track = tl.tracks.audio[info.trackIndex]
    if not track then return {} end
    local mpi = info.mediaPoolItem
    -- 파일보다 긴 범위(endFrame이 파일 길이 이상)는 넣지 않는다
    if info.endFrame >= tonumber(mpi.props.Frames) then return {} end
    local it = new_item(mpi.name, info.recordFrame, info.endFrame - info.startFrame + 1, mpi)
    it.where = { "audio", info.trackIndex }
    track.items[#track.items + 1] = it
    if S.append_answer == "true" then return true end
    if S.append_answer == "empty" then return {} end
    return { it }
  end

  local project = { name = "시험 프로젝트", settings = { timelineFrameRate = "29.97", timelineDropFrameTimecode = "0" } }
  S.project = project
  function project:GetName() return self.name end
  function project:GetCurrentTimeline()
    if S.timeline_open then return tl end
    return nil
  end
  function project:GetMediaPool() return pool end
  function project:GetSetting(k) return self.settings[k] end
  function project:GetSettings()
    local o = {}
    for k, v in pairs(self.settings) do o[k] = v end
    return o
  end
  local pm = {}
  function pm:GetCurrentProject()
    if S.project_open then return project end
    return nil
  end

  local resolve, fusion = {}, {}
  function resolve:GetProjectManager() S.pm_calls = S.pm_calls + 1; return pm end
  function resolve:GetProductName() return S.product end
  function resolve:GetVersionString() return S.version end
  function resolve:Fusion() return fusion end
  function fusion:GetPrefs(k) return S.prefs[k] end
  function fusion:SetPrefs(k, v)
    S.setprefs = S.setprefs + 1
    S.prefs[k] = v
    return true
  end
  function fusion:SavePrefs()
    S.saves = S.saves + 1
    save_cb(S.prefs)
    return true
  end
  function fusion:MapPath(p)
    if p == "Profile:" then return S.profile end
    return p
  end

  local yield = coroutine.yield
  local bmd = {}
  function bmd.wait(sec)
    S.waits = S.waits + 1
    S.last_wait = sec
    yield()
  end
  function bmd.scriptapp(name)
    if name == "Resolve" then return resolve end
  end

  local fake_os = {}
  -- bmd.wait 한 번에 0.1초씩 시간이 간다 (time_stuck이면 멈춰 있음)
  function fake_os.time()
    if S.time_stuck then return S.now end
    return math.floor(S.now + S.waits * 0.1)
  end
  function fake_os.clock() S.clock = S.clock + 0.0007; return S.clock end
  function fake_os.getenv(k) return nil end
  S.resolve, S.fusion, S.bmd, S.os = resolve, fusion, bmd, fake_os

  -- 스크립트 메뉴를 한 번 누를 때마다 코루틴 하나
  local threads = {}
  S.threads = threads
  local loadstring, create, resume, status = loadstring, coroutine.create, coroutine.resume, coroutine.status
  function S.click(src)
    local f = assert(loadstring(src, "=AI_Helper_Connect"))
    threads[#threads + 1] = { co = create(f), done = false }
    return #threads
  end
  function S.step()
    local alive = 0
    for i = 1, #threads do
      local th = threads[i]
      if not th.done then
        local ok, err = resume(th.co)
        if not ok then
          th.done = true
          th.err = tostring(err)
        elseif status(th.co) == "dead" then
          th.done = true
        end
        if not th.done then alive = alive + 1 end
      end
    end
    return alive
  end
  return S
end
'''


# ---------------------------------------------------------------------------
# 요청 파일 만들기 (명세의 형식 그대로)
# ---------------------------------------------------------------------------

def lua_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return repr(v)
    if isinstance(v, float):
        assert math.isfinite(v)
        return format(v, ".17g")
    if isinstance(v, str):
        return '"' + v.encode("utf-8").hex() + '"'
    if isinstance(v, dict):
        return "{" + ",".join(f"{k}={lua_value(x)}" for k, x in v.items()) + "}"
    if isinstance(v, (list, tuple)):
        return "{" + ",".join(lua_value(x) for x in v) + "}"
    raise TypeError(type(v))


def request_text(rid: int, op: str, args=None, t: int = NOW) -> str:
    return f'return {{v=1,id={rid},t={t},op="{op}",a={lua_value(args or {})}}}'


# ---------------------------------------------------------------------------
# Fusion.prefs 흉내와 답 읽기 (AI 도우미 창이 하는 방식)
# ---------------------------------------------------------------------------

def _lua_literal(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    text = str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{text}"'


def prefs_text(flat: dict) -> str:
    tree: dict = {"Global": {"Paths": {"Map": {"Profile:": "UserData:Profiles\\Default\\"}}}}
    for key, value in flat.items():
        node = tree
        parts = key.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    def write(node: dict, depth: int) -> list:
        lines = []
        pad = "\t" * depth
        for k in sorted(node):
            name = k if re.fullmatch(r"[A-Za-z_]\w*", k) else f'["{k}"]'
            v = node[k]
            if isinstance(v, dict):
                lines.append(f"{pad}{name} = {{")
                lines += write(v, depth + 1)
                lines.append(f"{pad}}},")
            else:
                lines.append(f"{pad}{name} = {_lua_literal(v)},")
        return lines

    return "\n".join(["{", "\tLocked = false,", *write(tree, 1), "}", ""])


RESPONSE_RE = re.compile(r"AIH1:([0-9A-Za-z_]+):(\d+):([0-9a-f]*)")


def parse_responses(text: str) -> list:
    out = []
    for owner, rid, hexed in RESPONSE_RE.findall(text):
        out.append((owner, int(rid), json.loads(bytes.fromhex(hexed).decode("utf-8"))))
    return out


def lua_list(t) -> list:
    return [t[i] for i in range(1, len(t) + 1)]


class Harness:
    """샌드박스 LuaJIT + 가짜 리졸브 + 우편함 폴더."""

    def __init__(self, tmp_path: Path, mailbox=None, template: str | None = None, version: str = "test-1",
                 mailbox_long: str = ""):
        self.lua = lupa_jit.LuaRuntime(unpack_returned_tuples=True)
        if mailbox is None:
            mailbox = tmp_path / "bridge"
        if isinstance(mailbox, Path):
            (mailbox / "files").mkdir(parents=True, exist_ok=True)
        self.mailbox = mailbox
        self.files = f"{mailbox}{os.sep if isinstance(mailbox, Path) else chr(92)}files"
        self.prefs_path = tmp_path / "Fusion.prefs"
        self.saved: list = []
        self.fake = self.lua.execute(FAKE_LUA)(self._save_prefs)
        self.lua.execute(SANDBOX)
        g = self.lua.globals()
        for name in ("resolve", "fusion", "bmd", "os"):
            g[name] = self.fake[name]
        text = template if template is not None else SCRIPT.read_text(encoding="utf-8")
        self.source = (text.replace("@@MAILBOX_HEX@@", str(mailbox).encode("utf-8").hex())
                       .replace("@@MAILBOX_LONG_HEX@@", mailbox_long.encode("utf-8").hex())
                       .replace("@@SCRIPT_VERSION@@", version))
        self._handle_json = self.lua.eval("function(h, r) return (h.json((h.handle(r)))) end")
        self.aih = None
        self.next_id = 1000

    # --- 가짜 fusion:SavePrefs() ---
    def _save_prefs(self, prefs) -> None:
        self.prefs_path.write_text(prefs_text(dict(prefs.items())), encoding="utf-8")
        self.saved += parse_responses(self.prefs_path.read_text(encoding="utf-8"))

    # --- 요청 ---
    def write_request(self, rid: int, op: str, args=None, t: int | None = None) -> int:
        self.write_raw(request_text(rid, op, args, NOW if t is None else t))
        return rid

    def write_raw(self, text: str) -> None:
        req = Path(self.mailbox) / "request.lua"
        tmp = req.with_name("request.lua.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, req)

    # --- 함수 단위 시험: main 없이 불러서 init만 ---
    def load(self):
        g = self.lua.globals()
        g.AIH_TEST_NO_MAIN = True
        self.lua.execute(self.source)
        g.AIH_TEST_NO_MAIN = None
        self.aih = g.AIH_EXPORT
        g.AIH_EXPORT = None
        return self.aih

    def init(self):
        aih = self.load()
        assert aih.init() is True
        return aih

    def op(self, op: str, args=None) -> dict:
        self.next_id += 1
        self.write_request(self.next_id, op, args)
        r = self.aih.read_request()
        assert r is not None
        return json.loads(self._handle_json(self.aih, r))

    def op_direct(self, op: str, args=None) -> dict:
        """요청 파일 없이 같은 표를 바로 처리 (윈도우 모양 우편함처럼 이 PC에서 열 수 없는 경로일 때)."""
        self.next_id += 1
        r = self.lua.execute(request_text(self.next_id, op, args))
        return json.loads(self._handle_json(self.aih, r))

    # --- 반복(main) 시험 ---
    def click(self) -> int:
        return self.fake.click(self.source)

    def step(self, rounds: int = 1) -> int:
        alive = 0
        for _ in range(rounds):
            alive = self.fake.step()
        return alive

    def run_until(self, cond, max_rounds: int = 200) -> int:
        for n in range(max_rounds):
            self.fake.step()
            if cond():
                return n + 1
        raise AssertionError("정해진 횟수 안에 끝나지 않음")

    def thread(self, index: int):
        return self.fake.threads[index]

    def answers(self, rid: int) -> list:
        return [(owner, payload) for owner, i, payload in self.saved if i == rid]

    def logged(self, name: str) -> list:
        out = []
        log = self.fake.log
        for i in range(1, len(log) + 1):
            e = log[i]
            if e.name == name:
                out.append([e.args[j] for j in range(1, e.n + 1)])
        return out


@pytest.fixture
def h(tmp_path) -> Harness:
    return Harness(tmp_path)


# ---------------------------------------------------------------------------
# 스크립트 파일 자체
# ---------------------------------------------------------------------------

def _code_only(text: str) -> str:
    text = re.sub(r"--\[\[.*?\]\]", "", text, flags=re.S)
    return re.sub(r"--[^\n]*", "", text)


def test_template_placeholders_and_forbidden_calls():
    raw = SCRIPT.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")  # BOM이 있으면 LuaJIT가 읽지 못한다
    text = raw.decode("utf-8")
    assert text.count("@@MAILBOX_HEX@@") == 1
    assert text.count("@@MAILBOX_LONG_HEX@@") == 1
    assert text.count("@@SCRIPT_VERSION@@") == 1
    code = _code_only(text)
    # 있는지 살펴보기만 하고(환경 조사) 부르지는 않는다
    for bad in (r"\bdofile\s*\(", r"\brequire\s*\(", r"\bio\.", r"\bgoto\b", r"\bos\.execute\s*\(",
                r"\bos\.remove\s*\(", r"\bffi\.", r"Execute\s*\(", r"bmd\.(readfile|writefile|readdir)",
                r"%q", r"UIManager"):
        assert not re.search(bad, code), bad
    assert "setfenv(f, {})" in code


def test_sandbox_really_blocks(h):
    lua = h.lua
    assert lua.eval("io == nil and require == nil and package == nil and debug == nil and ffi == nil")
    assert lua.eval("os.execute == nil and os.remove == nil")


# ---------------------------------------------------------------------------
# 16진수, JSON, 경로 검사
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("data", [
    b"", b"a", "한글 파일.wav".encode(), b'"}) resolve:GetProjectManager()', b"]]", b"]=]", b"\\",
    b"\r\n", b"\x00", b"\xff\xfe\x80", bytes(range(256)),
])
def test_hex_round_trip(h, data):
    aih = h.load()
    assert aih.hex(data) == data.hex()
    round_trip = h.lua.eval("function(h, x) return h.hex(h.unhex(x)) end")
    assert round_trip(aih, data.hex()) == data.hex()
    assert round_trip(aih, data.hex().upper()) == data.hex()  # 대문자 16진수도 받는다


@pytest.mark.parametrize("bad", ["abc", "zz", "0g", "12 3", None])
def test_unhex_rejects(h, bad):
    assert h.load().unhex(bad) is None


JSON_CASES = [
    ("{a={b={c={1,2,{d='e'}}}}}", {"a": {"b": {"c": [1, 2, {"d": "e"}]}}}),
    ("{1,2,3}", [1, 2, 3]),
    ("{'x','y'}", ["x", "y"]),
    ("{}", {}),
    ("A.array({})", []),
    ("{list=A.array({}), obj={}}", {"list": [], "obj": {}}),
    ("'\\0\\1\\31\\t\\n\\r\"\\\\/'", "\x00\x01\x1f\t\n\r\"\\/"),
    ("'한글 😀'", "한글 😀"),
    ("'\\255\\254ab\\237\\160\\128'", "\u00ff\u00feab\u00ed\u00a0\u0080"),  # UTF-8이 아닌 바이트
    ("{0/0, 1/0, -1/0}", [None, None, None]),
    ("{1759000000123, 2^53 - 1, -5, 0.1, 1e300, -0.0, 1.5}", [1759000000123, 2**53 - 1, -5, 0.1, 1e300, 0, 1.5]),
    ("{[1]=1, [3]=3}", {"1": 1, "3": 3}),
    ("{1, x=2}", {"1": 1, "x": 2}),
    ("{a=A.NULL, b=true, c=false}", {"a": None, "b": True, "c": False}),
    ("{f=print, n=nil, [2.5]='x'}", {"f": None, "2.5": "x"}),
]


@pytest.mark.parametrize("expr,expected", JSON_CASES)
def test_json_parses_with_python(h, expr, expected):
    aih = h.load()
    encode = h.lua.eval(f"function(A) return A.json({expr}) end")
    text = encode(aih)
    assert json.loads(text) == expected
    text.encode("utf-8")  # 늘 올바른 UTF-8


def test_json_cycle_does_not_hang(h):
    aih = h.load()
    text = h.lua.eval("function(A) local t = {x=1}; t.self = t; return A.json(t) end")(aih)
    assert json.loads(text) == {"x": 1, "self": None}


def test_path_guard_posix_mailbox(h):
    aih = h.init()
    files = Path(h.mailbox) / "files"
    assert aih.path_ok(str(files / "aih_test_tone.wav")) is True
    assert aih.path_ok(str(files / "AIH.WAV")) is True
    for bad in [
        str(Path(h.mailbox).parent / "other" / "a.wav"),
        str(files / ".." / "a.wav"),
        str(files / "sub" / ".." / ".." / "a.wav"),
        str(files / "a.mp3"),
        str(files / "a.wav.exe"),
        str(files) + "2/a.wav",
        str(files) + "/",
        str(files / "a.wav") + "\x00.wav",
        "",
    ]:
        assert aih.path_ok(bad) is not True, bad


def test_path_guard_windows_mailbox(tmp_path):
    mailbox = r"C:\Users\x\AppData\Local\video-editing-systems\bridge"
    h = Harness(tmp_path, mailbox=mailbox)
    aih = h.init()
    assert aih.path_ok(mailbox + r"\files\aih_test_tone.wav") is True
    # 대소문자와 / 섞인 경로도 같은 곳으로 본다
    assert aih.path_ok("c:/users/X/APPDATA/local/video-editing-systems/bridge/files/T.WAV") is True
    for bad in [
        r"C:\Users\x\Music\a.wav",
        mailbox + r"\files2\a.wav",
        mailbox + r"\filesevil.wav",
        mailbox + r"\files\..\..\secret.wav",
        mailbox + r"\files\a.mp3",
        mailbox + r"\files\a.wav:hidden.wav",
        r"D:" + mailbox[2:] + r"\files\a.wav",
    ]:
        assert aih.path_ok(bad) is not True, bad


# ---------------------------------------------------------------------------
# 할 일 하나씩 (main 없이)
# ---------------------------------------------------------------------------

def test_ping(h):
    h.init()
    res = h.op("ping")
    assert res["ok"] is True and res["sv"] == "test-1"
    r = res["result"]
    assert r["script_version"] == "test-1"
    assert r["product"] == "DaVinci Resolve" and r["resolve_version"] == "21.1.0.0"
    assert r["profile_path"].startswith("C:\\Users\\문성\\AppData")
    assert re.fullmatch(r"o\d+_\d+", r["owner"])
    assert r["owner_readback"] is True
    assert r["mailbox"] == str(h.mailbox)
    assert r["env"] == {
        "loadfile": True, "setfenv": True, "os_getenv": True, "os_time": True, "bmd_wait": True,
        "io": False, "require": False, "ffi": False, "os_execute": False, "dofile": True,
    }
    assert set(r["calls"].values()) == {"ok"}
    assert r["lua_version"] == "Lua 5.1"


def _sample_timeline(h):
    h.fake.add_clip("video", 1, "첫 영상.mp4", 86400, 900, "C:\\Users\\문성\\Videos\\첫 영상.mp4")
    h.fake.add_clip("video", 2, "자막 배경", 86500, 100, None)
    h.fake.add_clip("audio", 1, "첫 영상.mp4", 86400, 900, "C:\\Users\\문성\\Videos\\첫 영상.mp4")
    h.fake.add_clip("subtitle", 1, "안녕", 86400, 30, None)
    h.fake.add_clip("subtitle", 1, "하세요", 86430, 30, None)


def test_state(h):
    _sample_timeline(h)
    h.init()
    res = h.op("state")
    assert res["ok"] is True
    r = res["result"]
    assert r["project"] == "시험 프로젝트" and r["timeline"] == "타임라인 1"
    assert (r["start_frame"], r["end_frame"]) == (86400, 95399)
    assert (r["start_tc"], r["current_tc"]) == ("01:00:00:00", "01:00:10:00")
    assert (r["fps"], r["fps_source"]) == ("29.97", "timeline_get_setting")
    assert r["drop_frame"] is False and r["drop_frame_raw"] == "0"
    assert r["tracks"] == {"video": 2, "audio": 1, "subtitle": 1}
    assert r["subtitle_items"] == 2 and r["truncated"] is False
    first = r["items"]["video"][0]
    assert first == {
        "track": 1, "name": "첫 영상.mp4", "start": 86400, "end": 87300, "duration": 900,
        "left_offset": 12, "source_start": 34, "clip": "첫 영상.mp4", "path": "C:\\Users\\문성\\Videos\\첫 영상.mp4",
    }
    assert r["items"]["video"][1]["track"] == 2 and "path" not in r["items"]["video"][1]
    assert len(r["items"]["audio"]) == 1
    assert set(r["calls"].values()) == {"ok"}


def test_state_without_project_or_timeline(h):
    h.init()
    h.fake.timeline_open = False
    r = h.op("state")["result"]
    assert r["project"] == "시험 프로젝트" and r["timeline"] is None
    h.fake.project_open = False
    r = h.op("state")["result"]
    assert r["project"] is None and r["timeline"] is None


def test_state_records_missing_and_failing_calls(h):
    h.init()
    tl = h.fake.timeline
    tl.GetCurrentTimecode = None  # 없는 함수
    tl.GetSetting = None
    h.lua.execute("local p = ...; p.GetSetting = function() error('bad setting name') end", h.fake.project)
    r = h.op("state")["result"]
    calls = r["calls"]
    assert calls["Timeline.GetCurrentTimecode"] == "missing"
    assert calls["Timeline.GetSetting"] == "missing"
    assert calls["Timeline.GetSettings"] == "missing"
    assert calls["Project.GetSetting"].startswith("err:") and "bad setting name" in calls["Project.GetSetting"]
    assert calls["Project.GetSettings"] == "ok"
    assert r["current_tc"] is None and r["start_tc"] == "01:00:00:00"
    assert (r["fps"], r["fps_source"]) == ("29.97", "project_get_settings")
    assert r["drop_frame"] is False


def test_state_caps_items(h):
    h.lua.execute(
        "local S = ...; for i = 1, 350 do S.add_clip('video', 1 + (i % 3), 'c' .. i, 86400 + i, 1, nil) end",
        h.fake,
    )
    h.init()
    r = h.op("state")["result"]
    assert len(r["items"]["video"]) == 300 and r["truncated"] is True
    assert r["items"]["audio"] == []


def test_too_large_answer(h):
    h.lua.execute(
        "local S = ...; for i = 1, 600 do S.add_clip(i % 2 == 0 and 'video' or 'audio', 1, string.rep('x', 1000), i, 1, nil) end",
        h.fake,
    )
    aih = h.init()
    h.write_request(5, "state")
    value = h.lua.eval("function(A) local r = A.read_request(); return A.response_value(r.id, (A.handle(r))) end")(aih)
    assert len(value) < 1000
    ((owner, rid, payload),) = parse_responses(value)
    assert rid == 5 and payload["ok"] is False and payload["error"] == "too_large"
    assert payload["size"] > 400000


HOSTILE_NAME = '"}) resolve:GetProjectManager() --'
HOSTILE_NOTE = "]] ]=] \\ \n\r\t 한글 메모 \x00 끝"


def test_add_marker_hostile_strings_arrive_exact(h):
    h.init()
    res = h.op("add_marker", {"frame": 300, "color": "Yellow", "name": HOSTILE_NAME, "note": HOSTILE_NOTE,
                              "custom": "aih_test", "duration": 1})
    assert res["ok"] is True
    r = res["result"]
    assert r["added"] is True and r["frame"] == 300
    ((frame, color, name, note, duration, custom),) = h.logged("AddMarker")
    assert (frame, color, name, note, duration, custom) == (300, "Yellow", HOSTILE_NAME, HOSTILE_NOTE, 1, "aih_test")
    assert h.fake.pm_calls == 1  # 이름 속 글자가 코드로 실행되지 않았다
    # 돌아온 표시 목록에도 그대로
    markers = h.op("get_markers")["result"]["markers"]
    assert markers == [{"frame": 300, "color": "Yellow", "name": HOSTILE_NAME, "note": HOSTILE_NOTE,
                        "duration": 1, "custom": "aih_test"}]


def test_add_marker_moves_past_existing_markers(h):
    h.init()
    for f in (300, 301):
        h.op("add_marker", {"frame": f, "name": "사용자 표시", "custom": "user"})
    r = h.op("add_marker", {"frame": 300, "name": "AI 도우미 시험", "custom": "aih_test"})["result"]
    assert r["added"] is True and r["frame"] == 302 and r["tried"] == [300, 301, 302]
    assert r["requested_frame"] == 300


def test_add_marker_bad_args(h):
    h.init()
    res = h.op("add_marker", {"name": "x"})
    assert res == {"ok": False, "sv": "test-1", "error": "bad_args", "func": "frame", "calls": {}}
    res = h.op("add_marker", {"frame": 1, "duration": 0})
    assert res["error"] == "bad_args" and res["func"] == "duration"
    assert h.logged("AddMarker") == []


def test_add_marker_without_timeline(h):
    h.init()
    h.fake.timeline_open = False
    res = h.op("add_marker", {"frame": 1})
    assert res["ok"] is False and res["error"] == "no_timeline" and res["func"] == "Project.GetCurrentTimeline"


def test_get_and_delete_markers(h):
    h.init()
    assert h.op("get_markers")["result"]["markers"] == []
    h.op("add_marker", {"frame": 90, "color": "Blue", "name": "사용자", "custom": "user"})
    h.op("add_marker", {"frame": 30, "name": "시험", "custom": "aih_test", "duration": 5})
    markers = h.op("get_markers")["result"]["markers"]
    assert [m["frame"] for m in markers] == [30, 90]
    assert markers[0] == {"frame": 30, "color": "Yellow", "name": "시험", "note": "", "duration": 5, "custom": "aih_test"}
    r = h.op("delete_markers", {"custom": "aih_test"})["result"]
    assert (r["deleted"], r["deleted_count"], r["remaining"]) == (True, 1, 0)
    assert r["calls"] == {
        "Resolve.GetProjectManager": "ok", "ProjectManager.GetCurrentProject": "ok",
        "Project.GetCurrentTimeline": "ok", "Timeline.GetMarkers": "ok", "Timeline.GetMarkers.after": "ok",
        # 두 번째 부름이 false(더 없음)라서 되풀이가 끝났다
        "Timeline.DeleteMarkerByCustomData": "ok:false",
    }
    assert [m["custom"] for m in h.op("get_markers")["result"]["markers"]] == ["user"]
    r = h.op("delete_markers", {"custom": "aih_test"})["result"]
    assert (r["deleted"], r["deleted_count"], r["remaining"]) == (False, 0, 0)
    assert h.op("delete_markers", {"custom": ""})["error"] == "bad_args"


def test_delete_markers_removes_every_test_marker(h):
    """DeleteMarkerByCustomData는 첫 표시 하나만 지운다: ②를 여러 번 눌러 생긴 표시도 모두 지워야 한다."""
    h.init()
    for f in (30, 30, 30, 500):  # 같은 자리에서 여러 번 누르면 31, 32로 밀려 들어간다
        h.op("add_marker", {"frame": f, "name": "AI 도우미 시험", "custom": "aih_test"})
    h.op("add_marker", {"frame": 40, "name": "사용자", "custom": "user"})
    assert len(h.op("get_markers")["result"]["markers"]) == 5
    r = h.op("delete_markers", {"custom": "aih_test"})["result"]
    assert (r["deleted"], r["deleted_count"], r["remaining"]) == (True, 4, 0)
    assert len(h.logged("DeleteMarkerByCustomData")) == 5  # 4번 지우고 다섯 번째가 false
    assert h.logged("DeleteMarkerAtFrame") == []
    assert [m["custom"] for m in h.op("get_markers")["result"]["markers"]] == ["user"]


def test_delete_markers_without_delete_by_custom_data(h):
    """DeleteMarkerByCustomData가 없는 판: 프레임 위치로 하나씩 지운다."""
    h.init()
    for f in (10, 20):
        h.op("add_marker", {"frame": f, "custom": "aih_test"})
    h.fake.timeline.DeleteMarkerByCustomData = None
    r = h.op("delete_markers", {"custom": "aih_test"})["result"]
    assert (r["deleted_count"], r["remaining"]) == (2, 0)
    assert r["calls"]["Timeline.DeleteMarkerByCustomData"] == "missing"
    assert [x[0] for x in h.logged("DeleteMarkerAtFrame")] == [10, 20]
    assert h.op("get_markers")["result"]["markers"] == []


def test_delete_markers_stops_when_answer_is_always_true(h):
    """늘 true만 주는 판이어도 끝없이 돌지 않고, 지운 수는 다시 읽은 목록으로 센다."""
    h.init()
    h.op("add_marker", {"frame": 10, "custom": "aih_test"})
    h.lua.execute("local tl = ...; local real = tl.DeleteMarkerByCustomData; "
                  "tl.DeleteMarkerByCustomData = function(self, c) real(self, c); return true end", h.fake.timeline)
    r = h.op("delete_markers", {"custom": "aih_test"})["result"]
    assert (r["deleted_count"], r["remaining"]) == (1, 0)
    assert len(h.logged("DeleteMarkerByCustomData")) == 200


@pytest.mark.parametrize("make_path", [
    lambda files: str(Path(files).parent.parent / "outside.wav"),
    lambda files: str(Path(files) / ".." / "escape.wav"),
    lambda files: str(Path(files) / "tone.mp3"),
    lambda files: "/etc/passwd",
])
def test_place_audio_rejects_bad_paths(h, make_path):
    h.init()
    res = h.op("place_audio", {"path": make_path(h.files), "track_name": TRACK_NAME, "record_frame": 86700,
                               "frames": 90})
    assert res["ok"] is False and res["error"].startswith("bad_path") and res["func"] == "path_guard"
    for name in ("ImportMedia", "AddTrack", "AppendToTimeline", "AddSubFolder"):
        assert h.logged(name) == []


def test_place_audio(h):
    h.fake.add_clip("audio", 1, "첫 영상.mp4", 86400, 900, "C:\\Users\\문성\\Videos\\첫 영상.mp4")
    h.init()
    path = str(Path(h.files) / "aih_test_tone.wav")
    res = h.op("place_audio", {"path": path, "track_name": TRACK_NAME, "record_frame": 86700, "frames": 90})
    assert res["ok"] is True, res
    r = res["result"]
    assert r["imported"] is True and r["track_index"] == 2 and r["appended"] == 1
    assert (r["item_start"], r["item_end"]) == (86700, 86790)
    assert r["item_track"] == ["audio", 2]
    assert r["track_named"] is True and r["found_by"] is None
    assert (r["append_raw_type"], r["append_retry"]) == ("table", "not_needed")
    # 리졸브가 알려 주는 파일 경로와 길이 단위를 결과에 남긴다
    assert r["clip"] == {"name": "aih_test_tone.wav", "path": path, "frames": "90", "fps": "29.97", "duration": None}
    assert h.logged("AddSubFolder") == [["Master", "AI 도우미"]]
    assert h.logged("ImportMedia") == [[path, 1, "AI 도우미"]]
    assert h.logged("AddTrack") == [["audio", "stereo"]]
    assert h.logged("SetTrackName") == [["audio", 2, TRACK_NAME]]
    ((info,),) = h.logged("AppendToTimeline")
    assert dict(info.items()) == {
        "count": 1, "keys": "endFrame,mediaPoolItem,mediaType,recordFrame,startFrame,trackIndex",
        "item": "aih_test_tone.wav", "startFrame": 0, "endFrame": 89, "mediaType": 2, "trackIndex": 2,
        "recordFrame": 86700,
    }
    # 사용자가 보던 미디어 풀 폴더로 되돌렸다
    assert h.fake.pool.current.name == "Master"
    assert [x[0] for x in h.logged("SetCurrentFolder")] == ["AI 도우미", "Master"]
    assert list(lua_list(h.fake.track_names("audio"))) == ["Audio 1", TRACK_NAME]
    assert r["calls"]["MediaPool.AppendToTimeline"] == "ok"
    assert "MediaPool.AppendToTimeline.no_media_type" not in r["calls"]


def test_place_audio_retries_without_media_type_and_reuses_clip(h):
    h.init()
    h.fake.append_rejects_media_type = True
    path = str(Path(h.files) / "aih_test_tone.wav")
    args = {"path": path, "track_name": TRACK_NAME, "record_frame": 86400, "frames": 30}
    r = h.op("place_audio", args)["result"]
    assert r["appended"] == 1 and r["imported"] is True
    first, second = h.logged("AppendToTimeline")
    assert first[0]["mediaType"] == 2 and second[0]["mediaType"] is None
    assert "mediaType" not in second[0]["keys"].split(",")
    assert r["calls"]["MediaPool.AppendToTimeline"] == "ok"
    assert r["calls"]["MediaPool.AppendToTimeline.no_media_type"] == "ok"
    # 두 번째: 같은 파일은 다시 가져오지 않고, 저장소도 새로 만들지 않는다
    r2 = h.op("place_audio", args)["result"]
    assert r2["imported"] is False and r2["reused"] is True and r2["track_index"] == 2
    assert r2["found_by"] == "path"
    assert len(h.logged("ImportMedia")) == 1 and len(h.logged("AddSubFolder")) == 1


@pytest.mark.parametrize("answer, raw_type", [("true", "boolean"), ("empty", "table")])
def test_place_audio_does_not_add_twice_when_answer_is_empty(h, answer, raw_type):
    """AppendToTimeline이 넣고도 true나 빈 표를 주는 판: 다시 넣으면 같은 소리가 두 번 겹친다."""
    h.init()
    h.fake.append_answer = answer
    r = h.op("place_audio", {"path": str(Path(h.files) / "aih_test_tone.wav"), "track_name": TRACK_NAME,
                             "record_frame": 86400, "frames": 30})["result"]
    assert len(h.logged("AppendToTimeline")) == 1  # 한 번만
    assert (r["appended"], r["append_raw_type"], r["append_retry"]) == (1, raw_type, "skipped_already_placed")
    assert (r["item_start"], r["item_end"]) == (86400, 86430)
    assert len(h.fake.timeline.tracks.audio[1]["items"]) == 1


def test_place_audio_does_not_retry_when_track_cannot_be_read(h):
    h.init()
    h.fake.append_answer = "true"
    h.fake.timeline.GetItemListInTrack = None
    r = h.op("place_audio", {"path": str(Path(h.files) / "aih_test_tone.wav"), "track_name": TRACK_NAME,
                             "record_frame": 86400, "frames": 30})["result"]
    assert len(h.logged("AppendToTimeline")) == 1
    assert (r["appended"], r["append_retry"]) == (0, "skipped_track_unknown")


def test_place_audio_range_longer_than_file_is_refused(h):
    """파일 길이(프레임)를 넘는 endFrame은 리졸브가 받지 않는다 (가짜도 그렇게 한다)."""
    h.init()
    h.fake.media_frames = lambda path: 89  # 3초 소리를 29.97에서 버림으로 센 길이
    args = {"path": str(Path(h.files) / "aih_test_tone.wav"), "track_name": TRACK_NAME, "record_frame": 86400}
    r = h.op("place_audio", dict(args, frames=90))["result"]
    assert r["appended"] == 0 and r["append_retry"] == "done"
    r = h.op("place_audio", dict(args, frames=89))["result"]
    assert r["appended"] == 1


def test_place_audio_reports_track_name_not_set(h):
    h.init()
    h.lua.execute("local tl = ...; tl.SetTrackName = function() return false end", h.fake.timeline)
    r = h.op("place_audio", {"path": str(Path(h.files) / "aih_test_tone.wav"), "track_name": TRACK_NAME,
                             "record_frame": 86400, "frames": 30})["result"]
    assert r["track_named"] is False and r["appended"] == 1
    assert r["calls"]["Timeline.SetTrackName"] == "ok:false"  # 오류는 아니지만 거절한 것이 보인다


def test_place_audio_finds_existing_clip_by_name_when_import_gives_nothing(h):
    """이미 저장소에 있는 파일을 다시 가져오면 빈 목록을 주는 판: 이름으로 찾아 쓴다."""
    h.init()
    path = str(Path(h.files) / "aih_test_tone.wav")
    args = {"path": path, "track_name": TRACK_NAME, "record_frame": 86400, "frames": 30}
    h.op("place_audio", args)
    h.fake.import_empty_if_present = True
    # 경로 비교가 어긋나는 경우 (리졸브가 다른 모양으로 알려 줌)
    h.lua.execute("local S = ...; S.pool.root.subs[1].clips[1].props['File Path'] = nil", h.fake)
    r = h.op("place_audio", args)["result"]
    assert r["imported"] is False and r["found_by"] == "name" and r["appended"] == 1
    assert len(h.logged("ImportMedia")) == 2

def test_place_audio_import_failure_restores_folder(h):
    h.init()
    h.lua.execute("local S = ...; S.pool.ImportMedia = function() return {} end", h.fake)
    res = h.op("place_audio", {"path": str(Path(h.files) / "a.wav"), "track_name": TRACK_NAME,
                               "record_frame": 0, "frames": 1})
    assert res["ok"] is False and res["error"] == "import_failed" and res["func"] == "MediaPool.ImportMedia"
    assert h.fake.pool.current.name == "Master"
    assert h.logged("AddTrack") == []


def test_remove_audio_only_our_tracks(h):
    ours = str(Path(h.files) / "aih_test_tone.wav")
    user = "C:\\Users\\문성\\Music\\배경음.wav"
    f = h.fake
    f.add_clip("audio", 1, "원본", 0, 10, user)
    f.add_clip("audio", 2, "ours1", 0, 10, ours)
    f.add_clip("audio", 3, "user in our name", 0, 10, user)  # 이름은 같지만 사용자 파일
    f.add_clip("audio", 4, "other", 0, 10, ours)             # 우리 파일이지만 다른 이름 트랙
    f.add_clip("audio", 5, "ours2", 0, 10, ours)
    f.add_clip("audio", 5, "ours3", 20, 10, ours)
    f.set_track_name("audio", 6, TRACK_NAME)                 # 빈 트랙
    for i in (2, 3, 5):
        f.set_track_name("audio", i, TRACK_NAME)
    h.init()
    r = h.op("remove_audio", {"track_name": TRACK_NAME})["result"]
    assert (r["removed_tracks"], r["skipped"]) == (3, 1)
    assert lua_list(f.track_names("audio")) == ["Audio 1", TRACK_NAME, "Audio 4"]
    assert [x[1] for x in h.logged("DeleteTrack")] == [6, 5, 2]
    assert [lua_list(x[0]) for x in h.logged("DeleteClips")] == [["ours2", "ours3"], ["ours1"]]
    remaining = h.op("state")["result"]["items"]["audio"]
    assert [i["name"] for i in remaining] == ["원본", "user in our name", "other"]


def test_remove_audio_keeps_track_when_items_cannot_be_read(h):
    """클립 목록을 못 읽은 트랙은 비어 있는지 알 수 없으므로 지우지 않는다 (사용자 클립이 있을 수 있음)."""
    f = h.fake
    f.add_clip("audio", 1, "사용자 소리", 0, 10, "C:\\Users\\문성\\Music\\a.wav")
    f.set_track_name("audio", 1, TRACK_NAME)
    h.init()
    for broken in ("function() error('track list failed') end", "function() return nil end",
                   "function() return true end"):
        h.lua.execute(f"local tl = ...; tl.GetItemListInTrack = {broken}", f.timeline)
        r = h.op("remove_audio", {"track_name": TRACK_NAME})["result"]
        assert (r["removed_tracks"], r["skipped"]) == (0, 1), broken
    assert h.logged("DeleteTrack") == [] and h.logged("DeleteClips") == []


WIN_SHORT = r"C:\Users\HONGGI~1\AppData\Local\VIDEO-~1\bridge"
WIN_LONG = "C:\\Users\\홍길동\\AppData\\Local\\video-editing-systems\\bridge"


def test_short_name_mailbox_accepts_long_path_from_resolve(tmp_path):
    """한글 사용자 이름: 짧은 이름으로 가져온 파일을 리졸브가 긴 이름으로 알려 줘도 우리 파일로 알아본다."""
    h = Harness(tmp_path, mailbox=WIN_SHORT, mailbox_long=WIN_LONG)
    h.fake.rewrite_from, h.fake.rewrite_to = WIN_SHORT, WIN_LONG
    h.fake.add_clip("audio", 1, "사용자", 0, 10, WIN_LONG + "\\other.wav")  # 우편함 안이지만 files 밖
    h.fake.set_track_name("audio", 1, TRACK_NAME)
    aih = h.init()
    tone = WIN_SHORT + "\\files\\aih_test_tone.wav"
    assert aih.ours(WIN_LONG + "\\files\\aih_test_tone.wav") is True
    assert aih.path_ok(WIN_LONG + "\\files\\aih_test_tone.wav") is not True  # 가져오기는 짧은 경로만
    assert aih.ours(WIN_LONG + "\\other.wav") is not True
    args = {"path": tone, "track_name": TRACK_NAME, "record_frame": 86400, "frames": 30}
    r1 = h.op_direct("place_audio", args)["result"]
    assert r1["imported"] is True and r1["clip"]["path"] == WIN_LONG + "\\files\\aih_test_tone.wav"
    r2 = h.op_direct("place_audio", args)["result"]
    assert r2["reused"] is True and r2["found_by"] == "path"  # 긴 이름으로도 같은 파일
    assert len(h.logged("ImportMedia")) == 1
    r = h.op_direct("remove_audio", {"track_name": TRACK_NAME})["result"]
    assert (r["removed_tracks"], r["skipped"]) == (2, 1)  # 사용자 트랙(1번)은 남긴다
    assert lua_list(h.fake.track_names("audio")) == [TRACK_NAME]
    assert h.op_direct("ping")["result"]["mailbox_long"] == WIN_LONG


def test_long_mailbox_placeholder_left_empty(tmp_path):
    h = Harness(tmp_path, mailbox=WIN_SHORT)  # 긴 경로가 없는 PC (빈 값)
    aih = h.init()
    assert aih.ours(WIN_LONG + "\\files\\a.wav") is not True
    assert h.op_direct("ping")["result"]["mailbox_long"] is None


def test_unknown_op_and_bad_hex(h):
    h.init()
    assert h.op("format_disk")["error"] == "unknown_op"
    h.write_raw('return {v=1,id=7,t=0,op="add_marker",a={frame=1,name="zz"}}')
    res = json.loads(h._handle_json(h.aih, h.aih.read_request()))
    assert (res["error"], res["func"]) == ("bad_args", "hex")
    h.write_raw('return {v=2,id=8,t=0,op="ping",a={}}')
    assert json.loads(h._handle_json(h.aih, h.aih.read_request()))["error"] == "bad_version"
    assert h.logged("AddMarker") == []


def test_request_file_cannot_run_code(h):
    aih = h.init()
    h.write_raw('return {v=1,id=9,t=0,op="ping",a={x=resolve:GetProjectManager()}}')
    assert aih.read_request() is None
    h.write_raw('return {v=1,id=9,t=0,op="ping",a={x=os.time()}}')
    assert aih.read_request() is None
    h.write_raw("return {v=1,id=9,op=")  # 쓰는 중에 읽은 파일
    assert aih.read_request() is None
    assert h.fake.pm_calls == 0


# ---------------------------------------------------------------------------
# 반복(main) 전체: 스크립트 메뉴를 누른 것처럼
# ---------------------------------------------------------------------------

def test_loop_answers_ping_and_stops(h):
    th = h.click()
    assert h.step() == 1
    owner = h.fake.prefs["Global.AIHelper.Owner"]
    assert re.fullmatch(r"o\d+_\d+", owner)
    assert h.fake.saves == 0  # 할 일이 없으면 저장하지 않는다

    h.write_request(1_759_000_000_001, "ping")
    h.run_until(lambda: h.answers(1_759_000_000_001))
    ((who, payload),) = h.answers(1_759_000_000_001)
    assert who == owner and payload["ok"] is True and payload["result"]["owner"] == owner
    text = h.prefs_path.read_text(encoding="utf-8")
    assert "Global = {" in text and "AIHelper = {" in text and 'Response = "AIH1:' in text
    assert h.fake.prefs["Global.AIHelper.Claim"] == "1759000000001"

    # 한가할 때는 설정을 쓰지도 저장하지도 않는다
    saves, setprefs = h.fake.saves, h.fake.setprefs
    h.step(50)
    assert (h.fake.saves, h.fake.setprefs) == (saves, setprefs)
    assert h.fake.last_wait == pytest.approx(0.1)

    h.write_request(1_759_000_000_002, "stop")
    h.run_until(lambda: h.thread(th).done)
    assert h.thread(th).err is None
    assert h.answers(1_759_000_000_002)[0][1]["result"] == {"stopping": True}


def test_loop_marker_with_hostile_strings(h):
    th = h.click()
    h.step()
    h.write_request(20, "add_marker", {"frame": 10, "name": HOSTILE_NAME, "note": HOSTILE_NOTE, "custom": "aih_test"})
    h.run_until(lambda: h.answers(20))
    assert h.answers(20)[0][1]["result"]["added"] is True
    ((_, _, name, note, _, custom),) = h.logged("AddMarker")
    assert (name, note, custom) == (HOSTILE_NAME, HOSTILE_NOTE, "aih_test")
    h.write_request(21, "stop")
    h.run_until(lambda: h.thread(th).done)


def test_stale_request_at_startup_is_not_run(h):
    h.write_request(50, "add_marker", {"frame": 1, "custom": "old"}, t=NOW - 100)
    th = h.click()
    h.step(30)
    assert h.logged("AddMarker") == [] and h.answers(50) == []
    h.write_request(51, "ping")
    h.run_until(lambda: h.answers(51))
    assert h.logged("AddMarker") == []
    h.write_request(52, "stop")
    h.run_until(lambda: h.thread(th).done)


def test_fresh_request_at_startup_is_run(h):
    h.write_request(60, "add_marker", {"frame": 1, "custom": "new"}, t=NOW - 5)
    th = h.click()
    h.step()
    assert len(h.answers(60)) == 1 and len(h.logged("AddMarker")) == 1
    assert h.fake.waits == 1  # 첫 대기 전에 이미 처리
    h.step(30)
    assert len(h.answers(60)) == 1 and len(h.logged("AddMarker")) == 1
    h.write_request(61, "stop")
    h.run_until(lambda: h.thread(th).done)


def test_startup_request_is_not_run_without_os_time(h):
    h.fake.os.time = None
    h.write_request(70, "add_marker", {"frame": 1}, t=0)
    th = h.click()
    h.step(5)
    assert h.answers(70) == []
    h.write_request(71, "ping")
    h.run_until(lambda: h.answers(71))
    r = h.answers(71)[0][1]["result"]
    assert r["env"]["os_time"] is False and r["owner"].startswith("o0_")
    h.write_request(72, "stop")
    h.run_until(lambda: h.thread(th).done)


def test_second_click_takes_over_and_request_is_handled_once(h):
    first = h.click()
    h.step()
    owner1 = h.fake.prefs["Global.AIHelper.Owner"]
    second = h.click()  # 같은 초에 다시 누름
    h.step()
    owner2 = h.fake.prefs["Global.AIHelper.Owner"]
    assert owner1 != owner2

    h.write_request(200, "ping")
    h.run_until(lambda: h.thread(first).done, max_rounds=15)
    assert h.thread(first).err is None and not h.thread(second).done
    assert len(h.answers(200)) == 1

    h.write_request(201, "ping")
    h.run_until(lambda: h.answers(201))
    h.step(5)
    ((who, payload),) = h.answers(201)
    assert who == owner2 and payload["result"]["owner"] == owner2

    h.write_request(202, "stop")
    h.run_until(lambda: h.thread(second).done)
    assert h.thread(second).err is None


def test_claim_blocks_request_already_taken(h):
    h.fake.prefs["Global.AIHelper.Claim"] = "300"  # 다른 반복이 이미 맡은 요청
    th = h.click()
    h.step()
    h.write_request(300, "add_marker", {"frame": 1})
    h.step(20)
    assert h.answers(300) == [] and h.logged("AddMarker") == []
    h.write_request(301, "stop")
    h.run_until(lambda: h.thread(th).done)


def test_claim_from_before_clock_went_back_is_ignored(h):
    """PC 시계가 뒤로 가면 새 요청 번호가 저장된 Claim보다 작다. 10분 넘게 차이 나면 예전 값으로 본다."""
    now_ms = NOW * 1000
    h.fake.prefs["Global.AIHelper.Claim"] = str(now_ms + 9 * 3600 * 1000)  # 9시간 빠른 시계로 저장된 값
    th = h.click()
    h.step()
    h.write_request(now_ms, "ping")
    h.run_until(lambda: h.answers(now_ms))
    assert h.fake.prefs["Global.AIHelper.Claim"] == str(now_ms)
    h.write_request(now_ms + 1, "stop")
    h.run_until(lambda: h.thread(th).done)


def test_loop_invalid_requests(h):
    th = h.click()
    h.step()
    h.write_raw("return {v=1,id=")  # 쓰다 만 파일
    h.step(3)
    h.write_raw('return {v=1,id="x",op="ping"}')  # 번호가 숫자가 아님: 무시
    h.step(3)
    assert h.saved == []
    h.write_raw('return {v=1,id=400,t=0,op="os_execute",a={}}')
    h.run_until(lambda: h.answers(400))
    assert h.answers(400)[0][1]["error"] == "unknown_op"
    h.write_raw('return {v=1,id=401,t=0,op="ping",a={x=io.open("x")}}')  # 코드는 돌지 않는다
    h.step(5)
    assert h.answers(401) == []
    h.write_request(399, "ping")  # 이미 지나간 번호
    h.step(5)
    assert h.answers(399) == []
    h.write_request(402, "stop")
    h.run_until(lambda: h.thread(th).done)
    assert h.thread(th).err is None


def test_loop_ends_when_bmd_wait_does_not_wait(h):
    h.fake.time_stuck = True  # 기다렸다는데 시간이 가지 않음 = bmd.wait가 쉬지 않는다
    th = h.click()
    rounds = h.run_until(lambda: h.thread(th).done, max_rounds=400)
    assert 290 <= rounds <= 310 and h.thread(th).err is None
    ((_, rid, payload),) = h.saved
    assert rid == 0 and payload["error"] == "wait_not_waiting"


def test_long_running_loop_keeps_going(h):
    th = h.click()
    h.step(700)
    assert not h.thread(th).done and h.saved == []
    h.write_request(90, "stop")
    h.run_until(lambda: h.thread(th).done)


def test_fatal_without_loadfile(h):
    h.lua.globals().loadfile = None
    th = h.click()
    h.step()
    assert h.thread(th).done and h.thread(th).err is None
    ((owner, rid, payload),) = h.saved
    assert rid == 0 and payload["ok"] is False and payload["error"] == "no_loadfile"
    assert payload["env"]["loadfile"] is False
    assert h.fake.waits == 0


def test_fatal_when_not_installed(tmp_path):
    h = Harness(tmp_path)
    h.source = SCRIPT.read_text(encoding="utf-8")  # 자리 표시가 그대로인 원본
    th = h.click()
    h.step()
    assert h.thread(th).done and h.thread(th).err is None
    ((_, rid, payload),) = h.saved
    assert rid == 0 and payload["error"] == "bad_mailbox" and payload["sv"] == "@@SCRIPT_VERSION@@"


def test_without_fusion_does_nothing(h):
    g = h.lua.globals()
    g.fusion = None
    h.lua.execute("local S = ...; S.resolve.Fusion = nil", h.fake)
    th = h.click()
    h.step()
    assert h.thread(th).done and h.thread(th).err is None
    assert h.fake.setprefs == 0 and h.saved == []


def test_finds_resolve_through_bmd_scriptapp(h):
    g = h.lua.globals()
    g.resolve = None
    g.fusion = None
    th = h.click()
    h.step()
    h.write_request(500, "ping")
    h.run_until(lambda: h.answers(500))
    r = h.answers(500)[0][1]["result"]
    assert r["resolve_found"] is True and r["product"] == "DaVinci Resolve"
    h.write_request(501, "stop")
    h.run_until(lambda: h.thread(th).done)
