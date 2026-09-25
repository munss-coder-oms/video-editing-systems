--[[
AI 도우미 - 리졸브 연결 스크립트 (연결 시험판)

리졸브에서 Workspace(워크스페이스) → Scripts(스크립트) → AI_Helper_Connect를 누르면 시작한다.
리졸브를 켤 때마다 한 번 누른다. 옆에 뜨는 AI 도우미 창과 파일로 이야기를 주고받는다.

  AI 도우미 창 → 이 스크립트: 우편함 폴더의 request.lua (return {v=1,id=..,t=..,op="..",a={..}})
                             글자는 모두 UTF-8 바이트의 16진수로 온다.
  이 스크립트 → AI 도우미 창: Fusion 설정 "Global.AIHelper.Response" = "AIH1:<주인>:<id>:<JSON의 16진수>"
                             SavePrefs()로 Fusion.prefs 파일에 쓰면 창이 그 파일을 읽는다.

리졸브 21 무료판의 스크립트 메뉴 Lua(LuaJIT, Lua 5.1)에서는 io, require, package, ffi, debug,
os.execute, os.remove가 없다. 그래서 요청은 loadfile로 읽고, 답은 Fusion 설정으로 남긴다.

주의
- 이 스크립트를 .scriptlib나 fusion:Execute()로 자동 실행하면 안 된다
  (다른 프로그램이 그렇게 했다가 Fusion이 망가지고 리졸브가 꺼진 일이 있다). 메뉴로만 시작한다.
- 할 일이 없을 때는 설정을 쓰지 않는다 (리졸브가 꺼지는 중에 설정을 쓰다 꺼진 일이 있다).
- 리졸브 함수는 판마다 없거나 다르게 동작할 수 있어서 모두 pcall로 부르고,
  함수마다 결과("ok" / "err:<내용>" / "missing")를 calls에 적어 돌려준다.
- 우편함 경로와 스크립트 판은 설치할 때 AI 도우미가 아래 값을 채워 넣는다.
]]

local AIH = {}

AIH.VERSION = "@@SCRIPT_VERSION@@"
AIH.MAILBOX_HEX = "@@MAILBOX_HEX@@" -- 우편함 폴더 경로 (바이트의 16진수, 설치할 때 채움)
-- 같은 우편함의 긴 경로 (한글 사용자 이름이면 우편함은 짧은 이름 C:\Users\ABCDEF~1\...이다).
-- 리졸브가 파일 경로를 긴 이름으로 알려 줄 때 "우리 파일"인지 비교하는 데만 쓴다 (UTF-8, 없으면 빈 값).
AIH.MAILBOX_LONG_HEX = "@@MAILBOX_LONG_HEX@@"

AIH.BIN_NAME = "AI 도우미"      -- 미디어 풀에 만들 저장소(빈) 이름
AIH.MAX_HEX = 400000            -- 답(16진수)이 이보다 길면 too_large로 대신 답한다
AIH.MAX_ITEMS = 300             -- state: 종류(영상/오디오)마다 알려 줄 클립 수
AIH.MAX_MARKERS = 2000
AIH.FRESH_SECONDS = 15          -- 시작할 때 남아 있던 요청은 이 시간 안에 쓴 것만 실행
AIH.TICK = 0.1                  -- 요청 파일을 보는 간격(초)
AIH.OWNER_EVERY = 10            -- 이 횟수(약 1초)마다 주인 표시를 다시 본다
AIH.BUSY_CHECK = 300            -- 이 횟수(약 30초)마다 bmd.wait가 실제로 쉬는지 본다
AIH.MAX_DELETE = 200            -- 시험 표시를 지울 때 되풀이하는 최대 횟수
AIH.STALE_CLAIM_MS = 600000     -- Claim이 요청 번호보다 이만큼(10분) 넘게 크면 PC 시계가 뒤로 가기 전에 남은 값

local KEY_OWNER = "Global.AIHelper.Owner"
local KEY_CLAIM = "Global.AIHelper.Claim"
local KEY_RESPONSE = "Global.AIHelper.Response"

local OPS_ALLOWED = {
	ping = true, state = true, add_marker = true, get_markers = true, delete_markers = true,
	place_audio = true, remove_audio = true, stop = true,
}

local unpack = unpack or table.unpack

-- 실행 중 상태: 리졸브·Fusion 객체, 주인 표시, 우편함 경로, 마지막으로 본 요청 번호
local st = { last = 0 }
AIH.st = st

---------------------------------------------------------------------------
-- 16진수
---------------------------------------------------------------------------

local HEX_BYTE = {}
for b = 0, 255 do
	HEX_BYTE[b] = string.format("%02x", b)
end

function AIH.hex(s)
	local out = {}
	for i = 1, #s do
		out[i] = HEX_BYTE[string.byte(s, i)]
	end
	return table.concat(out)
end

-- 16진수가 아니면 nil
function AIH.unhex(h)
	if type(h) ~= "string" or #h % 2 ~= 0 or string.find(h, "[^0-9a-fA-F]") then
		return nil
	end
	return (string.gsub(h, "%x%x", function(p)
		return string.char(tonumber(p, 16))
	end))
end

---------------------------------------------------------------------------
-- JSON (답은 이 형식으로 만든 뒤 16진수로 바꿔 보낸다)
---------------------------------------------------------------------------

-- 값이 없다는 것을 JSON null로 꼭 적어야 할 때 쓰는 표시 (Lua nil은 표에 남지 않는다)
AIH.NULL = setmetatable({}, { __tostring = function() return "null" end })
local NULL = AIH.NULL

-- 비어 있어도 []로 적을 목록 표시
local ARRAY_MARK = setmetatable({}, { __mode = "k" })

function AIH.array(t)
	t = t or {}
	ARRAY_MARK[t] = true
	return t
end
local array = AIH.array

