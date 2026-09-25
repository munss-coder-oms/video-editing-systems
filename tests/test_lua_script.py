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
    -- media_frames(path): 가져온 파일의 길이(프레임). 없으면 .wav는 90, 나머지는 100000
    media_frames = nil,
    -- 리졸브가 파일 경로를 다르게(긴 이름으로) 알려 주는 것 흉내: rewrite_from으로 시작하면 rewrite_to로
    rewrite_from = nil, rewrite_to = nil,
    -- 이미 저장소에 있는 파일을 다시 가져오면 빈 목록을 주는 판 흉내
    import_empty_if_present = false,
    -- 지금 화면 (edit, fairlight, media, fusion ...)
    page = "edit",
    -- 오디오 클립을 끄면 이어진 영상 클립도 같이 꺼지는 판 흉내
    link_enable_propagates = false,
    -- GetMarkInOut / GetSelectedClips 답
    in_out = nil, selected = nil,
    uid = 0,
    profile = "C:\\Users\\문성\\AppData\\Roaming\\Blackmagic Design\\DaVinci Resolve\\Support\\Fusion\\Profiles\\Default\\",
  }
  local function log(name, ...)
    S.log[#S.log + 1] = { name = name, n = select("#", ...), args = { ... } }
  end
  local function next_uid(kind)
    S.uid = S.uid + 1
    return kind .. "-" .. S.uid
  end

  local function new_mpi(name, path)
    local frames = S.media_frames and S.media_frames(path)
    if frames == nil then
      frames = (path and string.match(string.lower(path), "%.wav$")) and 90 or 100000
    end
    local m = { name = name, uid = next_uid("mpi"), props = { ["File Path"] = path, FPS = "29.97", Frames = tostring(frames) } }
    m.mapping = '{"embedded_audio_channels":2,"track_mapping":{"1":{"channel_idx":[1,2],"type":"Stereo"}}}'
    function m:GetName() return self.name end
    function m:GetUniqueId() return self.uid end
    function m:GetAudioMapping() return self.mapping end
    function m:GetClipProperty(k)
      if k == nil then return self.props end
      return self.props[k]
    end
    return m
  end
  S.new_mpi = new_mpi

  local function new_item(name, start, len, mpi)
    local it = { name = name, start = start, len = len, mpi = mpi, left = 12, src = 34, enabled = true, linked = {},
                 uid = next_uid("item") }
    function it:GetName() return self.name end
    function it:GetUniqueId() return self.uid end
    function it:GetStart() return self.start end
    function it:GetEnd() return self.start + self.len end
    function it:GetDuration() return self.len end
    function it:GetLeftOffset() return self.left end
    function it:GetSourceStartFrame() return self.src end
    function it:GetSourceEndFrame() return self.src + self.len end
    function it:GetMediaPoolItem() return self.mpi end
    function it:GetTrackTypeAndIndex() return self.where end
    function it:GetClipEnabled() return self.enabled end
    function it:SetClipEnabled(v)
      log("SetClipEnabled", self.name, v)
      self.enabled = v and true or false
      if S.link_enable_propagates then
        for _, o in ipairs(self.linked) do o.enabled = self.enabled end
      end
      return true
    end
    function it:GetLinkedItems()
      local o = {}
      for i, x in ipairs(self.linked) do o[i] = x end
      return o
    end
    function it:GetSourceAudioChannelMapping()
      return '{"track_mapping":{"1":{"channel_idx":[1,2],"mute":false,"type":"Stereo"}}}'
    end
    function it:GetProperty(k)
      local props = { Pan = 0, Volume = 0, ClipEnabled = self.enabled, Speed = 100 }
      if k == nil then return props end
      return props[k]
    end
    return it
  end
  local function link(a, b)
    a.linked[#a.linked + 1] = b
    b.linked[#b.linked + 1] = a
  end
  S.link = link

  local TRACK_WORD = { video = "Video ", audio = "Audio ", subtitle = "Subtitle " }
  local PLAYHEAD_PAGES = { cut = true, edit = true, color = true, fairlight = true, deliver = true }
  S.timelines = {}
  local new_timeline

  local function copy_item(it)
    local c = new_item(it.name, it.start, it.len, it.mpi)
    c.left, c.src, c.enabled, c.where = it.left, it.src, it.enabled, it.where
    return c
  end

  new_timeline = function(name, start, len)
    local tl = {
      name = name, start = start, len = len, start_tc = "01:00:00:00", current_tc = "01:00:10:00",
      settings = { timelineFrameRate = "29.97", timelineDropFrameTimecode = "0" },
      tracks = { video = {}, audio = {}, subtitle = {} }, markers = {}, uid = next_uid("tl"),
    }
    local function ensure_track(kind, index)
      local list = tl.tracks[kind]
      while #list < index do
        list[#list + 1] = { name = TRACK_WORD[kind] .. (#list + 1), items = {}, enabled = true, sub = "stereo" }
      end
      return list[index]
    end
    tl.ensure_track = ensure_track
    function tl:GetName() return self.name end
    function tl:SetName(n) self.name = n; return true end
    function tl:GetUniqueId() return self.uid end
    function tl:GetStartFrame() return self.start end
    function tl:GetEndFrame() return self.start + self.len end
    function tl:GetStartTimecode() return self.start_tc end
    function tl:GetCurrentTimecode() return self.current_tc end
    function tl:SetCurrentTimecode(tc)
      log("SetCurrentTimecode", tc, S.page)
      if not PLAYHEAD_PAGES[S.page] then return false end
      self.current_tc = tc
      return true
    end
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
    function tl:SetTrackName(kind, i, n)
      log("SetTrackName", kind, i, n)
      local t = self.tracks[kind][i]
      if not t then return false end
      t.name = n
      return true
    end
    function tl:GetIsTrackEnabled(kind, i)
      local t = self.tracks[kind][i]
      if not t then return nil end
      return t.enabled
    end
    function tl:SetTrackEnable(kind, i, v)
      log("SetTrackEnable", kind, i, v)
      local t = self.tracks[kind][i]
      if not t then return false end
      t.enabled = v and true or false
      return true
    end
    function tl:GetIsTrackLocked(kind, i) return false end
    function tl:GetTrackSubType(kind, i)
      local t = self.tracks[kind][i]
      return t and t.sub or nil
    end
    function tl:AddTrack(kind, sub)
      log("AddTrack", kind, sub)
      local list = self.tracks[kind]
      list[#list + 1] = { name = TRACK_WORD[kind] .. (#list + 1), sub = sub, items = {}, enabled = true }
      return true
    end
    function tl:DeleteTrack(kind, i)
      local t = self.tracks[kind][i]
      log("DeleteTrack", kind, i, t and t.name)
      if not t then return false end
      table.remove(self.tracks[kind], i)
      return true
    end
    -- 리졸브처럼 편집(Edit) 화면이 아니면 지우지 않는다
    function tl:DeleteClips(items, ripple)
      local names = {}
      for _, it in ipairs(items) do names[#names + 1] = it.name end
      log("DeleteClips", names, S.page)
      if S.page ~= "edit" then return false end
      for _, it in ipairs(items) do
        for _, kind in ipairs({ "video", "audio" }) do
          for _, t in ipairs(self.tracks[kind]) do
            for j = #t.items, 1, -1 do
              if t.items[j] == it then table.remove(t.items, j) end
            end
          end
        end
      end
      return true
    end
    function tl:AddMarker(frame, color, n, note, duration, custom)
      log("AddMarker", frame, color, n, note, duration, custom)
      if self.markers[frame] then return false end
      if S.no_range_markers and duration > 1 then return false end
      self.markers[frame] = { color = color, name = n, note = note, duration = duration, customData = custom }
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
    function tl:GetMarkInOut() return S.in_out or {} end
    function tl:GetSelectedClips() return S.selected or {} end
    function tl:DuplicateTimeline(n)
      log("DuplicateTimeline", self.name, n)
      local c = new_timeline(n or (self.name .. " copy"), self.start, self.len)
      c.start_tc, c.current_tc = self.start_tc, self.current_tc
      for k, v in pairs(self.settings) do c.settings[k] = v end
      local map = {}
      for _, kind in ipairs({ "video", "audio", "subtitle" }) do
        for i, t in ipairs(self.tracks[kind]) do
          local ct = c.ensure_track(kind, i)
          ct.name, ct.enabled, ct.sub = t.name, t.enabled, t.sub
          for _, it in ipairs(t.items) do
            local ci = copy_item(it)
            map[it] = ci
            ct.items[#ct.items + 1] = ci
          end
        end
      end
      for old, ci in pairs(map) do
        for _, o in ipairs(old.linked) do
          if map[o] then ci.linked[#ci.linked + 1] = map[o] end
        end
      end
      for f, m in pairs(self.markers) do
        c.markers[f] = { color = m.color, name = m.name, note = m.note, duration = m.duration, customData = m.customData }
      end
      S.timelines[#S.timelines + 1] = c
      return c
    end
    return tl
  end
  S.new_timeline = new_timeline

  local tl = new_timeline("타임라인 1", 86400, 9000)
  S.timelines[1] = tl
  S.timeline = tl
  function S.add_timeline(name, start, len)
    local t = new_timeline(name, start or 86400, len or 9000)
    S.timelines[#S.timelines + 1] = t
    return t
  end

  function S.add_clip(kind, track, name, start, len, path)
    local t = S.timeline.ensure_track(kind, track)
    local it = new_item(name, start, len, path and new_mpi(name, path) or nil)
    it.where = { kind, track }
    t.items[#t.items + 1] = it
    return it
  end
  function S.set_track_name(kind, track, name) S.timeline.ensure_track(kind, track).name = name end
  function S.track_names(kind)
    local out = {}
    for i, t in ipairs(S.timeline.tracks[kind]) do out[i] = t.name end
    return out
  end
  function S.timeline_names()
    local out = {}
    for i, t in ipairs(S.timelines) do out[i] = t.name end
    return out
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
      for _, c in ipairs(self.current.clips) do
        if c.name == name then
          if S.import_empty_if_present then return {} end
          if S.import_returns_existing then out[i] = c end
        end
      end
      if out[i] == nil then
        local m = new_mpi(name, reported(p))
        self.current.clips[#self.current.clips + 1] = m
        out[i] = m
      end
    end
    return out
  end
  function pool:DeleteClips(clips)
    local names = {}
    for _, c in ipairs(clips) do names[#names + 1] = c.name end
    log("MediaPool.DeleteClips", names)
    local function drop(folder)
      for j = #folder.clips, 1, -1 do
        for _, c in ipairs(clips) do
          if folder.clips[j] == c then table.remove(folder.clips, j); break end
        end
      end
      for _, sub in ipairs(folder.subs) do drop(sub) end
    end
    drop(self.root)
    return true
  end
  function pool:DeleteTimelines(list)
    local names = {}
    for _, t in ipairs(list) do names[#names + 1] = t.name end
    log("DeleteTimelines", names)
    for _, t in ipairs(list) do
      if t == S.timeline then return false end
    end
    for _, t in ipairs(list) do
      for j = #S.timelines, 1, -1 do
        if S.timelines[j] == t then table.remove(S.timelines, j) end
      end
    end
    return true
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
    local cur = S.timeline
    local mpi = info.mediaPoolItem
    -- endFrame은 들어가지 않는 끝 (리졸브 21.1에서 확인). 파일보다 긴 범위는 넣지 않는다
    if info.endFrame > tonumber(mpi.props.Frames) then return {} end
    local len = info.endFrame - info.startFrame
    local placed = {}
    if info.trackIndex == nil and info.mediaType == nil then
      -- 영상+소리 클립: V1과 A1에 이어서 넣는다
      local v = new_item(mpi.name, info.recordFrame, len, mpi)
      local a = new_item(mpi.name, info.recordFrame, len, mpi)
      v.where, a.where = { "video", 1 }, { "audio", 1 }
      link(v, a)
      local vt, at = cur.ensure_track("video", 1), cur.ensure_track("audio", 1)
      vt.items[#vt.items + 1] = v
      at.items[#at.items + 1] = a
      placed = { v, a }
    else
      local track = cur.tracks.audio[info.trackIndex]
      if not track then return {} end
      local it = new_item(mpi.name, info.recordFrame, len, mpi)
      it.where = { "audio", info.trackIndex }
      track.items[#track.items + 1] = it
      placed = { it }
    end
    if S.append_answer == "true" then return true end
    if S.append_answer == "empty" then return {} end
    return placed
  end

  local project = { name = "시험 프로젝트", settings = { timelineFrameRate = "29.97", timelineDropFrameTimecode = "0" } }
  S.project = project
  function project:GetName() return self.name end
  function project:GetUniqueId() return "proj-1" end
  function project:GetCurrentTimeline()
    if S.timeline_open then return S.timeline end
    return nil
  end
  function project:SetCurrentTimeline(t)
    log("SetCurrentTimeline", t and t.name)
    for _, x in ipairs(S.timelines) do
      if x == t then S.timeline = t; return true end
    end
    return false
  end
  function project:GetTimelineCount() return #S.timelines end
  function project:GetTimelineByIndex(i) return S.timelines[i] end
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
  function resolve:GetCurrentPage() return S.page end
  function resolve:OpenPage(p)
    log("OpenPage", p)
    S.page = p
    return true
  end
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
    assert (r["start_frame"], r["end_frame"]) == (86400, 95400)  # 끝은 들어가지 않는 값 (21.1에서 확인)
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
    assert len(h.logged("DeleteMarkerByCustomData")) == 2000  # MAX_DELETE


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
        "item": "aih_test_tone.wav", "startFrame": 0, "endFrame": 90, "mediaType": 2, "trackIndex": 2,
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


# ---------------------------------------------------------------------------
# 스크립트 1.1.0: 할 일 목록, 막아 둔 함수, 읽기 전용 할 일, 기능 점검(probe_copy)
# ---------------------------------------------------------------------------

from engine.resolve_link import protocol  # noqa: E402


def _lua_table_keys(text: str, name: str) -> set:
    m = re.search(name + r"\s*=\s*\{(.*?)\n\}", text, flags=re.S)
    assert m, name
    return set(re.findall(r"([A-Za-z_]\w*)\s*=\s*true", m.group(1)))


def test_ops_allowed_equals_protocol_ops():
    text = SCRIPT.read_text(encoding="utf-8")
    assert _lua_table_keys(text, r"local OPS_ALLOWED") == set(protocol.OPS)
    assert len(protocol.OPS) == len(set(protocol.OPS))


def test_ping_lists_ops_page_and_product(h):
    h.init()
    h.fake.page = "fairlight"
    r = h.op("ping")["result"]
    assert r["ops"] == sorted(protocol.OPS)
    assert r["page"] == "fairlight"
    assert r["product"] == "DaVinci Resolve" and r["product_version"] == "21.1.0.0"


def test_deny_list_names_appear_only_in_the_deny_table():
    text = SCRIPT.read_text(encoding="utf-8")
    denied = _lua_table_keys(text, r"AIH\.DENY")
    assert {"CreateSubtitlesFromAudio", "TranscribeAudio", "GenerateSpeech", "SetVoiceIsolationState",
            "ConvertTimelineToStereo", "AnalyzeDolbyVision", "SmartReframe"} <= denied
    code = _code_only(text)
    table = re.search(r"AIH\.DENY\s*=\s*\{.*?\n\}", code, flags=re.S).group(0)
    rest = code.replace(table, "")
    for name in denied:
        assert not re.search(r"\b" + name + r"\b", rest), name


def test_denied_method_is_never_called(h):
    aih = h.init()
    h.lua.execute("local S = ...; S.resolve.GenerateSpeech = function() S.denied_called = true; return true end", h.fake)
    calls = h.lua.table()
    value, ok = aih.call(calls, "Resolve.GenerateSpeech", h.fake.resolve, "GenerateSpeech")
    assert value is None and ok is False
    assert calls["Resolve.GenerateSpeech"] == "err:denied"
    assert h.fake.denied_called is None


@pytest.mark.parametrize("custom", ["aih:P1:1", "aih_test"])
def test_delete_markers_accepts_our_tags(h, custom):
    h.init()
    h.op("add_marker", {"frame": 10, "custom": custom})
    h.op("add_marker", {"frame": 20, "custom": "user"})
    r = h.op("delete_markers", {"custom": custom})
    assert r["ok"] is True and r["result"]["deleted_count"] == 1
    assert [m["custom"] for m in h.op("get_markers")["result"]["markers"]] == ["user"]


@pytest.mark.parametrize("custom", ["autosubs_1", "", "aih", "AIH:x", "xaih:1", " aih:1"])
def test_delete_markers_refuses_foreign_tags_before_any_resolve_call(h, custom):
    h.init()
    h.op("add_marker", {"frame": 10, "custom": custom, "name": "다른 도구 표시"})
    before = h.fake.pm_calls
    res = h.op("delete_markers", {"custom": custom})
    assert res == {"ok": False, "sv": "test-1", "error": "bad_args", "func": "custom", "calls": {}}
    assert h.fake.pm_calls == before  # 리졸브를 부르기 전에 거절
    assert h.logged("DeleteMarkerByCustomData") == [] and h.logged("DeleteMarkerAtFrame") == []
    assert [m["custom"] for m in h.op("get_markers")["result"]["markers"]] == [custom]


def _obs_timeline(h, n_audio=4):
    """OBS 녹화 한 개: V1 + A1..A4 (서로 이어짐)."""
    path = "C:\\Users\\문성\\Videos\\2026-09-20 녹화.mp4"
    v = h.fake.add_clip("video", 1, "녹화.mp4", 86400, 900, path)
    for i in range(1, n_audio + 1):
        a = h.fake.add_clip("audio", i, "녹화.mp4", 86400, 900, path)
        a.mpi = v.mpi
        h.fake.link(v, a)
    h.fake.set_track_name("audio", 1, "마이크")
    return v


def test_timeline_info(h):
    _obs_timeline(h, 2)
    h.fake.add_clip("subtitle", 1, "안녕", 86400, 30, None)
    h.init()
    r = h.op("timeline_info")["result"]
    assert r["project"] == "시험 프로젝트" and r["project_uid"] == "proj-1"
    assert r["timeline"] == "타임라인 1" and r["timeline_uid"] == h.fake.timeline.uid
    assert (r["start_frame"], r["end_frame"]) == (86400, 95400)
    assert (r["fps"], r["drop_frame"], r["page"]) == ("29.97", False, "edit")
    assert r["timeline_count"] == 1 and r["is_probe_copy"] is False and r["probe"] is None
    a1 = r["tracks"]["audio"][0]
    assert a1 == {"index": 1, "name": "마이크", "enabled": True, "locked": False, "subtype": "stereo", "count": 1}
    assert [t["count"] for t in r["tracks"]["video"]] == [1]
    assert r["tracks"]["subtitle"][0]["count"] == 1 and "subtype" not in r["tracks"]["subtitle"][0]
    assert set(r["calls"].values()) <= {"ok", "ok:false"}  # GetIsTrackLocked가 false를 준 것


def test_timeline_info_survives_missing_methods(h):
    _obs_timeline(h, 1)
    h.init()
    h.fake.resolve.GetCurrentPage = None
    h.fake.timeline.GetUniqueId = None
    h.fake.timeline.GetIsTrackEnabled = None
    r = h.op("timeline_info")["result"]
    assert r["page"] is None and r["timeline_uid"] is None
    assert r["tracks"]["audio"][0]["enabled"] is None
    assert r["calls"]["Resolve.GetCurrentPage"] == "missing"
    assert r["calls"]["Timeline.GetUniqueId"] == "missing"


def test_timeline_items_pages_stay_small(h):
    long_dir = "C:\\Users\\문성\\Videos\\" + "아주 긴 폴더 이름 " * 8
    h.lua.execute(
        "local S, d = ...; for i = 1, 350 do "
        "S.add_clip('audio', 1 + (i % 4), 'clip' .. i, 86400 + i * 10, 10, d .. 'file' .. i .. '.wav') end",
        h.fake, long_dir,
    )
    aih = h.init()
    assert aih.PAGE_ITEMS == 100
    seen, offset, pages = [], 0, 0
    while True:
        h.next_id += 1
        h.write_request(h.next_id, "timeline_items", {"kind": "audio", "offset": offset, "limit": 100})
        text = h._handle_json(aih, aih.read_request())
        assert len(text.encode("utf-8")) <= 64 * 1024
        r = json.loads(text)["result"]
        pages += 1
        assert 1 <= len(r["items"]) <= 100
        seen += [it["name"] for it in r["items"]]
        if r["next"] is None:
            break
        assert r["next"] == offset + len(r["items"])
        offset = r["next"]
    assert pages >= 4
    assert sorted(seen) == sorted(f"clip{i}" for i in range(1, 351)) and len(seen) == 350
    first = h.op("timeline_items", {"kind": "audio", "limit": 1})["result"]["items"][0]
    assert set(first) == {"track", "kind", "uid", "name", "start", "end", "duration", "left_offset", "source_start",
                          "source_end", "enabled", "path", "clip_fps", "media_uid", "linked_uids"}
    assert first["end"] - first["start"] == first["duration"]
    assert h.op("timeline_items", {"limit": 101})["error"] == "bad_args"
    assert h.op("timeline_items", {"kind": "subtitle"})["error"] == "bad_args"


def test_timeline_items_linked_uids_and_track_range(h):
    v = _obs_timeline(h, 3)
    h.init()
    r = h.op("timeline_items", {"kind": "audio", "track_from": 2, "track_to": 3})["result"]
    assert [it["track"] for it in r["items"]] == [2, 3] and r["next"] is None
    assert v.uid in r["items"][0]["linked_uids"]
    rv = h.op("timeline_items", {"kind": "video"})["result"]
    assert len(rv["items"][0]["linked_uids"]) == 3


def test_scope(h):
    v = _obs_timeline(h, 1)
    h.lua.execute("local S, v = ...; S.in_out = {video = {['in'] = 100, out = 200}, audio = {}}; S.selected = {v}",
                  h.fake, v)
    h.init()
    r = h.op("scope")["result"]
    assert r["page"] == "edit" and r["playhead_tc"] == "01:00:10:00" and r["start_tc"] == "01:00:00:00"
    assert r["start_frame"] == 86400 and r["fps"] == "29.97"
    assert r["in_out"] == {"video": {"in": 100, "out": 200}}
    assert r["selected_uids"] == [v.uid] and r["selected_count"] == 1


def test_scope_survives_missing_methods(h):
    _obs_timeline(h, 1)
    h.init()
    tl = h.fake.timeline
    tl.GetMarkInOut = None
    tl.GetCurrentTimecode = None
    h.lua.execute("local tl = ...; tl.GetSelectedClips = function() error('21.0.4 이전 판') end", tl)
    res = h.op("scope")
    assert res["ok"] is True
    r = res["result"]
    assert r["in_out"] is None and r["playhead_tc"] is None
    assert r["selected_uids"] is None and r["selected_count"] is None
    assert r["calls"]["Timeline.GetMarkInOut"] == "missing"
    assert r["calls"]["Timeline.GetSelectedClips"].startswith("err:")


def test_probe_read(h):
    _obs_timeline(h, 2)
    h.init()
    r = h.op("probe_read")["result"]
    ex = r["exists"]
    assert ex["Timeline.DuplicateTimeline"] is True and ex["Timeline.GetMarkInOut"] is True
    assert ex["Timeline.Export"] is False and ex["TimelineItem.SetFades"] is False
    assert ex["MediaPoolItem.GetAudioMapping"] is True
    assert r["existence_reliable"] is True
    assert "Timeline.AIH_NoSuchMethod" not in ex
    assert r["props_keys"]["audio"] == ["ClipEnabled", "Pan", "Speed", "Volume"]
    assert [m["track"] for m in r["source_audio_mapping"]] == [1, 2]
    assert r["source_audio_mapping"][0]["truncated"] is False
    assert json.loads(r["source_audio_mapping"][0]["mapping"])["track_mapping"]["1"]["type"] == "Stereo"
    assert json.loads(r["clip_audio_mapping"]["mapping"])["embedded_audio_channels"] == 2
    assert r["project_uid"] == "proj-1" and r["timeline_uid"] == h.fake.timeline.uid
    assert r["selected_count"] == 0 and r["in_out"] is None
    # 이름만 찾아보고 부르지 않는다: 바꾸는 함수가 불리지 않았다
    for name in ("DuplicateTimeline", "SetTrackEnable", "SetClipEnabled", "AddMarker", "DeleteClips"):
        assert h.logged(name) == []


def test_probe_read_control_names_and_missing_objects(h):
    _obs_timeline(h, 1)
    h.init()
    h.lua.execute("local tl = ...; tl.Razor = function() end", h.fake.timeline)
    h.fake.timeline.GetSelectedClips = None
    r = h.op("probe_read")["result"]
    assert r["existence_reliable"] is False and r["exists"]["Timeline.Razor"] is True
    assert r["exists"]["Timeline.GetSelectedClips"] is False and r["selected_count"] is None
    h.fake.timeline_open = False
    res = h.op("probe_read")
    assert res["ok"] is True
    assert res["result"]["exists"]["Timeline.GetUniqueId"] is None  # 물어볼 타임라인이 없음


def test_probe_read_truncates_long_mapping(h):
    _obs_timeline(h, 1)
    h.init()
    h.lua.execute("local tl = ...; local it = tl.tracks.audio[1].items[1]; "
                  "it.GetSourceAudioChannelMapping = function() return string.rep('x', 5000) end", h.fake.timeline)
    m = h.op("probe_read")["result"]["source_audio_mapping"][0]
    assert m["truncated"] is True and len(m["mapping"]) == 4096


# --- 기능 점검 (probe_copy) ---

def _probe_env(h):
    _obs_timeline(h, 2)
    h.fake.timeline.markers[500] = h.lua.table(color="Green", name="사용자", note="", duration=1, customData="user")
    aih = h.init()
    original = h.fake.timeline
    return aih, original


def _probe_wav(h):
    probe_dir = Path(h.files) / "probe"
    probe_dir.mkdir(exist_ok=True)
    return str(probe_dir / "probe_140210.wav")


def _stage(h, stage, **args):
    res = h.op("probe_copy", dict(args, stage=stage))
    return res


def test_probe_copy_full_run_touches_only_the_copy(h):
    aih, original = _probe_env(h)
    fp_original = aih.fingerprint(original)
    assert re.fullmatch(r"\d+:\d+:\d+", fp_original)
    fps = {}
    r1 = _stage(h, "C1", suffix="140210")
    assert r1["ok"] is True, r1
    res = r1["result"]
    assert res["ok"] is True and res["detail"]["switched"] is True
    assert h.fake.timeline.name == "AI 도우미 점검용 140210" and h.fake.timeline.uid != original.uid
    assert res["detail"]["original_uid"] == original.uid and res["probe"]["copy_uid"] == h.fake.timeline.uid
    fps["C1"] = res["fingerprint"]
    wav = _probe_wav(h)
    h.fake.media_frames = lambda path: 120  # 2초 WAV (29.97fps에서 59.94프레임을 넉넉히)
    args = {"C5": {"tc": "01:00:20:00"}, "C7": {"path": wav, "frames": 59}}
    for stage in ("C2", "C3", "C4", "C5", "C6", "C7"):
        out = _stage(h, stage, **args.get(stage, {}))
        assert out["ok"] is True, (stage, out)
        assert out["result"]["ok"] is True, (stage, out["result"])
        assert re.fullmatch(r"\d+:\d+:\d+", out["result"]["fingerprint"]), stage
        fps[stage] = out["result"]["fingerprint"]
        if stage == "C6":
            d = out["result"]["detail"]
            assert d["end_semantics"] == "exclusive" and d["items_before"] == 3
            assert [(p["track_type"], p["track"], p["length"]) for p in d["placed"]] == [("video", 1, 600), ("audio", 1, 600)]
        if stage == "C7":
            d = out["result"]["detail"]
            assert d["imported"] is True and d["found_by"] == "import"
            assert d["placed_frames"] == 59 and d["length_ok"] is True
            assert set(d["clip"]) == {"name", "path", "frames", "fps", "duration"}
    # 되돌린 단계(C2~C5)는 지문이 C1과 같다
    assert fps["C2"] == fps["C3"] == fps["C4"] == fps["C5"] == fps["C1"]
    assert fps["C6"] != fps["C5"] and fps["C7"] != fps["C6"]
    c8 = _stage(h, "C8", expect_fingerprint=fps["C7"])["result"]
    assert c8["ok"] is True and c8["detail"]["deleted"] is True and c8["detail"]["clip_deleted"] is True
    assert h.fake.timeline.uid == original.uid and lua_list(h.fake.timeline_names()) == ["타임라인 1"]
    assert c8["probe"] is None
    # 원래 타임라인은 그대로, 미디어 풀에는 빈 "AI 도우미" 저장소만 남는다
    assert aih.fingerprint(original) == fp_original
    assert original.markers[500].customData == "user"
    bin_ = h.fake.pool.root.subs[1]
    assert bin_.name == "AI 도우미" and len(bin_.clips) == 0
    assert h.fake.pool.current.name == "Master"
    assert h.logged("DeleteTimelines") == [[h.lua.table()]] or len(h.logged("DeleteTimelines")) == 1


def test_probe_copy_refuses_when_current_timeline_is_not_the_copy(h):
    aih, original = _probe_env(h)
    for stage, extra in (("C2", {}), ("C6", {})):
        res = _stage(h, stage, **extra)
        assert (res["ok"], res["error"]) == (False, "no_probe")
    _stage(h, "C1", suffix="140210")
    h.lua.execute("local S, o = ...; S.timeline = o", h.fake, original)  # 사용자가 원래 타임라인으로 돌아감
    wav = _probe_wav(h)
    for stage, extra in (("C2", {}), ("C3", {}), ("C4", {}), ("C5", {"tc": "01:00:20:00"}), ("C6", {}),
                         ("C7", {"path": wav, "frames": 60})):
        res = _stage(h, stage, **extra)
        assert (res["ok"], res["error"]) == (False, "not_probe_copy"), stage
    for name in ("SetTrackEnable", "SetClipEnabled", "AddMarker", "SetCurrentTimecode", "DeleteClips",
                 "AppendToTimeline", "ImportMedia"):
        assert h.logged(name) == [], name
    # 이름만 같은 사용자 타임라인도 복사본으로 보지 않는다 (번호가 다름)
    other = h.fake.add_timeline("AI 도우미 점검용 140210")
    h.lua.execute("local S, o = ...; S.timeline = o", h.fake, other)
    assert _stage(h, "C2")["error"] == "not_probe_copy"


def test_probe_copy_c1_refuses_on_a_probe_copy_and_with_leftover(h):
    aih, original = _probe_env(h)
    _stage(h, "C1", suffix="140210")
    assert _stage(h, "C1", suffix="140211")["error"] == "already_probe_copy"
    h.lua.execute("local S, o = ...; S.timeline = o", h.fake, original)
    assert _stage(h, "C1", suffix="140212")["error"] == "leftover_copy"
    assert _stage(h, "C1", suffix="1402")["error"] == "bad_args"
    assert len(h.logged("DuplicateTimeline")) == 1


def test_probe_copy_c8_keeps_copy_when_fingerprint_differs(h):
    aih, original = _probe_env(h)
    _stage(h, "C1", suffix="140210")
    fp_c2 = _stage(h, "C2")["result"]["fingerprint"]
    # C3이 클립을 끈 채로 멈춘 것처럼
    h.lua.execute("local S = ...; S.timeline.tracks.audio[1].items[1].enabled = false", h.fake)
    c8 = _stage(h, "C8", expect_fingerprint=fp_c2)["result"]
    assert c8["ok"] is False and c8["detail"]["deleted"] is False
    assert c8["detail"]["reason"] == "fingerprint_mismatch"
    assert h.fake.timeline.uid == original.uid  # 원래 타임라인으로는 돌아간다
    assert "AI 도우미 점검용 140210" in lua_list(h.fake.timeline_names())
    assert h.logged("DeleteTimelines") == []
    assert c8["probe"] is not None  # 기록은 남긴다


def test_probe_copy_c8_cleans_up_after_c3_error(h):
    aih, original = _probe_env(h)
    _stage(h, "C1", suffix="140210")
    fp_c2 = _stage(h, "C2")["result"]["fingerprint"]
    h.lua.execute("local S = ...; S.timeline.tracks.audio[1].items[1].SetClipEnabled = "
                  "function() error('예상 못 한 오류') end", h.fake)
    c3 = _stage(h, "C3")
    assert c3["ok"] is True and c3["result"]["ok"] is False
    assert c3["result"]["calls"]["TimelineItem.SetClipEnabled"].startswith("err:")
    assert c3["result"]["fingerprint"] == fp_c2
    c8 = _stage(h, "C8", expect_fingerprint=fp_c2)["result"]
    assert c8["ok"] is True and c8["detail"]["deleted"] is True
    assert lua_list(h.fake.timeline_names()) == ["타임라인 1"] and h.fake.timeline.uid == original.uid


def test_probe_copy_c8_without_record_uses_given_ids_with_same_guards(h):
    aih, original = _probe_env(h)
    c1 = _stage(h, "C1", suffix="140210")["result"]
    fp = c1["fingerprint"]
    ids = {k: c1["detail"][k] for k in ("original_uid", "original_name", "copy_uid", "copy_name")}
    h.lua.execute("local A = ...; A.st.probe = nil", aih)  # 스크립트를 다시 누른 것처럼
    assert _stage(h, "C8", expect_fingerprint="1:2:3", **ids)["result"]["detail"]["reason"] == "fingerprint_mismatch"
    h.lua.execute("local S, c = ...; S.timeline = c", h.fake, h.fake.timelines[2])
    assert _stage(h, "C8", expect_fingerprint="", **ids)["result"]["detail"]["reason"] == "no_expect"
    # 점검용 이름이 아닌 타임라인은 번호를 알려 줘도 지우지 않는다
    bad = dict(ids, copy_uid=original.uid, copy_name=original.name)
    h.lua.execute("local S, c = ...; S.timeline = c", h.fake, h.fake.timelines[2])
    r = _stage(h, "C8", expect_fingerprint=aih.fingerprint(original), **bad)["result"]
    assert r["detail"]["deleted"] is False and r["detail"]["reason"] in ("not_probe_name", "copy_is_current")
    h.lua.execute("local S, c = ...; S.timeline = c", h.fake, h.fake.timelines[2])
    r = _stage(h, "C8", expect_fingerprint=fp, **ids)["result"]
    assert r["detail"]["deleted"] is True and lua_list(h.fake.timeline_names()) == ["타임라인 1"]
    assert _stage(h, "C8", expect_fingerprint=fp)["error"] == "no_probe"


def test_probe_copy_c5_page_gate_and_c6_switches_to_edit_page(h):
    aih, original = _probe_env(h)
    _stage(h, "C1", suffix="140210")
    for page in ("media", "fusion"):
        h.fake.page = page
        r = _stage(h, "C5", tc="01:00:20:00")["result"]
        assert r["ok"] is False and r["detail"] == {"reason": "page", "page": page}
    assert h.logged("SetCurrentTimecode") == []
    for page in ("cut", "color", "fairlight", "deliver", "edit"):
        h.fake.page = page
        r = _stage(h, "C5", tc="01:00:20:00")["result"]
        assert r["ok"] is True and r["detail"]["restored"] == "01:00:10:00", page
    assert _stage(h, "C5", tc="1:00:20:00")["error"] == "bad_args"
    h.fake.page = "fairlight"
    r = _stage(h, "C6")["result"]
    assert r["ok"] is True and r["detail"]["page_switched_from"] == "fairlight"
    assert h.fake.page == "fairlight"
    assert [x[0] for x in h.logged("OpenPage")] == ["edit", "fairlight"]


def test_probe_copy_c4_range_marker_fallback(h):
    aih, original = _probe_env(h)
    _stage(h, "C1", suffix="140210")
    r = _stage(h, "C4")["result"]
    assert r["ok"] is True and r["detail"]["range_ok"] is True and r["detail"]["duration_readback"] == 120
    h.fake.no_range_markers = True
    r = _stage(h, "C4")["result"]
    assert r["ok"] is True and r["detail"]["point_only"] is True and r["detail"]["range_ok"] is False
    assert r["detail"]["left"] == 0
    assert all(c[5] == "aih:probe:c4" for c in h.logged("AddMarker"))


def test_probe_copy_c7_path_guard_and_reused_file(h):
    aih, original = _probe_env(h)
    _stage(h, "C1", suffix="140210")
    outside = str(Path(h.files) / "not_probe.wav")
    assert _stage(h, "C7", path=outside, frames=60)["error"] == "bad_path:not_probe"
    assert _stage(h, "C7", path="/etc/x.wav", frames=60)["error"].startswith("bad_path")
    wav = _probe_wav(h)
    assert _stage(h, "C7", path=wav, frames=60)["result"]["detail"]["imported"] is True
    h.fake.import_returns_existing = True  # 같은 파일을 다시 가져오면 있던 클립을 주는 판
    d = _stage(h, "C7", path=wav, frames=60)["result"]["detail"]
    assert d["imported"] is False  # 새로 가져온 것이 아니다


def test_switch_timeline_only_to_recorded_original(h):
    aih, original = _probe_env(h)
    other = h.fake.add_timeline("다른 타임라인")
    # 기록이 없고 지금 타임라인이 점검용 복사본이 아니면 거절
    assert h.op("switch_timeline", {"uid": other.uid})["error"] == "not_original"
    _stage(h, "C1", suffix="140210")
    assert h.op("switch_timeline", {"uid": other.uid})["error"] == "not_original"
    r = h.op("switch_timeline", {"uid": original.uid})["result"]
    assert r["switched"] is True and r["readback_uid"] == original.uid and r["recorded"] is True
    assert h.fake.timeline.uid == original.uid
    assert h.logged("DeleteTimelines") == [] and h.logged("DeleteClips") == []


def test_place_audio_exact_length(h):
    h.init()
    r = h.op("place_audio", {"path": str(Path(h.files) / "aih_test_tone.wav"), "track_name": TRACK_NAME,
                             "record_frame": 86400, "frames": 90})["result"]
    ((info,),) = h.logged("AppendToTimeline")
    assert info["endFrame"] == 90  # 들어가지 않는 끝
    assert (r["placed_frames"], r["length_ok"], r["requested_frames"]) == (90, True, 90)


def test_remove_audio_page_gate_and_results(h):
    ours = str(Path(h.files) / "aih_test_tone.wav")
    h.fake.add_clip("audio", 1, "원본", 0, 10, "C:\\Users\\문성\\Music\\a.wav")
    h.fake.add_clip("audio", 2, "ours", 0, 10, ours)
    h.fake.set_track_name("audio", 2, TRACK_NAME)
    h.init()
    for page in ("fairlight", "media", "color"):
        h.fake.page = page
        res = h.op("remove_audio", {"track_name": TRACK_NAME})
        assert (res["ok"], res["error"], res["func"]) == (False, "need_edit_page", "Resolve.GetCurrentPage")
    assert h.logged("DeleteClips") == [] and h.logged("DeleteTrack") == []
    h.fake.page = "edit"
    r = h.op("remove_audio", {"track_name": TRACK_NAME})["result"]
    assert r["page"] == "edit" and r["removed_tracks"] == 1
    assert r["delete_clips"] == [{"track": 2, "count": 1, "result": True}]
    assert r["delete_track"] == [{"track": 2, "result": True}]


def test_remove_audio_reports_delete_clips_refusal(h):
    ours = str(Path(h.files) / "aih_test_tone.wav")
    h.fake.add_clip("audio", 1, "ours", 0, 10, ours)
    h.fake.set_track_name("audio", 1, TRACK_NAME)
    h.init()
    h.fake.resolve.GetCurrentPage = None  # 화면을 알 수 없는 판: 예전처럼 해 본다
    h.lua.execute("local tl = ...; tl.DeleteClips = function() return false end", h.fake.timeline)
    r = h.op("remove_audio", {"track_name": TRACK_NAME})["result"]
    assert r["page"] is None and r["delete_clips"] == [{"track": 1, "count": 1, "result": False}]


def test_hundred_marker_request_fits_and_loads(h):
    """add_markers 100개 (이름 40자, 메모 200자에 가깝게)가 요청 크기 안에 들고, 샌드박스에서 읽혀 모두 들어간다."""
    aih = h.init()
    markers = [{"frame": i * 60, "dur": 30, "color": "Blue", "name": "쉼" * 40, "note": "메모 " * 66,
                "custom": f"aih:P12:{i}"} for i in range(1, 101)]
    text = protocol.encode_request(77, "add_markers", {"markers": markers}, t=NOW)
    assert len(text.encode("utf-8")) <= protocol.MAX_REQUEST_BYTES
    h.write_raw(text)
    r = aih.read_request()
    assert r is not None and r.id == 77
    res = json.loads(h._handle_json(aih, r))
    assert res["ok"] is True, res
    assert len(res["result"]["placed"]) == 100 and res["result"]["failed"] == []
    too_many = markers + [dict(markers[0], custom="aih:P12:101")]
    res = h.op("add_markers", {"markers": too_many})
    assert (res["ok"], res["error"], res["func"]) == (False, "bad_args", "markers")
    with pytest.raises(ValueError):
        protocol.encode_request(78, "get_markers", {"pad": "x" * protocol.MAX_REQUEST_BYTES})


# ---------------------------------------------------------------------------
# 2.1b: add_markers, delete_markers{prefix}, get_markers{prefix}
# ---------------------------------------------------------------------------

def _mk(i, frame, dur=30, **kw):
    row = {"frame": frame, "dur": dur, "color": "Blue", "name": f"쉼 {i}", "note": "메모 · AI 도우미",
           "custom": f"aih:P1:{i}"}
    row.update(kw)
    return row


def _user_markers(h):
    """사용자가 찍은 표시 (꼬리표가 없거나 우리 것이 아닌 것). 어떤 요청으로도 지워지면 안 된다."""
    for f, custom in ((5, ""), (6, "user"), (7, "aih"), (8, "AIH:P1:1"), (9, "autosubs_1"), (11, "aih-P1-1"),
                      (12, " aih:P1:1")):
        h.op("add_marker", {"frame": f, "color": "Green", "name": "내 표시", "custom": custom})
    h.fake.log = h.lua.table()  # 준비한 AddMarker 기록은 지운다
    return {5: "", 6: "user", 7: "aih", 8: "AIH:P1:1", 9: "autosubs_1", 11: "aih-P1-1", 12: " aih:P1:1"}


def _markers(h, prefix=None):
    args = {} if prefix is None else {"prefix": prefix}
    return h.op("get_markers", args)["result"]["markers"]


def test_add_markers_places_tagged_markers_and_reads_back(h):
    h.init()
    r = h.op("add_markers", {"markers": [_mk(1, 100), _mk(2, 400, dur=90), _mk(3, 900, dur=1)]})
    assert r["ok"] is True, r
    res = r["result"]
    assert [(p["i"], p["frame"], p["dur"], p["custom"], p["shifted"], p["found"], p["dur_readback"])
            for p in res["placed"]] == [(1, 100, 30, "aih:P1:1", 0, True, 30), (2, 400, 90, "aih:P1:2", 0, True, 90),
                                        (3, 900, 1, "aih:P1:3", 0, True, 1)]
    assert res["failed"] == [] and res["skipped_existing"] == [] and res["point_fallback"] is False
    assert res["requested"] == 3 and res["length"] == 9000
    assert res["calls"]["Timeline.GetMarkers"] == "ok" and res["calls"]["Timeline.GetMarkers.after"] == "ok"
    assert [x[4] for x in h.logged("AddMarker")] == [30, 90, 1]
    assert [m["custom"] for m in _markers(h)] == ["aih:P1:1", "aih:P1:2", "aih:P1:3"]


def test_add_markers_hostile_strings_arrive_exact(h):
    h.init()
    name = HOSTILE_NAME[:40]
    r = h.op("add_markers", {"markers": [_mk(1, 300, name=name, note=HOSTILE_NOTE)]})
    assert r["ok"] is True
    ((frame, color, got_name, note, duration, custom),) = h.logged("AddMarker")
    assert (frame, color, got_name, note, duration, custom) == (300, "Blue", name, HOSTILE_NOTE, 30, "aih:P1:1")
    assert h.fake.pm_calls == 1  # 이름 속 글자가 코드로 실행되지 않았다
    # 꼬리표에 코드 글자를 넣으면 거절
    r = h.op("add_markers", {"markers": [_mk(2, 400, custom='aih:P1:2"}) resolve:GetProjectManager() --')]})
    assert (r["ok"], r["error"], r["func"]) == (False, "bad_args", "custom")


@pytest.mark.parametrize("change,func", [
    ({"color": "Orange"}, "color"),
    ({"color": ""}, "color"),
    ({"custom": "user"}, "custom"),
    ({"custom": "aih_test"}, "custom"),
    ({"custom": "AIH:P1:1"}, "custom"),
    ({"custom": "aih:"}, "custom"),
    ({"custom": "aih:P1 1"}, "custom"),
    ({"custom": "aih:" + "x" * 61}, "custom"),
    ({"custom": 5}, "custom"),
    ({"frame": -1}, "frame"),
    ({"frame": 1.5}, "frame"),
    ({"dur": 0}, "dur"),
    ({"name": "가" * 41}, "name"),
    ({"note": "나" * 201}, "note"),
    ({"frame": 8990, "dur": 20}, "frame"),  # 타임라인 끝(9000)을 넘는다
])
def test_add_markers_refuses_bad_input_before_any_change(h, change, func):
    h.init()
    rows = [_mk(1, 100), {**_mk(2, 200), **change}]
    r = h.op("add_markers", {"markers": rows})
    assert (r["ok"], r["error"], r["func"]) == (False, "bad_args", func)
    assert h.logged("AddMarker") == []  # 하나라도 틀리면 아무것도 넣지 않는다


def test_add_markers_refuses_empty_duplicate_and_too_many(h):
    h.init()
    for markers in ([], [_mk(1, 100), _mk(1, 200)], [_mk(i, i * 10) for i in range(1, 102)], "x"):
        r = h.op("add_markers", {"markers": markers})
        assert r["ok"] is False and r["error"] == "bad_args", r
    assert h.logged("AddMarker") == []


def test_add_markers_resend_is_idempotent(h):
    h.init()
    rows = [_mk(1, 100), _mk(2, 200)]
    first = h.op("add_markers", {"markers": rows})["result"]
    again = h.op("add_markers", {"markers": rows + [_mk(3, 300)]})["result"]
    assert len(first["placed"]) == 2
    assert again["skipped_existing"] == [1, 2] and [p["custom"] for p in again["placed"]] == ["aih:P1:3"]
    assert len(h.logged("AddMarker")) == 3
    assert len(_markers(h, "aih:P1:")) == 3


def test_add_markers_shifts_past_occupied_frames_and_keeps_the_end(h):
    h.init()
    for f in (300, 301):
        h.op("add_marker", {"frame": f, "name": "사용자 표시", "custom": "user"})
    for f in range(600, 606):
        h.op("add_marker", {"frame": f, "name": "사용자 표시", "custom": ""})
    res = h.op("add_markers", {"markers": [_mk(1, 300, dur=10), _mk(2, 600, dur=10)]})["result"]
    assert [(p["frame"], p["dur"], p["shifted"]) for p in res["placed"]] == [(302, 8, 2)]
    assert res["failed"] == [{"i": 2, "err": "taken"}]
    user = [m for m in _markers(h) if not str(m["custom"]).startswith("aih:")]
    assert len(user) == 8  # 사용자 표시는 그대로


def test_add_markers_falls_back_to_point_markers(h):
    h.init()
    h.lua.execute("local S = ...; S.no_range_markers = true", h.fake)
    res = h.op("add_markers", {"markers": [_mk(1, 100, dur=30), _mk(2, 200, dur=30)]})["result"]
    assert res["point_fallback"] is True
    assert [(p["frame"], p["dur"], p["dur_readback"]) for p in res["placed"]] == [(100, 1, 1), (200, 1, 1)]
    assert res["calls"]["Timeline.AddMarker.point"] == "ok"
    # 첫 표시에서 길이 있는 표시가 안 되는 것을 알았으니 두 번째는 바로 길이 1로 넣는다
    assert [x[4] for x in h.logged("AddMarker")] == [30, 1, 1]
    res = h.op("add_markers", {"markers": [_mk(3, 300, dur=30)], "point_only": True})["result"]
    assert res["placed"][0]["dur"] == 1 and [x[4] for x in h.logged("AddMarker")][-1] == 1


def test_add_markers_needs_readable_timeline(h):
    h.init()
    h.fake.timeline.GetMarkers = None
    r = h.op("add_markers", {"markers": [_mk(1, 100)]})
    assert (r["ok"], r["error"], r["func"]) == (False, "markers_unreadable", "Timeline.GetMarkers")
    assert h.logged("AddMarker") == []


def test_get_markers_prefix_limit_and_total(h):
    h.init()
    _user_markers(h)
    h.op("add_markers", {"markers": [_mk(i, 100 + i * 10) for i in range(1, 4)]})
    h.op("add_markers", {"markers": [_mk(1, 500, custom="aih:P2:1")]})
    r = h.op("get_markers", {"prefix": "aih:P1:", "limit": 2})["result"]
    assert [m["custom"] for m in r["markers"]] == ["aih:P1:1", "aih:P1:2"] and r["total"] == 3
    r = h.op("get_markers", {"prefix": "aih:", "limit": 0})["result"]
    assert r["markers"] == [] and r["total"] == 4
    r = h.op("get_markers")["result"]
    assert r["total"] == 11 and len(r["markers"]) == 11
    assert h.op("get_markers", {"prefix": 5})["error"] == "bad_args"


def test_prefix_delete_never_touches_user_markers(h):
    h.init()
    user = _user_markers(h)
    h.op("add_marker", {"frame": 20, "name": "시험", "custom": "aih_test"})
    h.op("add_markers", {"markers": [_mk(i, 100 + i * 10) for i in range(1, 4)]})
    h.op("add_markers", {"markers": [_mk(1, 500, custom="aih:P2:1", color="Red")]})
    r = h.op("delete_markers", {"prefix": "aih:P1:"})["result"]
    assert (r["deleted"], r["deleted_count"], r["matched"], r["remaining"]) == (True, 3, 3, 0)
    assert r["remaining_ours"] == 2  # aih:P2:1과 aih_test
    assert "snapshot" not in r
    assert sorted(x[0] for x in h.logged("DeleteMarkerAtFrame")) == [110, 120, 130]
    assert h.logged("DeleteMarkerByCustomData") == []
    # 색으로 거르기: 빨간 aih: 표시만
    r = h.op("delete_markers", {"prefix": "aih:", "colors": ["Blue"]})["result"]
    assert r["deleted_count"] == 0
    r = h.op("delete_markers", {"prefix": "aih:", "colors": ["Red"], "snapshot": True})["result"]
    assert r["deleted_count"] == 1 and r["snapshot"][0]["custom"] == "aih:P2:1" and r["snapshot_truncated"] is False
    left = {m["frame"]: m["custom"] for m in _markers(h)}
    assert left == {**user, 20: "aih_test"}  # 사용자 표시와 옛 시험 표시는 그대로


@pytest.mark.parametrize("args", [
    {"prefix": ""}, {"prefix": "a"}, {"prefix": "aih"}, {"prefix": "AIH:"}, {"prefix": "user"},
    {"prefix": "aih: x"}, {"prefix": 5}, {"prefix": "aih:", "custom": "aih:P1:1"}, {"prefix": "aih:", "colors": ["Orange"]},
    {"prefix": "aih:", "colors": "Blue"},
])
def test_prefix_delete_refuses_bad_args_before_any_change(h, args):
    h.init()
    user = _user_markers(h)
    r = h.op("delete_markers", args)
    assert r["ok"] is False and r["error"] == "bad_args", r
    assert h.logged("DeleteMarkerAtFrame") == [] and h.logged("DeleteMarkerByCustomData") == []
    assert {m["frame"]: m["custom"] for m in _markers(h)} == user


def test_no_delete_request_removes_a_user_marker(h):
    """삭제에 쓰일 수 있는 모든 요청 모양을 보내도 사용자 표시는 하나도 지워지지 않는다."""
    h.init()
    user = _user_markers(h)
    tries = [
        {"prefix": "aih:"}, {"prefix": "aih:P1:"}, {"prefix": "aih:", "colors": ["Green"]},
        {"custom": "aih_test"}, {"custom": "aih:P1:1"}, {"custom": ""}, {"custom": "user"}, {"custom": "aih"},
        {"custom": "AIH:P1:1"}, {"custom": " aih:P1:1"}, {"custom": "autosubs_1"}, {}, {"colors": ["Green"]},
        # 2.1c 대화의 지우기: 꼬리표 목록(customs)은 모두 우리 꼬리표여야 한다
        {"prefix": "aih:", "customs": ["autosubs_1"]}, {"prefix": "aih:", "customs": ["", "user"]},
        {"prefix": "aih:", "customs": [" aih:P1:1"]}, {"prefix": "aih:", "customs": ["AIH:P1:1"]},
        {"prefix": "aih:", "customs": ["aih"]}, {"prefix": "aih:", "customs": ["aih-P1-1"]},
        {"prefix": "aih:", "customs": ["aih:P1:1", "autosubs_1"]}, {"prefix": "aih:", "customs": "autosubs_1"},
        {"prefix": "aih:", "customs": []}, {"prefix": "aih:", "customs": ["aih:P1:1"], "snapshot": True},
        {"customs": ["aih:P1:1"]},
    ]
    for args in tries:
        h.op("delete_markers", args)
    h.op("remove_audio", {"track_name": TRACK_NAME, "switch_page": True})
    assert {m["frame"]: m["custom"] for m in _markers(h)} == user


def test_remove_audio_switches_to_edit_page_only_when_asked(h):
    ours = str(Path(h.files) / "aih_test_tone.wav")
    h.fake.add_clip("audio", 1, "원본", 0, 10, "C:\\Users\\문성\\Music\\a.wav")
    h.fake.add_clip("audio", 2, "ours", 0, 10, ours)
    h.fake.set_track_name("audio", 2, TRACK_NAME)
    h.init()
    h.fake.page = "fairlight"
    res = h.op("remove_audio", {"track_name": TRACK_NAME})
    assert (res["ok"], res["error"]) == (False, "need_edit_page")
    assert h.logged("OpenPage") == [] and h.logged("DeleteClips") == []
    r = h.op("remove_audio", {"track_name": TRACK_NAME, "switch_page": True})["result"]
    assert r["page"] == "fairlight" and r["switched_page"] is True and r["removed_tracks"] == 1
    assert [x[0] for x in h.logged("OpenPage")] == ["edit", "fairlight"]  # 원래 화면으로 돌아간다
    assert h.logged("DeleteClips")[0][1] == "edit"
    assert h.fake.page == "fairlight"


# ---------------------------------------------------------------------------
# 2.1c: 대화의 지우기 (delete_markers{prefix, customs}) · 재생 위치 옮기기 (jump_to)
# ---------------------------------------------------------------------------

def test_customs_delete_takes_only_the_listed_tags(h):
    h.init()
    user = _user_markers(h)
    h.op("add_markers", {"markers": [_mk(i, 100 + i * 10) for i in range(1, 4)]})
    r = h.op("delete_markers", {"prefix": "aih:", "customs": ["aih:P1:1", "aih:P1:3"], "snapshot": True})["result"]
    assert (r["deleted_count"], r["matched"], r["remaining"], r["remaining_ours"]) == (2, 2, 0, 1)
    assert sorted(x["custom"] for x in r["snapshot"]) == ["aih:P1:1", "aih:P1:3"]
    assert {x["frame"] for x in r["snapshot"]} == {110, 130} and r["snapshot"][0]["name"] == "쉼 1"
    assert sorted(x[0] for x in h.logged("DeleteMarkerAtFrame")) == [110, 130]
    left = {m["frame"]: m["custom"] for m in _markers(h)}
    assert left == {**user, 120: "aih:P1:2"}
    # 이미 없는 꼬리표만 주면 아무것도 지우지 않는다
    r = h.op("delete_markers", {"prefix": "aih:", "customs": ["aih:P1:1"]})["result"]
    assert r["deleted_count"] == 0 and r["matched"] == 0


@pytest.mark.parametrize("args", [
    {"prefix": "aih:", "customs": ["autosubs_1"]},
    {"prefix": "aih:", "customs": ["aih:P1:1", "user"]},
    {"prefix": "aih:P1:", "customs": ["aih:P2:1"]},  # prefix 밖의 꼬리표
    {"prefix": "aih:", "customs": []},
    {"prefix": "aih:", "customs": "aih:P1:1"},
    {"prefix": "aih:", "customs": [5]},
])
def test_customs_delete_refuses_foreign_or_bad_tags_before_any_change(h, args):
    h.init()
    user = _user_markers(h)
    h.op("add_markers", {"markers": [_mk(1, 100), _mk(1, 500, custom="aih:P2:1")]})
    r = h.op("delete_markers", args)
    assert r["ok"] is False and r["error"] == "bad_args" and r["func"] == "customs", r
    assert h.logged("DeleteMarkerAtFrame") == [] and h.logged("DeleteMarkerByCustomData") == []
    assert {m["frame"]: m["custom"] for m in _markers(h)} == {**user, 100: "aih:P1:1", 500: "aih:P2:1"}


@pytest.mark.parametrize("page", ["cut", "edit", "color", "fairlight", "deliver"])
def test_jump_to_moves_the_playhead_on_pages_with_a_playhead(h, page):
    h.init()
    h.fake.page = page
    r = h.op("jump_to", {"frame": 86400 + 300, "tc": "01:00:10:00"})
    assert r["ok"] is True, r
    res = r["result"]
    assert res["ok"] is True and res["readback_tc"] == "01:00:10:00" and res["requested_tc"] == "01:00:10:00"
    assert res["page"] == page and res["frame"] == 86400 + 300
    assert [x[0] for x in h.logged("SetCurrentTimecode")] == ["01:00:10:00"]
    assert h.logged("OpenPage") == [] and h.logged("AddMarker") == []  # 화면을 바꾸지도, 표시를 넣지도 않는다


@pytest.mark.parametrize("page", ["media", "fusion"])
def test_jump_to_refuses_media_and_fusion_pages_without_calling_resolve(h, page):
    h.init()
    h.fake.page = page
    res = h.op("jump_to", {"frame": 86400 + 300, "tc": "01:00:10:00"})["result"]
    assert (res["ok"], res["reason"], res["page"]) == (False, "page", page)
    assert h.logged("SetCurrentTimecode") == [] and h.logged("OpenPage") == []


@pytest.mark.parametrize("frame", [86400 - 1, 86400 + 9000, 0])
def test_jump_to_outside_the_timeline_is_refused(h, frame):
    h.init()
    res = h.op("jump_to", {"frame": frame, "tc": "01:00:10:00"})["result"]
    assert res["ok"] is False and res["reason"] == "outside"
    assert (res["start_frame"], res["end_frame"]) == (86400, 86400 + 9000)
    assert h.logged("SetCurrentTimecode") == []


@pytest.mark.parametrize("args,func", [
    ({"frame": 86700, "tc": "1:00:10"}, "tc"), ({"frame": 86700, "tc": "01:00:10:00;"}, "tc"),
    ({"frame": 86700}, "tc"), ({"frame": "86700", "tc": "01:00:10:00"}, "frame"),
    ({"frame": 86700.5, "tc": "01:00:10:00"}, "frame"), ({"tc": "01:00:10:00"}, "frame"),
])
def test_jump_to_bad_args(h, args, func):
    h.init()
    r = h.op("jump_to", args)
    assert (r["ok"], r["error"], r["func"]) == (False, "bad_args", func), r
    assert h.logged("SetCurrentTimecode") == []


def test_jump_to_reports_a_readback_that_did_not_move(h):
    h.init()
    tl = h.fake.timeline
    tl.SetCurrentTimecode = h.lua.eval("function(self, tc) return true end")  # 답은 true인데 움직이지 않는 판
    res = h.op("jump_to", {"frame": 86400 + 300, "tc": "01:00:20:00"})["result"]
    assert res["ok"] is False and res["set_result"] is True and res["readback_tc"] == "01:00:10:00"