-- s의 i번째 바이트에서 시작하는 UTF-8 글자의 길이. 올바르지 않으면 0
local function is_cont(s, i)
	local c = string.byte(s, i)
	return c ~= nil and c >= 0x80 and c <= 0xBF
end

local function utf8_len(s, i, b)
	if b >= 0xC2 and b <= 0xDF then
		return is_cont(s, i + 1) and 2 or 0
	end
	local c = string.byte(s, i + 1)
	if c == nil then
		return 0
	end
	if b >= 0xE0 and b <= 0xEF then
		local lo, hi = 0x80, 0xBF
		if b == 0xE0 then lo = 0xA0 elseif b == 0xED then hi = 0x9F end
		if c < lo or c > hi then return 0 end
		return is_cont(s, i + 2) and 3 or 0
	end
	if b >= 0xF0 and b <= 0xF4 then
		local lo, hi = 0x80, 0xBF
		if b == 0xF0 then lo = 0x90 elseif b == 0xF4 then hi = 0x8F end
		if c < lo or c > hi then return 0 end
		return (is_cont(s, i + 2) and is_cont(s, i + 3)) and 4 or 0
	end
	return 0
end

-- UTF-8 글자는 그대로 두고 ", \, 제어 문자는 이스케이프한다.
-- 리졸브가 UTF-8이 아닌 바이트(예: 윈도우 한글 코드 페이지 경로)를 주면 그 바이트를 \u00XX로 적어
-- 파이썬이 JSON을 못 읽는 일이 없게 한다.
local function json_string(s)
	if not string.find(s, '[%z\1-\31"\\\128-\255]') then
		return '"' .. s .. '"'
	end
	local out, n, i, len = {}, 0, 1, #s
	while i <= len do
		local b = string.byte(s, i)
		local take, piece = 1, nil
		if b < 0x20 then
			piece = string.format("\\u%04x", b)
		elseif b == 0x22 then
			piece = '\\"'
		elseif b == 0x5C then
			piece = "\\\\"
		elseif b < 0x80 then
			piece = string.char(b)
		else
			local k = utf8_len(s, i, b)
			if k > 0 then
				piece, take = string.sub(s, i, i + k - 1), k
			else
				piece = string.format("\\u%04x", b)
			end
		end
		n = n + 1
		out[n] = piece
		i = i + take
	end
	return '"' .. table.concat(out) .. '"'
end

-- 정수를 글자로. 요청 번호(밀리초, 약 1.8조)는 32비트를 넘는데, LuaJIT 2.0의 %d는 윈도우에서
-- 32비트라 숫자가 깨진다. %.0f는 2^53까지 정확하고 어느 판에서나 같다.
local function int_text(x)
	return string.format("%.0f", x)
end
AIH.int_text = int_text

local function json_number(x)
	if x ~= x or x == math.huge or x == -math.huge then
		return "null"
	end
	if x == math.floor(x) and x > -2 ^ 53 and x < 2 ^ 53 then
		return int_text(x)
	end
	return string.format("%.17g", x)
end

local function key_text(k)
	if type(k) == "number" then
		return json_number(k)
	end
	return tostring(k)
end

-- 1..n이 빈틈없이 채워진 표면 n, 아니면 nil
local function array_length(t)
	local count, max = 0, 0
	for k in pairs(t) do
		if type(k) ~= "number" or k < 1 or k ~= math.floor(k) then
			return nil
		end
		count = count + 1
		if k > max then max = k end
	end
	if count == max then
		return count
	end
	return nil
end

local encode

local function encode_table(t, stack, depth)
	if stack[t] or depth > 40 then
		return "null" -- 자기 자신을 품은 표나 너무 깊은 표
	end
	stack[t] = true
	local parts = {}
	local n = array_length(t)
	local out
	if n ~= nil and (n > 0 or ARRAY_MARK[t]) then
		for i = 1, n do
			parts[i] = encode(t[i], stack, depth + 1)
		end
		out = "[" .. table.concat(parts, ",") .. "]"
	else
		local keys = {}
		for k in pairs(t) do
			keys[#keys + 1] = { text = key_text(k), key = k }
		end
		table.sort(keys, function(x, y) return x.text < y.text end)
		for i = 1, #keys do
			parts[i] = json_string(keys[i].text) .. ":" .. encode(t[keys[i].key], stack, depth + 1)
		end
		out = "{" .. table.concat(parts, ",") .. "}"
	end
	stack[t] = nil
	return out
end

encode = function(v, stack, depth)
	local tv = type(v)
	if v == nil or v == NULL then
		return "null"
	elseif tv == "boolean" then
		return v and "true" or "false"
	elseif tv == "number" then
		return json_number(v)
	elseif tv == "string" then
		return json_string(v)
	elseif tv == "table" then
		return encode_table(v, stack, depth)
	end
	return "null" -- 함수, 리졸브 객체(userdata) 등
end

function AIH.json(v)
	return encode(v, {}, 0)
end

---------------------------------------------------------------------------
-- 작은 도구
---------------------------------------------------------------------------

-- nil이면 JSON null (키가 빠지지 않게)
local function nz(v)
	if v == nil then
		return NULL
	end
	return v
end

local function short(msg)
	local s = tostring(msg)
	if #s > 200 then
		s = string.sub(s, 1, 200)
	end
	return s
end

-- 리스트처럼 쓰는 표를 1, 2, 3 ... 순서의 목록으로 바꾼다 (빈틈이 있어도 # 없이 안전하게)
local function seq(t)
	local out = {}
	if type(t) ~= "table" then
		return out
	end
	local keys = {}
	for k in pairs(t) do
		if type(k) == "number" then
			keys[#keys + 1] = k
		end
	end
	table.sort(keys)
	for i = 1, #keys do
		out[i] = t[keys[i]]
	end
	return out
end

local function now()
	local ok, t = pcall(function() return os.time() end)
	if ok and type(t) == "number" then
		return t
	end
	return nil
end

-- calls[name]에 결과를 적는다. 한 번이라도 실패했으면(ok가 아니면) 그 기록을 남긴다.
local function record(calls, name, status)
	local old = calls[name]
	if old == nil or old == "ok" then
		calls[name] = status
	end
end

-- 리졸브·Fusion 함수 하나를 안전하게 부른다: obj:method(...)
-- 돌려주는 값: 첫 결과, 성공 여부 (오류 없이 끝났는지. false를 돌려준 것도 성공으로 친다)
-- false를 돌려주면(리졸브가 거절) "ok:false"로 적는다. 결과를 쓰지 않는 함수(SetTrackName 등)가
-- 거절한 것도 보고서에서 보이게 하려는 것이다.
function AIH.call(calls, name, obj, method, ...)
	if obj == nil then
		return nil, false
	end
	local okm, fn = pcall(function() return obj[method] end)
	if not okm or fn == nil then
		record(calls, name, "missing")
		return nil, false
	end
	local n = select("#", ...)
	local args = { ... }
	local ok, res = pcall(fn, obj, unpack(args, 1, n))
	if ok then
		record(calls, name, res == false and "ok:false" or "ok")
		return res, true
	end
	record(calls, name, "err:" .. short(res))
	return nil, false
end
local call = AIH.call

-- 처리를 멈추고 오류로 답한다
local function fail(code, func)
	error({ code = code, func = func }, 0)
end

local function int_arg(v, name, min)
	if type(v) ~= "number" or v ~= v or v ~= math.floor(v) or v < (min or 0) or v >= 2 ^ 53 then
		fail("bad_args", name)
	end
	return v
end

local function str_arg(v, name, default)
	if v == nil and default ~= nil then
		return default
	end
	if type(v) ~= "string" then
		fail("bad_args", name)
	end
	return v
end

local function get_pref(key)
	return (call({}, "Fusion.GetPrefs", st.F, "GetPrefs", key))
end

local function set_pref(key, value)
	return (call({}, "Fusion.SetPrefs", st.F, "SetPrefs", key, value))
end

---------------------------------------------------------------------------
-- 경로 검사: 우편함의 files 폴더 안에 있는 .wav 파일만 리졸브에 넣는다
---------------------------------------------------------------------------

local function to_backslash(p)
	return (string.gsub(p, "/", "\\"))
end

-- base: 우편함 경로 (없으면 설치할 때 넣은 우편함)
function AIH.files_prefix(base)
	local b = string.gsub(to_backslash(base or st.mailbox or ""), "\\+$", "")
	return b .. "\\files\\"
end

function AIH.path_ok(p, base)
	base = base or st.mailbox
	if type(p) ~= "string" or p == "" then
		return false, "empty"
	end
	if not base or base == "" then
		return false, "no_mailbox"
	end
	if string.find(p, "%z") then
		return false, "nul"
	end
	local norm = to_backslash(p)
	local prefix = AIH.files_prefix(base)
	if #norm <= #prefix or string.lower(string.sub(norm, 1, #prefix)) ~= string.lower(prefix) then
		return false, "outside"
	end
	-- 앞부분(우편함)은 위에서 그대로 맞춰 보았으니 나머지 부분만 본다
	local rest = string.sub(norm, #prefix + 1)
	if string.find(rest, "..", 1, true) then
		return false, "dotdot"
	end
	if string.find(rest, ":", 1, true) then
		return false, "colon"
	end
	if string.lower(string.sub(norm, -4)) ~= ".wav" then
		return false, "not_wav"
	end
	return true
end

-- 이 스크립트가 넣은 파일인지 (지울 때, 다시 쓸 때). 가져올 때(ImportMedia)는 path_ok만 쓴다.
-- 짧은 이름(8.3)으로 가져온 파일을 리졸브가 긴 이름으로 알려 줄 수도 있어서 둘 다 받아 준다.
function AIH.ours(p)
	if AIH.path_ok(p) then
		return true
	end
	return st.mailbox_long ~= nil and AIH.path_ok(p, st.mailbox_long) == true
end

---------------------------------------------------------------------------
-- 리졸브 설정 값 읽기: GetSetting(이름)이 안 되면 GetSettings()[이름]
---------------------------------------------------------------------------

local function setting(calls, obj, cls, key)
	local v = call(calls, cls .. ".GetSetting", obj, "GetSetting", key)
	if v ~= nil and v ~= "" then
		return v, "get_setting"
	end
	local all = call(calls, cls .. ".GetSettings", obj, "GetSettings")
	if type(all) == "table" and all[key] ~= nil and all[key] ~= "" then
		return all[key], "get_settings"
	end
	return nil, nil
end

local function as_bool(v)
	if v == true or v == 1 or v == "1" or v == "true" or v == "True" then
		return true
	end
	if v == false or v == 0 or v == "0" or v == "false" or v == "False" then
		return false
	end
	return nil
end

local function project_of(calls)
	if st.R == nil then
		fail("no_resolve", "resolve")
	end
	local pm = call(calls, "Resolve.GetProjectManager", st.R, "GetProjectManager")
	if pm == nil then
		fail("no_project_manager", "Resolve.GetProjectManager")
	end
	return call(calls, "ProjectManager.GetCurrentProject", pm, "GetCurrentProject")
end

local function need_timeline(calls)
	local project = project_of(calls)
	if project == nil then
		fail("no_project", "ProjectManager.GetCurrentProject")
	end
	local tl = call(calls, "Project.GetCurrentTimeline", project, "GetCurrentTimeline")
	if tl == nil then
		fail("no_timeline", "Project.GetCurrentTimeline")
	end
	return project, tl
end

local function clip_path(calls, mpi)
	local p = call(calls, "MediaPoolItem.GetClipProperty", mpi, "GetClipProperty", "File Path")
	if type(p) == "string" then
		return p
	end
	return nil
end

---------------------------------------------------------------------------
-- 할 일 (화이트리스트)
---------------------------------------------------------------------------

local ops = {}
AIH.ops = ops

ops.ping = function(a, calls)
	local res = {
		script_version = AIH.VERSION,
		owner = st.owner,
		env = st.env,
		mailbox = st.mailbox,
		mailbox_long = nz(st.mailbox_long),
		lua_version = _VERSION,
		resolve_found = st.R ~= nil,
		calls = calls,
	}
	local okj, jv = pcall(function() return jit and jit.version end)
	if okj and type(jv) == "string" then
		res.jit_version = jv
	end
	res.product = nz(call(calls, "Resolve.GetProductName", st.R, "GetProductName"))
	res.resolve_version = nz(call(calls, "Resolve.GetVersionString", st.R, "GetVersionString"))
	res.profile_path = nz(call(calls, "Fusion.MapPath", st.F, "MapPath", "Profile:"))
	-- 저장하지 않은 설정을 다시 읽을 수 있는지 (두 번 눌렀을 때 먼저 켠 쪽이 멈추는 데 필요)
	res.owner_readback = call(calls, "Fusion.GetPrefs", st.F, "GetPrefs", KEY_OWNER) == st.owner
	return res
end

local function item_info(calls, item, track)
	local info = { track = track }
	info.name = call(calls, "TimelineItem.GetName", item, "GetName")
	info.start = call(calls, "TimelineItem.GetStart", item, "GetStart")
	info["end"] = call(calls, "TimelineItem.GetEnd", item, "GetEnd")
	info.duration = call(calls, "TimelineItem.GetDuration", item, "GetDuration")
	info.left_offset = call(calls, "TimelineItem.GetLeftOffset", item, "GetLeftOffset")
	info.source_start = call(calls, "TimelineItem.GetSourceStartFrame", item, "GetSourceStartFrame")
	local mpi = call(calls, "TimelineItem.GetMediaPoolItem", item, "GetMediaPoolItem")
	if mpi ~= nil then
		info.clip = call(calls, "MediaPoolItem.GetName", mpi, "GetName")
		info.path = clip_path(calls, mpi)
	end
	return info
end

ops.state = function(a, calls)
	local res = { calls = calls }
	local project = project_of(calls)
	if project == nil then
		res.project, res.timeline = NULL, NULL -- 창에서 "프로젝트를 열어 주세요"
		return res
	end
	res.project = call(calls, "Project.GetName", project, "GetName") or "" -- 이름을 못 읽어도 프로젝트는 있다
	local tl = call(calls, "Project.GetCurrentTimeline", project, "GetCurrentTimeline")
	if tl == nil then
		res.timeline = NULL -- 창에서 "타임라인을 열어 주세요"
		return res
	end
	res.timeline = call(calls, "Timeline.GetName", tl, "GetName") or ""
	res.start_frame = nz(call(calls, "Timeline.GetStartFrame", tl, "GetStartFrame"))
	res.end_frame = nz(call(calls, "Timeline.GetEndFrame", tl, "GetEndFrame"))
	res.start_tc = nz(call(calls, "Timeline.GetStartTimecode", tl, "GetStartTimecode"))
	res.current_tc = nz(call(calls, "Timeline.GetCurrentTimecode", tl, "GetCurrentTimecode"))

	-- 프레임 속도: 타임라인 설정 → 프로젝트 설정 순서로, 리졸브가 주는 글자 그대로
	local fps, how = setting(calls, tl, "Timeline", "timelineFrameRate")
	local source = fps ~= nil and ("timeline_" .. how) or nil
	if fps == nil then
		fps, how = setting(calls, project, "Project", "timelineFrameRate")
		source = fps ~= nil and ("project_" .. how) or nil
	end
	res.fps = fps ~= nil and tostring(fps) or NULL
	res.fps_source = source or NULL

	local df, df_how = setting(calls, tl, "Timeline", "timelineDropFrameTimecode")
	local df_source = df ~= nil and ("timeline_" .. df_how) or nil
	if df == nil then
		df, df_how = setting(calls, project, "Project", "timelineDropFrameTimecode")
		df_source = df ~= nil and ("project_" .. df_how) or nil
	end
	local drop = as_bool(df)
	if drop == nil then
		res.drop_frame = NULL
	else
		res.drop_frame = drop
	end
	res.drop_frame_raw = df ~= nil and tostring(df) or NULL
	res.drop_frame_source = df_source or NULL

	local counts = {}
	for _, kind in ipairs({ "video", "audio", "subtitle" }) do
		local n = call(calls, "Timeline.GetTrackCount", tl, "GetTrackCount", kind)
		counts[kind] = type(n) == "number" and n or NULL
	end
	res.tracks = counts

	res.items = { video = array(), audio = array() }
	for _, kind in ipairs({ "video", "audio" }) do
		local list = res.items[kind]
		local n = counts[kind]
		if type(n) == "number" then
			for track = 1, n do
				local items = seq(call(calls, "Timeline.GetItemListInTrack", tl, "GetItemListInTrack", kind, track))
				for i = 1, #items do
					if #list >= AIH.MAX_ITEMS then
						res.truncated = true
						break
					end
					list[#list + 1] = item_info(calls, items[i], track)
				end
			end
		end
	end
	local subs = 0
	if type(counts.subtitle) == "number" then
		for track = 1, counts.subtitle do
			subs = subs + #seq(call(calls, "Timeline.GetItemListInTrack", tl, "GetItemListInTrack", "subtitle", track))
		end
	end
	res.subtitle_items = subs
	res.truncated = res.truncated or false
	return res
end

ops.add_marker = function(a, calls)
	local frame = int_arg(a.frame, "frame", 0)
	local duration = a.duration == nil and 1 or int_arg(a.duration, "duration", 1)
	local color = str_arg(a.color, "color", "Yellow")
	local name = str_arg(a.name, "name", "")
	local note = str_arg(a.note, "note", "")
	local custom = str_arg(a.custom, "custom", "")
	local _, tl = need_timeline(calls)
	local tried = array()
	for k = 0, 5 do
		local f = frame + k
		tried[#tried + 1] = f
		local added, ok = call(calls, "Timeline.AddMarker", tl, "AddMarker", f, color, name, note, duration, custom)
		if added then
			return { added = true, frame = f, requested_frame = frame, tried = tried, calls = calls }
		end
		if not ok then
			break
		end
		-- false: 그 프레임에 이미 표시가 있을 때만 다음 프레임으로 옮겨 본다
		local markers = call(calls, "Timeline.GetMarkers", tl, "GetMarkers")
		if type(markers) == "table" and markers[f] == nil then
			break
		end
	end
	return { added = false, frame = frame, requested_frame = frame, tried = tried, calls = calls }
end

ops.get_markers = function(a, calls)
	local _, tl = need_timeline(calls)
	local markers = call(calls, "Timeline.GetMarkers", tl, "GetMarkers")
	local list = array()
	if type(markers) == "table" then
		local frames = {}
		for f in pairs(markers) do
			if type(f) == "number" then
				frames[#frames + 1] = f
			end
		end
		table.sort(frames)
		for i = 1, #frames do
			local m = markers[frames[i]]
			if type(m) == "table" and #list < AIH.MAX_MARKERS then
				list[#list + 1] = {
					frame = frames[i], color = m.color, name = m.name, note = m.note,
					duration = m.duration, custom = m.customData,
				}
			end
		end
	end
	return { markers = list, calls = calls }
end

-- custom data가 custom인 표시의 프레임 목록. GetMarkers를 못 쓰면 nil
local function marker_frames(calls, tl, custom, name)
	local markers = call(calls, name, tl, "GetMarkers")
	if type(markers) ~= "table" then
		return nil
	end
	local frames = {}
	for f, m in pairs(markers) do
		if type(f) == "number" and type(m) == "table" and m.customData == custom then
			frames[#frames + 1] = f
		end
	end
	table.sort(frames)
	return frames
end

ops.delete_markers = function(a, calls)
	local custom = str_arg(a.custom, "custom")
	if custom == "" then
		fail("bad_args", "custom")
	end
	local _, tl = need_timeline(calls)
	local before = marker_frames(calls, tl, custom, "Timeline.GetMarkers")
	-- DeleteMarkerByCustomData는 맞는 표시 가운데 첫 하나만 지운다. false가 나올 때까지 되풀이한다
	-- (늘 true만 주는 판이 있어도 끝없이 돌지 않게 MAX_DELETE번까지).
	local count = 0
	for _ = 1, AIH.MAX_DELETE do
		local done, ok = call(calls, "Timeline.DeleteMarkerByCustomData", tl, "DeleteMarkerByCustomData", custom)
		if not (ok and done) then
			break
		end
		count = count + 1
	end
	-- 그래도 남은 것은 프레임 위치로 하나씩 지운다 (DeleteMarkerByCustomData가 없거나 다르게 동작하는 판)
	local left = marker_frames(calls, tl, custom, "Timeline.GetMarkers.after")
	if left ~= nil and #left > 0 then
		for i = 1, #left do
			if call(calls, "Timeline.DeleteMarkerAtFrame", tl, "DeleteMarkerAtFrame", left[i]) then
				count = count + 1
			end
		end
		left = marker_frames(calls, tl, custom, "Timeline.GetMarkers.check")
	end
	-- 앞뒤로 다시 읽을 수 있으면 실제로 줄어든 수를 믿는다 (true만 주는 판이 있어도 수가 맞게)
	if before ~= nil and left ~= nil then
		count = math.max(0, #before - #left)
	end
	local remaining = NULL -- 다시 읽지 못하면 남았는지 모른다
	if left ~= nil then
		remaining = #left
	end
	return { deleted = count > 0, deleted_count = count, remaining = remaining, calls = calls }
end

-- 미디어 풀 맨 위 폴더 아래의 "AI 도우미" 저장소를 찾고, 없으면 만든다
local function find_bin(calls, pool)
	local root = call(calls, "MediaPool.GetRootFolder", pool, "GetRootFolder")
	if root == nil then
		fail("no_root_folder", "MediaPool.GetRootFolder")
	end
	local subs = seq(call(calls, "Folder.GetSubFolderList", root, "GetSubFolderList"))
	for i = 1, #subs do
		if call(calls, "Folder.GetName", subs[i], "GetName") == AIH.BIN_NAME then
			return subs[i]
		end
	end
	local bin = call(calls, "MediaPool.AddSubFolder", pool, "AddSubFolder", root, AIH.BIN_NAME)
	if bin == nil then
		fail("add_bin_failed", "MediaPool.AddSubFolder")
	end
	return bin
end

local function same_path(x, y)
	return type(x) == "string" and type(y) == "string"
		and string.lower(to_backslash(x)) == string.lower(to_backslash(y))
end

-- 저장소의 클립 경로(clip_p)가 가져오려는 파일(path, 경로 검사를 통과한 것)과 같은지.
-- 리졸브가 짧은 이름으로 가져온 파일을 긴 이름으로 알려 줄 수도 있어서 긴 이름으로도 맞춰 본다.
local function same_file(clip_p, path)
	if same_path(clip_p, path) then
		return true
	end
	if st.mailbox_long == nil or type(clip_p) ~= "string" then
		return false
	end
	local rest = string.sub(to_backslash(path), #AIH.files_prefix() + 1)
	return same_path(clip_p, AIH.files_prefix(st.mailbox_long) .. rest)
end

-- 가져온 클립이 리졸브에서 어떻게 보이는지 (경로, 길이 단위를 결과 파일에서 보려고)
local function clip_info(calls, mpi)
	local function prop(key)
		return nz(call(calls, "MediaPoolItem.GetClipProperty", mpi, "GetClipProperty", key))
	end
	return {
		name = nz(call(calls, "MediaPoolItem.GetName", mpi, "GetName")),
		path = prop("File Path"), frames = prop("Frames"), fps = prop("FPS"), duration = prop("Duration"),
	}
end

local function place_audio(a, calls, pool, tl, path, track_name, record_frame, frames)
	local bin = find_bin(calls, pool)
	call(calls, "MediaPool.SetCurrentFolder", pool, "SetCurrentFolder", bin)

	-- 같은 파일을 이미 넣어 두었으면 다시 가져오지 않는다
	local item, imported, found_by = nil, false, nil
	local clips = seq(call(calls, "Folder.GetClipList", bin, "GetClipList"))
	for i = 1, #clips do
		if same_file(clip_path(calls, clips[i]), path) then
			item, found_by = clips[i], "path"
			break
		end
	end
	if item == nil then
		local got = seq(call(calls, "MediaPool.ImportMedia", pool, "ImportMedia", { path }))
		item = got[1]
		if item ~= nil then
			imported = true
		else
			-- 이미 저장소에 있는 파일이면 빈 목록을 주는 판이 있다: 파일 이름으로 한 번 더 찾는다
			local base = string.lower(string.match(to_backslash(path), "([^\\]+)$") or "")
			clips = seq(call(calls, "Folder.GetClipList.after_import", bin, "GetClipList"))
			for i = 1, #clips do
				local nm = call(calls, "MediaPoolItem.GetName", clips[i], "GetName")
				if type(nm) == "string" and string.lower(nm) == base then
					local p = clip_path(calls, clips[i])
					if p == nil or AIH.ours(p) then
						item, found_by = clips[i], "name"
						break
					end
				end
			end
			if item == nil then
				fail("import_failed", "MediaPool.ImportMedia")
			end
		end
	end

	local added = call(calls, "Timeline.AddTrack", tl, "AddTrack", "audio", "stereo")
	if not added then
		fail("add_track_failed", "Timeline.AddTrack")
	end
	local index = call(calls, "Timeline.GetTrackCount", tl, "GetTrackCount", "audio")
	if type(index) ~= "number" or index < 1 then
		fail("track_count_failed", "Timeline.GetTrackCount")
	end
	local named = call(calls, "Timeline.SetTrackName", tl, "SetTrackName", "audio", index, track_name)
	-- 이름이 정말 바뀌었는지 다시 읽어 본다. 이름이 없으면 [시험 흔적 지우기]가 이 트랙을 찾지 못한다.
	local now_name = call(calls, "Timeline.GetTrackName", tl, "GetTrackName", "audio", index)
	local track_named
	if type(now_name) == "string" then
		track_named = now_name == track_name
	else
		track_named = named == true
	end

	local info = {
		mediaPoolItem = item, startFrame = 0, endFrame = frames - 1,
		mediaType = 2, trackIndex = index, recordFrame = record_frame,
	}
	local raw = call(calls, "MediaPool.AppendToTimeline", pool, "AppendToTimeline", { info })
	local placed = seq(raw)
	local retry = "not_needed"
	if #placed == 0 then
		-- 빈 답이어도 실제로는 들어갔을 수 있다. 새 트랙이 정말 비어 있을 때만
		-- mediaType(2 = 소리만)을 빼고 한 번 더 넣는다 (같은 소리를 두 번 겹쳐 넣지 않게).
		local now_raw, okl = call(calls, "Timeline.GetItemListInTrack", tl, "GetItemListInTrack", "audio", index)
		local now_items = seq(now_raw)
		if not okl or type(now_raw) ~= "table" then
			retry = "skipped_track_unknown"
		elseif #now_items > 0 then
			placed, retry = now_items, "skipped_already_placed"
		else
			info.mediaType = nil
			placed = seq(call(calls, "MediaPool.AppendToTimeline.no_media_type", pool, "AppendToTimeline", { info }))
			retry = "done"
		end
	end
	local res = {
		imported = imported, reused = not imported, found_by = nz(found_by),
		track_index = index, track_named = track_named, appended = #placed,
		append_raw_type = type(raw), append_retry = retry,
		clip = clip_info(calls, item), calls = calls,
	}
	if placed[1] ~= nil then
		res.item_start = call(calls, "TimelineItem.GetStart", placed[1], "GetStart")
		res.item_end = call(calls, "TimelineItem.GetEnd", placed[1], "GetEnd")
		-- trackIndex대로 들어갔는지 확인용 (없는 판이면 missing)
		res.item_track = call(calls, "TimelineItem.GetTrackTypeAndIndex", placed[1], "GetTrackTypeAndIndex")
	end
	return res
end

ops.place_audio = function(a, calls)
	local path = str_arg(a.path, "path")
	local good, why = AIH.path_ok(path)
	if not good then
		fail("bad_path:" .. why, "path_guard")
	end
	local track_name = str_arg(a.track_name, "track_name")
	local record_frame = int_arg(a.record_frame, "record_frame", 0)
	local frames = int_arg(a.frames, "frames", 1)
	local project, tl = need_timeline(calls)
	local pool = call(calls, "Project.GetMediaPool", project, "GetMediaPool")
	if pool == nil then
		fail("no_media_pool", "Project.GetMediaPool")
	end
	local previous = call(calls, "MediaPool.GetCurrentFolder", pool, "GetCurrentFolder")
	local ok, res = pcall(place_audio, a, calls, pool, tl, path, track_name, record_frame, frames)
	-- 사용자가 보고 있던 미디어 풀 폴더로 되돌린다
	if previous ~= nil then
		call(calls, "MediaPool.SetCurrentFolder.restore", pool, "SetCurrentFolder", previous)
	end
	if not ok then
		error(res, 0)
	end
	return res
end

ops.remove_audio = function(a, calls)
	local track_name = str_arg(a.track_name, "track_name")
	if track_name == "" then
		fail("bad_args", "track_name")
	end
	local _, tl = need_timeline(calls)
	local count = call(calls, "Timeline.GetTrackCount", tl, "GetTrackCount", "audio")
	local removed, skipped = 0, 0
	if type(count) == "number" then
		-- 뒤 트랙부터 지워야 앞 트랙 번호가 바뀌지 않는다
		for index = count, 1, -1 do
			if call(calls, "Timeline.GetTrackName", tl, "GetTrackName", "audio", index) == track_name then
				-- 이 트랙의 클립이 모두 우리가 넣은 파일일 때만 지운다 (사용자 트랙은 건드리지 않는다).
				-- 클립 목록을 못 읽으면(오류, 표가 아닌 답) 비어 있는지 알 수 없으므로 지우지 않는다.
				local raw, okl = call(calls, "Timeline.GetItemListInTrack", tl, "GetItemListInTrack", "audio", index)
				local ours = okl and type(raw) == "table"
				local items = ours and seq(raw) or {}
				for i = 1, #items do
					local mpi = call(calls, "TimelineItem.GetMediaPoolItem", items[i], "GetMediaPoolItem")
					if mpi == nil or not AIH.ours(clip_path(calls, mpi)) then
						ours = false
						break
					end
				end
				if ours then
					if #items > 0 then
						call(calls, "Timeline.DeleteClips", tl, "DeleteClips", items)
					end
					if call(calls, "Timeline.DeleteTrack", tl, "DeleteTrack", "audio", index) then
						removed = removed + 1
					else
						skipped = skipped + 1
					end
				else
					skipped = skipped + 1
				end
			end
		end
	end
	return { removed_tracks = removed, skipped = skipped, calls = calls }
end

ops.stop = function(a, calls)
	return { stopping = true }
end

---------------------------------------------------------------------------
-- 요청 처리와 답
---------------------------------------------------------------------------

local function decode_args(v, depth)
	if depth > 20 then
		error("too_deep", 0)
	end
	if type(v) == "string" then
		local s = AIH.unhex(v)
		if s == nil then
			error("not_hex", 0)
		end
		return s
	elseif type(v) == "table" then
		local out = {}
		for k, x in pairs(v) do
			out[k] = decode_args(x, depth + 1)
		end
		return out
	end
	return v
end

local function failure(code, func, extra)
	local p = { ok = false, sv = AIH.VERSION, error = code, func = func }
	for k, v in pairs(extra or {}) do
		p[k] = v
	end
	return p
end

-- 요청 하나를 처리해 답(표)을 만든다. 두 번째 값이 true면 반복을 끝낸다.
function AIH.handle(r)
	if type(r) ~= "table" or r.v ~= 1 then
		return failure("bad_version", "request")
	end
	if type(r.op) ~= "string" or not OPS_ALLOWED[r.op] then
		return failure("unknown_op", "request")
	end
	if r.a ~= nil and type(r.a) ~= "table" then
		return failure("bad_args", "request")
	end
	local okd, a = pcall(decode_args, r.a or {}, 0)
	if not okd then
		return failure("bad_args", "hex")
	end
	local calls = {}
	local ok, res = pcall(ops[r.op], a, calls)
	if ok then
		return { ok = true, sv = AIH.VERSION, result = res }, r.op == "stop"
	end
	if type(res) == "table" then
		return failure(tostring(res.code), tostring(res.func), { calls = calls })
	end
	return failure("lua_error", r.op, { detail = short(res), calls = calls })
end

-- 답을 Fusion 설정에 적고 파일(Fusion.prefs)로 저장한다
function AIH.response_value(id, payload)
	local h = AIH.hex(AIH.json(payload))
	if #h > AIH.MAX_HEX then
		h = AIH.hex(AIH.json(failure("too_large", "response", { size = #h })))
	end
	return "AIH1:" .. st.owner .. ":" .. int_text(id) .. ":" .. h
end

function AIH.respond(id, payload)
	set_pref(KEY_RESPONSE, AIH.response_value(id, payload))
	call({}, "Fusion.SavePrefs", st.F, "SavePrefs")
end

-- request.lua를 읽는다. 없거나 쓰는 중이면 nil
function AIH.read_request()
	local f = loadfile(st.req_path)
	if f == nil then
		return nil
	end
	setfenv(f, {}) -- 요청 파일 안에서는 아무 함수도 쓸 수 없게
	local ok, r = pcall(f)
	if ok and type(r) == "table" then
		return r
	end
	return nil
end

local function valid_id(id)
	return type(id) == "number" and id == math.floor(id) and id >= 1 and id < 2 ^ 53
end

-- 새 요청이면 처리하고 답한다. 두 번째 반복(다시 누른 스크립트)과 겹치지 않게 Claim으로 차지한다.
-- 돌려주는 값: 반복을 끝내야 하면 true
function AIH.process(r)
	if not valid_id(r.id) or r.id <= st.last then
		return false
	end
	st.last = r.id
	local claimed = tonumber(get_pref(KEY_CLAIM)) or 0
	-- 다른 반복이 이미 맡았다. 단 Claim이 요청 번호보다 10분 넘게 크면 다른 반복이 아니라
	-- PC 시계가 뒤로 가기 전에 저장된 값이다: 그대로 두면 시계가 따라잡을 때까지 아무 답도 못 한다.
	if r.id <= claimed and claimed - r.id <= AIH.STALE_CLAIM_MS then
		return false
	end
	set_pref(KEY_CLAIM, int_text(r.id))
	local okh, payload, stop = pcall(AIH.handle, r)
	if not okh then
		payload, stop = failure("lua_error", "handle", { detail = short(payload) }), false
	end
	AIH.respond(r.id, payload)
	return stop and true or false
end

-- 다른 곳에서 주인 표시를 바꿨으면(스크립트를 다시 누름) false
function AIH.still_owner()
	local v = get_pref(KEY_OWNER)
	-- 읽지 못하면(nil) 계속한다. 이때는 Claim이 두 번 처리를 막는다.
	return not (type(v) == "string" and v ~= "" and v ~= st.owner)
end

---------------------------------------------------------------------------
-- 시작
---------------------------------------------------------------------------

local function has(fn)
	local ok, v = pcall(fn)
	return ok and v ~= nil
end

function AIH.probe_env()
	local wait = st.bmd_wait
	return {
		loadfile = type(loadfile) == "function",
		setfenv = type(setfenv) == "function",
		os_getenv = has(function() return os.getenv end),
		os_time = has(function() return os.time end),
		bmd_wait = wait ~= nil,
		io = has(function() return io end),
		require = has(function() return require end),
		ffi = has(function() return ffi end),
		os_execute = has(function() return os.execute end),
		dofile = type(dofile) == "function",
	}
end

local function make_owner()
	local seed = now() or 0
	local okc, c = pcall(function() return os.clock() end)
	if okc and type(c) == "number" then
		seed = seed + math.floor(c * 1000)
	end
	-- 같은 초에 두 번 눌러도 다른 값이 나오게 새 표의 메모리 주소도 섞는다
	local addr = string.match(tostring({}), "(%x+)$")
	if addr then
		seed = seed + (tonumber(string.sub(addr, -7), 16) or 0)
	end
	math.randomseed(seed % 2147483647)
	math.random()
	return "o" .. int_text(math.floor(now() or 0)) .. "_" .. int_text(math.random(1, 999999999))
end

-- 리졸브·Fusion을 찾고 주인 표시를 남긴다. 실패하면 false와 이유
function AIH.init()
	local okr, R = pcall(function()
		return resolve or (Resolve and Resolve()) or (bmd and bmd.scriptapp and bmd.scriptapp("Resolve"))
	end)
	st.R = okr and R or nil
	local okf, F = pcall(function()
		return fusion or fu or (st.R and st.R:Fusion())
	end)
	st.F = okf and F or nil
	if st.F == nil or not has(function() return st.F.SetPrefs end) then
		return false, "no_fusion" -- 답을 남길 곳이 없다
	end
	local okw, wait = pcall(function() return bmd.wait end)
	st.bmd_wait = okw and wait or nil
	st.env = AIH.probe_env()
	st.owner = make_owner()
	st.last = 0

	st.mailbox = AIH.unhex(AIH.MAILBOX_HEX)
	local long = AIH.unhex(AIH.MAILBOX_LONG_HEX) -- 채워지지 않은 틀(@@...)이면 nil
	if long ~= nil and long ~= "" and long ~= st.mailbox then
		st.mailbox_long = long
	else
		st.mailbox_long = nil
	end
	local fatal = nil
	if not st.env.loadfile then
		fatal = "no_loadfile"
	elseif not st.env.setfenv then
		fatal = "no_setfenv"
	elseif not st.env.bmd_wait then
		fatal = "no_bmd_wait" -- 쉬지 않고 도는 반복은 리졸브를 멈추게 하므로 시작하지 않는다
	elseif st.mailbox == nil or st.mailbox == "" then
		fatal = "bad_mailbox" -- 설치할 때 경로가 채워지지 않았다
	end
	if fatal then
		AIH.respond(0, failure(fatal, "env", { env = st.env }))
		return false, fatal
	end

	local mailbox = st.mailbox
	local sep = "\\"
	if not string.find(mailbox, "\\", 1, true) and string.find(mailbox, "/", 1, true) then
		sep = "/"
	end
	mailbox = string.gsub(mailbox, "[\\/]+$", "")
	st.req_path = mailbox .. sep .. "request.lua"

	set_pref(KEY_OWNER, st.owner) -- 먼저 켜 둔 반복은 이것을 보고 끝난다 (저장은 하지 않음)
	return true
end

function AIH.main()
	if not AIH.init() then
		return
	end
	-- 시작할 때 남아 있던 요청: 방금 쓴 것만 처리하고, 오래된 것(지난번 실행)은 본 것으로 친다
	local r = AIH.read_request()
	if r ~= nil and valid_id(r.id) then
		local t = now()
		if t ~= nil and type(r.t) == "number" and math.abs(t - r.t) <= AIH.FRESH_SECONDS then
			local ok, stop = pcall(AIH.process, r)
			if ok and stop then
				return
			end
		else
			st.last = r.id
		end
	end

	local wait = st.bmd_wait
	local tick = 0
	local checked_at = now()
	while true do
		wait(AIH.TICK)
		tick = tick + 1
		if tick % AIH.OWNER_EVERY == 0 and not AIH.still_owner() then
			break
		end
		-- bmd.wait가 쉬지 않고 바로 돌아오면(300번이 1초도 안 걸림) 리졸브가 멈출 수 있으니 끝낸다
		if tick % AIH.BUSY_CHECK == 0 then
			local t = now()
			if t ~= nil and checked_at ~= nil and t - checked_at < 1 then
				AIH.respond(0, failure("wait_not_waiting", "bmd.wait"))
				break
			end
			checked_at = t
		end
		local ok, req = pcall(AIH.read_request)
		if ok and req ~= nil then
			local okp, stop = pcall(AIH.process, req)
			if okp and stop then
				break
			end
		end
	end
end

if AIH_TEST_NO_MAIN then
	AIH_EXPORT = AIH
else
	AIH.main()
end
