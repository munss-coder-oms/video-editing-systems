--[[
AI 도우미 - 리졸브 연결 스크립트 (1.1.0)

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
- 유료판(Studio) 전용 함수(AIH.DENY)는 부르지 않는다. 무료판에서 부르면 리졸브에 안내 창이 떠서
  그 뒤의 요청이 모두 멈춘 일이 있다.
- 우리가 넣은 것에는 꼬리표를 붙이고(표시의 custom data "aih:", 트랙 이름 "AI ...",
  미디어 풀 폴더 "AI 도우미", 점검용 타임라인 이름 "AI 도우미 점검용 ..."), 지울 때는 꼬리표가 있는 것만 지운다.
- 우편함 경로와 스크립트 판은 설치할 때 AI 도우미가 아래 값을 채워 넣는다.
]]

local AIH = {}

AIH.VERSION = "@@SCRIPT_VERSION@@"
AIH.MAILBOX_HEX = "@@MAILBOX_HEX@@" -- 우편함 폴더 경로 (바이트의 16진수, 설치할 때 채움)
-- 같은 우편함의 긴 경로 (한글 사용자 이름이면 우편함은 짧은 이름 C:\Users\ABCDEF~1\...이다).
-- 리졸브가 파일 경로를 긴 이름으로 알려 줄 때 "우리 파일"인지 비교하는 데만 쓴다 (UTF-8, 없으면 빈 값).
AIH.MAILBOX_LONG_HEX = "@@MAILBOX_LONG_HEX@@"

AIH.BIN_NAME = "AI 도우미"      -- 미디어 풀에 만들 저장소(빈) 이름
AIH.PROBE_PREFIX = "AI 도우미 점검용" -- 기능 점검 때 잠깐 만드는 복사본 타임라인 이름의 앞부분
AIH.PROBE_TRACK = "AI 도우미 점검" -- 기능 점검(C7)이 복사본에 만드는 오디오 트랙 이름
AIH.MAX_HEX = 400000            -- 답(16진수)이 이보다 길면 too_large로 대신 답한다
AIH.MAX_ITEMS = 300             -- state: 종류(영상/오디오)마다 알려 줄 클립 수
AIH.MAX_MARKERS = 2000
AIH.PAGE_ITEMS = 100            -- timeline_items: 한 번에 알려 줄 클립 수
AIH.PAGE_BYTES = 60000          -- timeline_items: 한 번의 답(JSON)이 이보다 크지 않게 (64KB 아래)
AIH.MAX_MAPPING = 4096          -- probe_read: 오디오 연결 정보(JSON 글자)를 이만큼만
AIH.FRESH_SECONDS = 15          -- 시작할 때 남아 있던 요청은 이 시간 안에 쓴 것만 실행
AIH.TICK = 0.1                  -- 요청 파일을 보는 간격(초)
AIH.OWNER_EVERY = 10            -- 이 횟수(약 1초)마다 주인 표시를 다시 본다
AIH.BUSY_CHECK = 300            -- 이 횟수(약 30초)마다 bmd.wait가 실제로 쉬는지 본다
AIH.MAX_DELETE = 2000           -- 표시를 지울 때 되풀이하는 최대 횟수
AIH.MAX_ADD = 100               -- add_markers: 한 번에 넣는 표시 수
AIH.MARKER_SHIFT = 5            -- 그 프레임에 이미 표시가 있으면 이만큼까지 뒤로 옮겨 본다
AIH.MAX_NAME = 40               -- 표시 이름 글자 수
AIH.MAX_NOTE = 200              -- 표시 메모 글자 수
AIH.MAX_SNAPSHOT = 200          -- delete_markers(prefix)가 지운 표시를 적어 돌려주는 최대 수
-- 리졸브 표시 색 이름 16개 (이 밖의 이름은 받지 않는다)
AIH.MARKER_COLORS = {
	Blue = true, Cyan = true, Green = true, Yellow = true, Red = true, Pink = true, Purple = true,
	Fuchsia = true, Rose = true, Lavender = true, Sky = true, Mint = true, Lemon = true, Sand = true,
	Cocoa = true, Cream = true,
}
AIH.STALE_CLAIM_MS = 600000     -- Claim이 요청 번호보다 이만큼(10분) 넘게 크면 PC 시계가 뒤로 가기 전에 남은 값

local KEY_OWNER = "Global.AIHelper.Owner"
local KEY_CLAIM = "Global.AIHelper.Claim"
local KEY_RESPONSE = "Global.AIHelper.Response"

-- 할 일 목록 (앱의 engine/resolve_link/protocol.py OPS와 같아야 한다: 시험이 맞춰 본다)
local OPS_ALLOWED = {
	ping = true, state = true, timeline_info = true, timeline_items = true, scope = true,
	probe_read = true, probe_copy = true, switch_timeline = true,
	add_marker = true, add_markers = true, get_markers = true, delete_markers = true,
	place_audio = true, remove_audio = true, stop = true,
}

-- 유료판(Studio) 전용이거나 부르면 안내 창이 뜰 수 있는 함수. AIH.call은 이 이름을 부르지 않고
-- "err:denied"로만 적는다. 이 이름들은 이 표 밖에서 쓰지 않는다 (시험이 스크립트 글을 뒤져 확인한다).
AIH.DENY = {
	CreateSubtitlesFromAudio = true, TranscribeAudio = true, ClearTranscription = true,
	GetTranscription = true, PerformAudioClassification = true, ClearAudioClassification = true,
	AnalyzeForIntellisearch = true, AnalyzeForSlate = true, GenerateSpeech = true,
	RemoveMotionBlur = true, SmartReframe = true, CreateMagicMask = true, RegenerateMagicMask = true,
	SetVoiceIsolationState = true, GetVoiceIsolationState = true, EnableVoiceIsolationState = true,
	ConvertTimelineToStereo = true, AnalyzeDolbyVision = true,
	GetStereoConvergenceValues = true, GetStereoLeftFloatingWindowParams = true,
	GetStereoRightFloatingWindowParams = true, SetStereoConvergenceValues = true,
}

-- 재생 위치를 옮길 수 있는 화면 (SetCurrentTimecode 설명서)
local PLAYHEAD_PAGES = { cut = true, edit = true, color = true, fairlight = true, deliver = true }

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
	if AIH.DENY[method] then
		record(calls, name, "err:denied") -- 부르지 않는다
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

-- 목록 인자: 1..n 목록이고 max개 이하일 때만
local function list_arg(v, name, max)
	if type(v) ~= "table" then
		fail("bad_args", name)
	end
	local out, count = seq(v), 0
	for _ in pairs(v) do
		count = count + 1
	end
	if count ~= #out or #out > max then
		fail("bad_args", name)
	end
	return out
end
AIH.list_arg = list_arg

-- true/false가 아니면 JSON null
local function bool_or_null(v)
	if type(v) == "boolean" then
		return v
	end
	return NULL
end

-- 바꾸는 함수의 결과: 오류면 "error", 아니면 true/false (그 밖의 답은 null)
local function call_result(v, ok)
	if not ok then
		return "error"
	end
	return bool_or_null(v)
end

-- 우리가 붙인 표시 꼬리표인지: "aih:"로 시작하거나 1차 시험판이 쓴 "aih_test"
local function our_custom(c)
	return type(c) == "string" and (string.sub(c, 1, 4) == "aih:" or c == "aih_test")
end
AIH.our_custom = our_custom

-- 표시를 넣을 때 쓰는 꼬리표: "aih:" 뒤에 영문·숫자·:·_·- 만 (예: aih:P1a2b3:12)
local function tag_ok(c)
	return type(c) == "string" and #c <= 64 and string.find(c, "^aih:[%w:_%-]+$") ~= nil
end
AIH.tag_ok = tag_ok

-- 지울 때 쓰는 꼬리표 앞부분: "aih:"로 시작해야 한다 (그 뒤는 비어 있어도 된다)
local function prefix_ok(c)
	return type(c) == "string" and #c <= 64 and string.find(c, "^aih:[%w:_%-]*$") ~= nil
end
AIH.prefix_ok = prefix_ok

-- UTF-8 글자 수 (이어지는 바이트 0x80~0xBF는 세지 않는다)
local function char_count(s)
	local n = 0
	for i = 1, #s do
		local b = string.byte(s, i)
		if b < 0x80 or b >= 0xC0 then
			n = n + 1
		end
	end
	return n
end
AIH.char_count = char_count

local function is_probe_name(name)
	return type(name) == "string" and string.sub(name, 1, #AIH.PROBE_PREFIX) == AIH.PROBE_PREFIX
end
AIH.is_probe_name = is_probe_name

-- 지금 리졸브 화면 (edit, fairlight ...). 알 수 없으면 nil
local function current_page(calls)
	local p = call(calls, "Resolve.GetCurrentPage", st.R, "GetCurrentPage")
	if type(p) == "string" and p ~= "" then
		return p
	end
	return nil
end

local function uid_of(calls, name, obj)
	local u = call(calls, name, obj, "GetUniqueId")
	if type(u) == "string" and u ~= "" then
		return u
	end
	return nil
end

-- 타임라인의 번호(uid)와 이름
local function timeline_ident(calls, tl)
	local uid = uid_of(calls, "Timeline.GetUniqueId", tl)
	local name = call(calls, "Timeline.GetName", tl, "GetName")
	if type(name) ~= "string" then
		name = nil
	end
	return uid, name
end

-- 번호가 둘 다 있으면 번호로, 아니면 이름으로 같은 타임라인인지 본다
local function same_timeline(uid, name, want_uid, want_name)
	if uid ~= nil and want_uid ~= nil then
		return uid == want_uid
	end
	return name ~= nil and want_name ~= nil and name == want_name
end

-- 프로젝트에서 번호(또는 이름)가 맞는 타임라인을 찾는다. 없으면 nil
local function find_timeline(calls, project, want_uid, want_name)
	local n = call(calls, "Project.GetTimelineCount", project, "GetTimelineCount")
	if type(n) ~= "number" then
		return nil
	end
	for i = 1, n do
		local tl = call(calls, "Project.GetTimelineByIndex", project, "GetTimelineByIndex", i)
		if tl ~= nil then
			local uid, name = timeline_ident(calls, tl)
			if same_timeline(uid, name, want_uid, want_name) then
				return tl
			end
		end
	end
	return nil
end

-- 프레임 속도와 드롭 프레임: 타임라인 설정 → 프로젝트 설정 순서로, 리졸브가 주는 글자 그대로
local function timeline_rate(calls, project, tl, res)
	local fps, how = setting(calls, tl, "Timeline", "timelineFrameRate")
	local source = fps ~= nil and ("timeline_" .. how) or nil
	if fps == nil and project ~= nil then
		fps, how = setting(calls, project, "Project", "timelineFrameRate")
		source = fps ~= nil and ("project_" .. how) or nil
	end
	res.fps = fps ~= nil and tostring(fps) or NULL
	res.fps_source = source or NULL

	local df, df_how = setting(calls, tl, "Timeline", "timelineDropFrameTimecode")
	local df_source = df ~= nil and ("timeline_" .. df_how) or nil
	if df == nil and project ~= nil then
		df, df_how = setting(calls, project, "Project", "timelineDropFrameTimecode")
		df_source = df ~= nil and ("project_" .. df_how) or nil
	end
	res.drop_frame = bool_or_null(as_bool(df))
	res.drop_frame_raw = df ~= nil and tostring(df) or NULL
	res.drop_frame_source = df_source or NULL
	return res
end

-- GetMarkInOut 답을 숫자만 남긴 표로. 아무것도 없으면 null
local function in_out_table(v)
	if type(v) ~= "table" then
		return NULL
	end
	local out, any = {}, false
	for _, k in ipairs({ "video", "audio" }) do
		local x = v[k]
		if type(x) == "table" then
			local i, o = x["in"], x.out
			if type(i) == "number" or type(o) == "number" then
				out[k] = { ["in"] = type(i) == "number" and i or NULL, out = type(o) == "number" and o or NULL }
				any = true
			end
		end
	end
	if not any then
		return NULL
	end
	return out
end

---------------------------------------------------------------------------
-- 타임라인 지문: 클립·트랙·(우리 것이 아닌) 표시를 줄 글로 적어 정렬한 뒤 두 가지 해시로 줄인다.
-- 점검용 복사본을 지우기 전에 "점검 때와 같은지" 맞춰 보는 데 쓴다. 읽지 못하면 nil (그러면 지우지 않는다)
---------------------------------------------------------------------------

local TWO32 = 4294967296

local function hash_rows(rows)
	local h1, h2 = 17, 5381
	for r = 1, #rows do
		local s = rows[r] .. "\n"
		for i = 1, #s do
			local b = string.byte(s, i)
			h1 = (h1 * 31 + b) % TWO32
			h2 = (h2 * 131 + b + 7) % TWO32
		end
	end
	return int_text(#rows) .. ":" .. int_text(h1) .. ":" .. int_text(h2)
end
AIH.hash_rows = hash_rows

local function text_of(v)
	if v == nil or v == NULL then
		return ""
	end
	if type(v) == "number" then
		return json_number(v)
	end
	return tostring(v)
end

function AIH.fingerprint(tl)
	if tl == nil then
		return nil
	end
	local calls = {} -- 지문 읽기는 답의 calls에 섞지 않는다
	local rows = {}
	for _, kind in ipairs({ "video", "audio" }) do
		local n = call(calls, "Timeline.GetTrackCount", tl, "GetTrackCount", kind)
		if type(n) ~= "number" then
			return nil
		end
		for track = 1, n do
			local raw, ok = call(calls, "Timeline.GetItemListInTrack", tl, "GetItemListInTrack", kind, track)
			if not ok or type(raw) ~= "table" then
				return nil
			end
			local items = seq(raw)
			local en = call(calls, "Timeline.GetIsTrackEnabled", tl, "GetIsTrackEnabled", kind, track)
			rows[#rows + 1] = "T|" .. kind .. "|" .. track .. "|" .. #items .. "|" .. text_of(en)
			for i = 1, #items do
				local it = items[i]
				local mpi = call(calls, "TimelineItem.GetMediaPoolItem", it, "GetMediaPoolItem")
				rows[#rows + 1] = table.concat({
					"I", kind, tostring(track),
					text_of(uid_of(calls, "TimelineItem.GetUniqueId", it)),
					text_of(call(calls, "TimelineItem.GetStart", it, "GetStart")),
					text_of(call(calls, "TimelineItem.GetEnd", it, "GetEnd")),
					text_of(call(calls, "TimelineItem.GetLeftOffset", it, "GetLeftOffset")),
					text_of(mpi ~= nil and clip_path(calls, mpi) or nil),
					text_of(call(calls, "TimelineItem.GetClipEnabled", it, "GetClipEnabled")),
				}, "|")
			end
		end
	end
	local markers = call(calls, "Timeline.GetMarkers", tl, "GetMarkers")
	if type(markers) == "table" then
		for f, m in pairs(markers) do
			if type(m) == "table" and not our_custom(m.customData) then
				rows[#rows + 1] = "M|" .. text_of(f) .. "|" .. text_of(m.color) .. "|" .. text_of(m.duration)
					.. "|" .. text_of(m.name) .. "|" .. text_of(m.customData)
			end
		end
	else
		rows[#rows + 1] = "M|unreadable"
	end
	table.sort(rows)
	return hash_rows(rows)
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
	res.product_version = res.resolve_version
	res.page = nz(current_page(calls))
	-- 이 스크립트가 할 수 있는 일 목록: 창이 예전 스크립트인지 알아보는 데 쓴다
	local names = {}
	for name in pairs(OPS_ALLOWED) do
		names[#names + 1] = name
	end
	table.sort(names)
	res.ops = array(names)
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

	timeline_rate(calls, project, tl, res)

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

-- 트랙 목록: 이름, 켜짐, 잠김, (오디오) 종류, 클립 수
local function track_list(calls, tl, kind)
	local list = array()
	local n = call(calls, "Timeline.GetTrackCount", tl, "GetTrackCount", kind)
	if type(n) ~= "number" then
		return NULL
	end
	for i = 1, n do
		local t = { index = i }
		t.name = nz(call(calls, "Timeline.GetTrackName", tl, "GetTrackName", kind, i))
		t.enabled = bool_or_null(call(calls, "Timeline.GetIsTrackEnabled", tl, "GetIsTrackEnabled", kind, i))
		t.locked = bool_or_null(call(calls, "Timeline.GetIsTrackLocked", tl, "GetIsTrackLocked", kind, i))
		if kind == "audio" then
			t.subtype = nz(call(calls, "Timeline.GetTrackSubType", tl, "GetTrackSubType", kind, i))
		end
		local raw, ok = call(calls, "Timeline.GetItemListInTrack", tl, "GetItemListInTrack", kind, i)
		t.count = (ok and type(raw) == "table") and #seq(raw) or NULL
		list[#list + 1] = t
	end
	return list
end

local function probe_record()
	local p = st.probe
	if p == nil then
		return NULL
	end
	return {
		original_uid = nz(p.original_uid), original_name = nz(p.original_name),
		copy_uid = nz(p.copy_uid), copy_name = nz(p.copy_name),
	}
end

-- 읽기만: 프로젝트·타임라인 이름과 번호, 길이, 프레임 속도, 화면, 트랙마다 이름·켜짐·잠김·클립 수
ops.timeline_info = function(a, calls)
	local res = { calls = calls, page = nz(current_page(calls)) }
	local project = project_of(calls)
	if project == nil then
		res.project, res.timeline = NULL, NULL
		return res
	end
	res.project = call(calls, "Project.GetName", project, "GetName") or ""
	res.project_uid = nz(uid_of(calls, "Project.GetUniqueId", project))
	res.timeline_count = nz(call(calls, "Project.GetTimelineCount", project, "GetTimelineCount"))
	local tl = call(calls, "Project.GetCurrentTimeline", project, "GetCurrentTimeline")
	if tl == nil then
		res.timeline = NULL
		return res
	end
	local uid, name = timeline_ident(calls, tl)
	res.timeline = name or ""
	res.timeline_uid = nz(uid)
	res.start_frame = nz(call(calls, "Timeline.GetStartFrame", tl, "GetStartFrame"))
	res.end_frame = nz(call(calls, "Timeline.GetEndFrame", tl, "GetEndFrame"))
	res.start_tc = nz(call(calls, "Timeline.GetStartTimecode", tl, "GetStartTimecode"))
	res.current_tc = nz(call(calls, "Timeline.GetCurrentTimecode", tl, "GetCurrentTimecode"))
	timeline_rate(calls, project, tl, res)
	res.tracks = {
		video = track_list(calls, tl, "video"),
		audio = track_list(calls, tl, "audio"),
		subtitle = track_list(calls, tl, "subtitle"),
	}
	res.is_probe_copy = is_probe_name(name)
	res.probe = probe_record()
	return res
end

-- 클립 한 줄 (timeline_items). end는 GetEnd 그대로 (끝 프레임은 들어가지 않는 값)
local function item_row(calls, item, track, kind)
	local info = { track = track, kind = kind }
	info.uid = nz(uid_of(calls, "TimelineItem.GetUniqueId", item))
	info.name = nz(call(calls, "TimelineItem.GetName", item, "GetName"))
	info.start = nz(call(calls, "TimelineItem.GetStart", item, "GetStart"))
	info["end"] = nz(call(calls, "TimelineItem.GetEnd", item, "GetEnd"))
	info.duration = nz(call(calls, "TimelineItem.GetDuration", item, "GetDuration"))
	info.left_offset = nz(call(calls, "TimelineItem.GetLeftOffset", item, "GetLeftOffset"))
	info.source_start = nz(call(calls, "TimelineItem.GetSourceStartFrame", item, "GetSourceStartFrame"))
	info.source_end = nz(call(calls, "TimelineItem.GetSourceEndFrame", item, "GetSourceEndFrame"))
	info.enabled = bool_or_null(call(calls, "TimelineItem.GetClipEnabled", item, "GetClipEnabled"))
	info.path, info.clip_fps, info.media_uid = NULL, NULL, NULL
	local mpi = call(calls, "TimelineItem.GetMediaPoolItem", item, "GetMediaPoolItem")
	if mpi ~= nil then
		info.path = nz(clip_path(calls, mpi))
		info.clip_fps = nz(call(calls, "MediaPoolItem.GetClipProperty.FPS", mpi, "GetClipProperty", "FPS"))
		info.media_uid = nz(uid_of(calls, "MediaPoolItem.GetUniqueId", mpi))
	end
	local linked = call(calls, "TimelineItem.GetLinkedItems", item, "GetLinkedItems")
	if type(linked) == "table" then
		local list, uids = seq(linked), array()
		for i = 1, math.min(#list, 8) do
			uids[#uids + 1] = nz(uid_of(calls, "TimelineItem.GetUniqueId.linked", list[i]))
		end
		info.linked_uids = uids
	else
		info.linked_uids = NULL
	end
	return info
end

-- 읽기만: 클립 목록을 나눠서 (한 번에 limit개, JSON PAGE_BYTES 이하). 다음 쪽이 있으면 next = 다음 offset
ops.timeline_items = function(a, calls)
	local kind = str_arg(a.kind, "kind", "audio")
	if kind ~= "audio" and kind ~= "video" then
		fail("bad_args", "kind")
	end
	local from = a.track_from == nil and 1 or int_arg(a.track_from, "track_from", 1)
	local to = nil
	if a.track_to ~= nil then
		to = int_arg(a.track_to, "track_to", 1)
	end
	local offset = a.offset == nil and 0 or int_arg(a.offset, "offset", 0)
	local limit = a.limit == nil and AIH.PAGE_ITEMS or int_arg(a.limit, "limit", 1)
	if limit > AIH.PAGE_ITEMS then
		fail("bad_args", "limit")
	end
	local _, tl = need_timeline(calls)
	local n = call(calls, "Timeline.GetTrackCount", tl, "GetTrackCount", kind)
	if type(n) ~= "number" then
		fail("track_count_failed", "Timeline.GetTrackCount")
	end
	if to == nil or to > n then
		to = n
	end
	local items, bytes, index, nxt = array(), 0, 0, NULL
	local track = from
	while track <= to and nxt == NULL do
		local list = seq(call(calls, "Timeline.GetItemListInTrack", tl, "GetItemListInTrack", kind, track))
		for i = 1, #list do
			if index >= offset then
				if #items >= limit then
					nxt = index
					break
				end
				local row = item_row(calls, list[i], track, kind)
				local size = #AIH.json(row) + 1
				if #items > 0 and bytes + size > AIH.PAGE_BYTES then
					nxt = index
					break
				end
				items[#items + 1] = row
				bytes = bytes + size
			end
			index = index + 1
		end
		track = track + 1
	end
	return {
		kind = kind, items = items, offset = offset, ["next"] = nxt,
		track_from = from, track_to = to, track_count = n, calls = calls,
	}
end

local function readable_tc(calls, tl)
	return nz(call(calls, "Timeline.GetCurrentTimecode", tl, "GetCurrentTimecode"))
end

-- 읽기만: 지금 화면, 재생 위치, In/Out, 고른 클립 (없는 함수는 null과 calls 기록)
ops.scope = function(a, calls)
	local res = { calls = calls, page = nz(current_page(calls)) }
	local project, tl = need_timeline(calls)
	local uid, name = timeline_ident(calls, tl)
	res.timeline, res.timeline_uid = nz(name), nz(uid)
	res.playhead_tc = readable_tc(calls, tl)
	res.start_tc = nz(call(calls, "Timeline.GetStartTimecode", tl, "GetStartTimecode"))
	res.start_frame = nz(call(calls, "Timeline.GetStartFrame", tl, "GetStartFrame"))
	timeline_rate(calls, project, tl, res)
	res.in_out = in_out_table(call(calls, "Timeline.GetMarkInOut", tl, "GetMarkInOut"))
	local sel, ok = call(calls, "Timeline.GetSelectedClips", tl, "GetSelectedClips")
	if ok and type(sel) == "table" then
		local list, uids = seq(sel), array()
		for i = 1, math.min(#list, 50) do
			uids[#uids + 1] = nz(uid_of(calls, "TimelineItem.GetUniqueId", list[i]))
		end
		res.selected_uids, res.selected_count = uids, #list
	else
		res.selected_uids, res.selected_count = NULL, NULL
	end
	return res
end

-- 기능 점검(읽기): 이 판에 있는 함수 이름. 이름으로 찾아보기만 하고 부르지 않는다
local PROBE_NAMES = {
	{ "Resolve", { "GetCurrentPage", "OpenPage", "GetProductName", "GetVersionString",
		"GetCurrentProject", "GetCurrentTimeline", "GetMediaPool", "GetFairlightPresets" } },
	{ "Project", { "GetUniqueId", "GetTimelineCount", "GetTimelineByIndex", "SetCurrentTimeline",
		"GetCurrentTimeline", "InsertAudioToCurrentTrackAtPlayhead", "GetAudioRenderFormats" } },
	{ "MediaPool", { "GetUniqueId", "ImportMedia", "AppendToTimeline", "DeleteClips", "DeleteTimelines",
		"CreateEmptyTimeline", "ImportTimelineFromFile", "GetSelectedClips" } },
	{ "Timeline", { "GetUniqueId", "DuplicateTimeline", "GetMarkInOut", "SetMarkInOut", "GetSelectedClips",
		"SetCurrentTimecode", "GetCurrentTimecode", "AddMarker", "DeleteMarkerAtFrame",
		"DeleteMarkerByCustomData", "GetMarkerByCustomData", "UpdateMarkerCustomData",
		"SetTrackEnable", "GetIsTrackEnabled", "SetTrackLock", "GetIsTrackLocked", "GetTrackSubType",
		"DeleteClips", "SetClipsLinked", "Export", "ImportIntoTimeline", "GetOutputBlanking",
		"GetNormalizeAudioModes", "NormalizeAudioLevel", "AutoAlignClips" } },
	{ "TimelineItem", { "GetUniqueId", "GetClipEnabled", "SetClipEnabled", "GetLinkedItems",
		"GetSourceStartFrame", "GetSourceEndFrame", "GetSourceAudioChannelMapping",
		"SetSourceAudioChannelMapping", "GetProperty", "SetProperty", "GetTrackTypeAndIndex",
		"GetFades", "SetFades", "GetSpeed", "GetType", "AddTransition" } },
	{ "MediaPoolItem", { "GetUniqueId", "GetAudioMapping", "SetAudioMapping", "ReplaceClip",
		"GetClipProperty", "LinkProxyMedia" } },
}
AIH.PROBE_NAMES = PROBE_NAMES
-- 있을 리 없는 이름. 이것이 "있다"고 나오면 이름 찾기 결과를 믿을 수 없다
local CONTROL_NAMES = { "AIH_NoSuchMethod", "Razor" }

local function has_name(obj, name)
	if obj == nil or AIH.DENY[name] then
		return nil
	end
	local ok, v = pcall(function() return obj[name] end)
	return ok and v ~= nil
end

local function first_item(calls, tl, kind)
	local n = call(calls, "Timeline.GetTrackCount", tl, "GetTrackCount", kind)
	if type(n) ~= "number" then
		return nil
	end
	for track = 1, n do
		local items = seq(call(calls, "Timeline.GetItemListInTrack", tl, "GetItemListInTrack", kind, track))
		if items[1] ~= nil then
			return items[1]
		end
	end
	return nil
end

local function props_keys(calls, item, name)
	local all = call(calls, name, item, "GetProperty")
	if type(all) ~= "table" then
		return NULL
	end
	local keys = {}
	for k in pairs(all) do
		keys[#keys + 1] = tostring(k)
	end
	table.sort(keys)
	while #keys > 120 do
		table.remove(keys)
	end
	return array(keys)
end

local function clipped(s)
	if type(s) ~= "string" then
		return NULL, false
	end
	if #s > AIH.MAX_MAPPING then
		return string.sub(s, 1, AIH.MAX_MAPPING), true
	end
	return s, false
end

ops.probe_read = function(a, calls)
	local res = { calls = calls, page = nz(current_page(calls)) }
	local project = project_of(calls)
	local tl = project ~= nil and call(calls, "Project.GetCurrentTimeline", project, "GetCurrentTimeline") or nil
	local pool = project ~= nil and call(calls, "Project.GetMediaPool", project, "GetMediaPool") or nil
	local vitem = tl ~= nil and first_item(calls, tl, "video") or nil
	local aitem = tl ~= nil and first_item(calls, tl, "audio") or nil
	-- 오디오 트랙마다 첫 클립 (A1~A4: OBS 녹화의 소리 1~4가 어디로 갔는지 보려고)
	local aitems = {}
	local na = tl ~= nil and call(calls, "Timeline.GetTrackCount", tl, "GetTrackCount", "audio") or nil
	if type(na) == "number" then
		for track = 1, na do
			if #aitems >= 4 then
				break
			end
			local first = seq(call(calls, "Timeline.GetItemListInTrack", tl, "GetItemListInTrack", "audio", track))[1]
			if first ~= nil then
				aitems[#aitems + 1] = { item = first, track = track }
			end
		end
	end
	local sample = aitem or vitem
	local mpi = sample ~= nil and call(calls, "TimelineItem.GetMediaPoolItem", sample, "GetMediaPoolItem") or nil
	local objects = {
		Resolve = st.R, Project = project, MediaPool = pool, Timeline = tl, TimelineItem = sample, MediaPoolItem = mpi,
	}
	local exists, reliable = {}, true
	for _, group in ipairs(PROBE_NAMES) do
		local cls, names = group[1], group[2]
		local obj = objects[cls]
		for _, name in ipairs(names) do
			exists[cls .. "." .. name] = nz(has_name(obj, name))
		end
		for _, name in ipairs(CONTROL_NAMES) do
			if has_name(obj, name) then
				reliable = false
				exists[cls .. "." .. name] = true
			end
		end
	end
	res.exists = exists
	res.existence_reliable = reliable
	res.project_uid = project ~= nil and nz(uid_of(calls, "Project.GetUniqueId", project)) or NULL
	res.timeline_uid = tl ~= nil and nz(uid_of(calls, "Timeline.GetUniqueId", tl)) or NULL
	res.props_keys = {
		video = vitem ~= nil and props_keys(calls, vitem, "TimelineItem.GetProperty.video") or NULL,
		audio = aitem ~= nil and props_keys(calls, aitem, "TimelineItem.GetProperty.audio") or NULL,
	}
	local mappings = array()
	for i = 1, #aitems do
		local raw = call(calls, "TimelineItem.GetSourceAudioChannelMapping", aitems[i].item,
			"GetSourceAudioChannelMapping")
		local text, cut = clipped(raw)
		mappings[#mappings + 1] = { track = aitems[i].track, mapping = text, truncated = cut }
	end
	res.source_audio_mapping = mappings
	if mpi ~= nil then
		local text, cut = clipped(call(calls, "MediaPoolItem.GetAudioMapping", mpi, "GetAudioMapping"))
		res.clip_audio_mapping = { mapping = text, truncated = cut }
	else
		res.clip_audio_mapping = NULL
	end
	if tl ~= nil then
		res.in_out = in_out_table(call(calls, "Timeline.GetMarkInOut", tl, "GetMarkInOut"))
		local sel, ok = call(calls, "Timeline.GetSelectedClips", tl, "GetSelectedClips")
		res.selected_count = (ok and type(sel) == "table") and #seq(sel) or NULL
	else
		res.in_out, res.selected_count = NULL, NULL
	end
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

-- 표시 목록. prefix가 있으면 custom data가 그것으로 시작하는 것만, limit개까지 (total은 맞는 전체 수)
ops.get_markers = function(a, calls)
	local prefix = nil
	if a.prefix ~= nil then
		prefix = str_arg(a.prefix, "prefix")
	end
	local limit = a.limit == nil and AIH.MAX_MARKERS or math.min(int_arg(a.limit, "limit", 0), AIH.MAX_MARKERS)
	local _, tl = need_timeline(calls)
	local markers = call(calls, "Timeline.GetMarkers", tl, "GetMarkers")
	local list = array()
	local total = NULL
	if type(markers) == "table" then
		total = 0
		local frames = {}
		for f, m in pairs(markers) do
			if type(f) == "number" and type(m) == "table" then
				local c = m.customData
				if prefix == nil or (type(c) == "string" and string.sub(c, 1, #prefix) == prefix) then
					frames[#frames + 1] = f
				end
			end
		end
		table.sort(frames)
		total = #frames
		for i = 1, #frames do
			local m = markers[frames[i]]
			if #list < limit then
				list[#list + 1] = {
					frame = frames[i], color = m.color, name = m.name, note = m.note,
					duration = m.duration, custom = m.customData,
				}
			end
		end
	end
	return { markers = list, total = total, calls = calls }
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

local function delete_by_custom(a, calls)
	local custom = str_arg(a.custom, "custom")
	-- 우리 꼬리표("aih:..." 또는 1차 시험판의 "aih_test")가 아니면 리졸브를 부르기 전에 거절한다.
	-- 다른 프로그램(예: 자막 도구)이 붙인 표시를 지우는 일이 없게.
	if not our_custom(custom) then
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

-- 색 목록 인자 (리졸브 색 이름 16개 가운데서만). 없으면 nil = 모든 색
local function colors_arg(v)
	if v == nil then
		return nil
	end
	local list = list_arg(v, "colors", 16)
	local out = {}
	for i = 1, #list do
		if type(list[i]) ~= "string" or not AIH.MARKER_COLORS[list[i]] then
			fail("bad_args", "colors")
		end
		out[list[i]] = true
	end
	return out
end

-- 표시 m이 prefix(와 색)에 맞는지
local function marker_matches(m, prefix, colors)
	if type(m) ~= "table" then
		return false
	end
	local c = m.customData
	return type(c) == "string" and string.sub(c, 1, #prefix) == prefix and (colors == nil or colors[m.color] == true)
end

-- 꼬리표 앞부분(prefix, "aih:"로 시작)으로 지우기. GetMarkers로 찾아 맞는 프레임만 DeleteMarkerAtFrame
local function delete_by_prefix(a, calls)
	if a.custom ~= nil then
		fail("bad_args", "custom") -- custom과 prefix를 같이 주면 무엇을 지울지 모호하다
	end
	local prefix = a.prefix
	if not prefix_ok(prefix) then
		fail("bad_args", "prefix")
	end
	local colors = colors_arg(a.colors)
	local want_snapshot = a.snapshot == true
	local _, tl = need_timeline(calls)
	local markers = call(calls, "Timeline.GetMarkers", tl, "GetMarkers")
	if type(markers) ~= "table" then
		fail("markers_unreadable", "Timeline.GetMarkers")
	end
	local frames = {}
	for f, m in pairs(markers) do
		if type(f) == "number" and marker_matches(m, prefix, colors) then
			frames[#frames + 1] = f
		end
	end
	table.sort(frames)
	local count, snapshot = 0, array()
	for i = 1, math.min(#frames, AIH.MAX_DELETE) do
		local f = frames[i]
		local m = markers[f]
		local gone = call(calls, "Timeline.DeleteMarkerAtFrame", tl, "DeleteMarkerAtFrame", f)
		if gone then
			count = count + 1
			if want_snapshot and #snapshot < AIH.MAX_SNAPSHOT then
				snapshot[#snapshot + 1] = {
					frame = f, color = m.color, name = m.name, note = m.note, duration = m.duration, custom = m.customData,
				}
			end
		end
	end
	-- 다시 읽어서 남은 수를 센다 (읽을 수 없으면 null)
	local after = call(calls, "Timeline.GetMarkers.after", tl, "GetMarkers")
	local remaining, remaining_ours = NULL, NULL
	if type(after) == "table" then
		remaining, remaining_ours = 0, 0
		for f, m in pairs(after) do
			if type(f) == "number" and type(m) == "table" and our_custom(m.customData) then
				remaining_ours = remaining_ours + 1
				if marker_matches(m, prefix, colors) then
					remaining = remaining + 1
				end
			end
		end
		count = math.max(0, #frames - remaining)
	end
	local res = {
		deleted = count > 0, deleted_count = count, matched = #frames,
		remaining = remaining, remaining_ours = remaining_ours, calls = calls,
	}
	if want_snapshot then
		res.snapshot = snapshot
		res.snapshot_truncated = count > #snapshot
	end
	return res
end

ops.delete_markers = function(a, calls)
	if a.prefix ~= nil then
		return delete_by_prefix(a, calls)
	end
	return delete_by_custom(a, calls)
end

-- 표시 여러 개 넣기 (자동화 버튼의 카드에서 [리졸브에 넣기]를 누른 뒤에만 불린다).
-- markers: {frame(타임라인 시작부터 센 프레임), dur, color, name, note, custom} 목록, 100개까지.
-- 모두 먼저 검사하고(하나라도 틀리면 아무것도 넣지 않음), GetMarkers는 넣기 전에 한 번만 읽는다.
-- 같은 꼬리표가 이미 있으면 넣지 않는다 (답을 못 받아 다시 보내도 두 번 들어가지 않게).
-- 그 프레임에 표시가 있으면 +1..+5 프레임으로 옮긴다 (끝은 그대로 두고 길이를 줄인다).
-- 길이 있는 표시(dur>1)를 리졸브가 거절하면 길이 1로 다시 넣고 point_fallback을 켠다 (그 뒤는 모두 1).
ops.add_markers = function(a, calls)
	local list = list_arg(a.markers, "markers", AIH.MAX_ADD)
	if #list == 0 then
		fail("bad_args", "markers")
	end
	local rows, seen = {}, {}
	for i = 1, #list do
		local m = list[i]
		if type(m) ~= "table" then
			fail("bad_args", "markers")
		end
		local row = {
			frame = int_arg(m.frame, "frame", 0),
			dur = m.dur == nil and 1 or int_arg(m.dur, "dur", 1),
			color = str_arg(m.color, "color"),
			name = str_arg(m.name, "name", ""),
			note = str_arg(m.note, "note", ""),
			custom = m.custom,
		}
		if not AIH.MARKER_COLORS[row.color] then
			fail("bad_args", "color")
		end
		if char_count(row.name) > AIH.MAX_NAME then
			fail("bad_args", "name")
		end
		if char_count(row.note) > AIH.MAX_NOTE then
			fail("bad_args", "note")
		end
		if not tag_ok(row.custom) or seen[row.custom] then
			fail("bad_args", "custom")
		end
		seen[row.custom] = true
		rows[i] = row
	end
	local _, tl = need_timeline(calls)
	local first = call(calls, "Timeline.GetStartFrame", tl, "GetStartFrame")
	local last = call(calls, "Timeline.GetEndFrame", tl, "GetEndFrame")
	if type(first) ~= "number" or type(last) ~= "number" or last <= first then
		fail("timeline_length_unknown", "Timeline.GetEndFrame")
	end
	local length = last - first
	for i = 1, #rows do
		if rows[i].frame + rows[i].dur > length then
			fail("bad_args", "frame")
		end
	end
	local existing = call(calls, "Timeline.GetMarkers", tl, "GetMarkers")
	if type(existing) ~= "table" then
		fail("markers_unreadable", "Timeline.GetMarkers")
	end
	local taken, have = {}, {}
	for f, m in pairs(existing) do
		if type(f) == "number" then
			taken[f] = true
			if type(m) == "table" and type(m.customData) == "string" then
				have[m.customData] = f
			end
		end
	end
	local placed, failed, skipped = array(), array(), array()
	local point_fallback = a.point_only == true
	for i = 1, #rows do
		local r = rows[i]
		if have[r.custom] ~= nil then
			skipped[#skipped + 1] = i
		else
			local err, done = "taken", false
			for k = 0, AIH.MARKER_SHIFT do
				local f = r.frame + k
				local dur = r.dur > k and r.dur - k or 1
				if point_fallback then
					dur = 1
				end
				if f + dur > length then
					err = "outside"
					break
				end
				if not taken[f] then
					local added, ok = call(calls, "Timeline.AddMarker", tl, "AddMarker", f, r.color, r.name, r.note, dur, r.custom)
					if ok and not added and dur > 1 then
						added, ok = call(calls, "Timeline.AddMarker.point", tl, "AddMarker", f, r.color, r.name, r.note, 1, r.custom)
						if added then
							point_fallback = true
							dur = 1
						end
					end
					if not ok then
						err = "error"
						break
					end
					taken[f] = true
					if added then
						have[r.custom] = f
						placed[#placed + 1] = { i = i, frame = f, dur = dur, custom = r.custom, shifted = k }
						done = true
						break
					end
					err = "refused"
				end
			end
			if not done then
				failed[#failed + 1] = { i = i, err = err }
			end
		end
	end
	-- 넣은 뒤 다시 읽어 실제로 들어갔는지와 길이를 본다 (영수증은 이 값으로 만든다)
	local after = call(calls, "Timeline.GetMarkers.after", tl, "GetMarkers")
	for j = 1, #placed do
		local p = placed[j]
		local m = type(after) == "table" and after[p.frame] or nil
		if type(after) ~= "table" then
			p.found, p.dur_readback = NULL, NULL
		elseif type(m) == "table" and m.customData == p.custom then
			p.found = true
			p.dur_readback = type(m.duration) == "number" and m.duration or NULL
		else
			p.found, p.dur_readback = false, NULL
		end
	end
	return {
		placed = placed, failed = failed, skipped_existing = skipped, point_fallback = point_fallback,
		requested = #rows, length = length, calls = calls,
	}
end

-- 미디어 풀 맨 위 폴더 아래의 "AI 도우미" 저장소를 찾고, 없으면 만든다 (no_create면 nil)
local function find_bin(calls, pool, no_create)
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
	if no_create then
		return nil
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

	-- endFrame은 들어가지 않는 끝이다 (리졸브 21.1에서 frames - 1을 주면 한 프레임 모자라게 들어갔다)
	local info = {
		mediaPoolItem = item, startFrame = 0, endFrame = frames,
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
	res.requested_frames = frames
	res.placed_frames, res.length_ok = NULL, NULL
	if placed[1] ~= nil then
		res.item_start = call(calls, "TimelineItem.GetStart", placed[1], "GetStart")
		res.item_end = call(calls, "TimelineItem.GetEnd", placed[1], "GetEnd")
		if type(res.item_start) == "number" and type(res.item_end) == "number" then
			res.placed_frames = res.item_end - res.item_start
			res.length_ok = res.placed_frames == frames
		end
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

-- 편집(Edit) 화면이 아니면 잠깐 바꾼다. 돌려주는 값: 원래 화면(바꿨을 때만)
local function edit_page(calls)
	local page = current_page(calls)
	if page ~= nil and page ~= "edit" then
		call(calls, "Resolve.OpenPage", st.R, "OpenPage", "edit")
		return page
	end
	return nil
end

local function restore_page(calls, page)
	if page ~= nil then
		call(calls, "Resolve.OpenPage.restore", st.R, "OpenPage", page)
	end
end

-- 우리 시험 트랙(track_name, 클립이 모두 우리 파일인 트랙)을 지운다.
-- 클립 지우기(DeleteClips)는 편집(Edit) 화면 밖에서 실패한다는 보고가 있다: 편집 화면에서만 한다.
-- switch_page=true면 (창에서 [바꿔서 빼기]를 누른 뒤) 편집 화면으로 바꿔서 하고 원래 화면으로 돌린다.
local remove_tracks
ops.remove_audio = function(a, calls)
	local track_name = str_arg(a.track_name, "track_name")
	if track_name == "" then
		fail("bad_args", "track_name")
	end
	local page = current_page(calls)
	local switched = nil
	if page ~= nil and page ~= "edit" then
		if a.switch_page ~= true then
			fail("need_edit_page", "Resolve.GetCurrentPage")
		end
		switched = edit_page(calls)
		if current_page(calls) ~= "edit" then
			restore_page(calls, switched)
			fail("need_edit_page", "Resolve.OpenPage")
		end
	end
	local ok, res = pcall(remove_tracks, calls, track_name)
	restore_page(calls, switched)
	if not ok then
		error(res, 0)
	end
	res.page = nz(page)
	res.switched_page = switched ~= nil
	res.calls = calls
	return res
end

remove_tracks = function(calls, track_name)
	local _, tl = need_timeline(calls)
	local count = call(calls, "Timeline.GetTrackCount", tl, "GetTrackCount", "audio")
	local removed, skipped = 0, 0
	local clip_results, track_results = array(), array()
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
						local done, okd = call(calls, "Timeline.DeleteClips", tl, "DeleteClips", items)
						clip_results[#clip_results + 1] = { track = index, count = #items, result = call_result(done, okd) }
					end
					local gone, okt = call(calls, "Timeline.DeleteTrack", tl, "DeleteTrack", "audio", index)
					track_results[#track_results + 1] = { track = index, result = call_result(gone, okt) }
					if gone then
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
	return {
		removed_tracks = removed, skipped = skipped, delete_clips = clip_results, delete_track = track_results,
	}
end

---------------------------------------------------------------------------
-- 기능 점검 (probe_copy): 지금 타임라인의 복사본을 잠깐 만들어 그 복사본에서만 시험하고 지운다.
-- C1 복사본 만들기 · C2 트랙 끄기 · C3 클립 끄기 · C4 구간 표시 · C5 재생 위치 · C6 다시 넣기
-- C7 새 소리 파일 가져오기 · C8 정리. C2~C7은 지금 타임라인이 그 복사본일 때만 한다.
-- 단계마다 바꾼 것을 되돌린 뒤 복사본의 지문을 돌려준다. C8은 지문이 맞을 때만 복사본을 지운다.
---------------------------------------------------------------------------

-- 지금 타임라인이 C1이 만든 복사본인지. 아니면 오류로 멈춘다 (사용자 타임라인은 건드리지 않는다)
local function probe_copy_current(calls)
	local p = st.probe
	if p == nil or (p.copy_uid == nil and p.copy_name == nil) then
		fail("no_probe", "probe_copy")
	end
	local project, tl = need_timeline(calls)
	local uid, name = timeline_ident(calls, tl)
	if not same_timeline(uid, name, p.copy_uid, p.copy_name) or (name ~= nil and not is_probe_name(name)) then
		fail("not_probe_copy", "probe_copy")
	end
	return project, tl
end

local function stage_result(stage, ok, detail, tl)
	return { stage = stage, ok = ok and true or false, detail = detail, fingerprint = nz(AIH.fingerprint(tl)) }
end

local probe_stages = {}

probe_stages.C1 = function(a, calls)
	local suffix = str_arg(a.suffix, "suffix")
	if not string.match(suffix, "^%d%d%d%d%d%d$") then
		fail("bad_args", "suffix")
	end
	local project, tl = need_timeline(calls)
	local uid, name = timeline_ident(calls, tl)
	if is_probe_name(name) then
		fail("already_probe_copy", "probe_copy") -- 복사본의 복사본은 만들지 않는다
	end
	local copy_name = AIH.PROBE_PREFIX .. " " .. suffix
	if find_timeline(calls, project, nil, copy_name) ~= nil then
		fail("name_taken", "probe_copy")
	end
	-- 지난 점검의 복사본이 남아 있으면 새로 만들지 않는다 (그 기록을 잃지 않게)
	local old = st.probe
	if old ~= nil and (old.copy_uid ~= nil or old.copy_name ~= nil)
		and find_timeline(calls, project, old.copy_uid, old.copy_name) ~= nil then
		fail("leftover_copy", "probe_copy")
	end
	st.probe = {
		original_uid = uid, original_name = name,
		page = current_page(calls),
		tc = call(calls, "Timeline.GetCurrentTimecode", tl, "GetCurrentTimecode"),
	}
	local copy = call(calls, "Timeline.DuplicateTimeline", tl, "DuplicateTimeline", copy_name)
	if copy == nil then
		fail("duplicate_failed", "Timeline.DuplicateTimeline")
	end
	local cuid, cname = timeline_ident(calls, copy)
	if cname ~= nil and not is_probe_name(cname) then
		-- 이름을 무시한 판: 꼬리표가 붙은 이름으로 한 번 더 바꿔 본다
		call(calls, "Timeline.SetName", copy, "SetName", copy_name)
		cuid, cname = timeline_ident(calls, copy)
	end
	st.probe.copy_uid = cuid
	st.probe.copy_name = cname or copy_name
	call(calls, "Project.SetCurrentTimeline", project, "SetCurrentTimeline", copy)
	local now_tl = call(calls, "Project.GetCurrentTimeline.after", project, "GetCurrentTimeline")
	local nuid, nname = nil, nil
	if now_tl ~= nil then
		nuid, nname = timeline_ident(calls, now_tl)
	end
	local switched = same_timeline(nuid, nname, st.probe.copy_uid, st.probe.copy_name)
	local detail = {
		original_uid = nz(uid), original_name = nz(name), copy_uid = nz(cuid), copy_name = nz(st.probe.copy_name),
		name_tagged = is_probe_name(st.probe.copy_name), switched = switched,
		page = nz(st.probe.page), tc = nz(st.probe.tc),
	}
	return stage_result("C1", switched and is_probe_name(st.probe.copy_name), detail, copy)
end

probe_stages.C2 = function(a, calls)
	local _, tl = probe_copy_current(calls)
	local n = call(calls, "Timeline.GetTrackCount", tl, "GetTrackCount", "audio")
	if type(n) ~= "number" or n < 1 then
		return stage_result("C2", false, { reason = "no_audio_track" }, tl)
	end
	local before = call(calls, "Timeline.GetIsTrackEnabled", tl, "GetIsTrackEnabled", "audio", 1)
	local set = call(calls, "Timeline.SetTrackEnable", tl, "SetTrackEnable", "audio", 1, false)
	local mid = call(calls, "Timeline.GetIsTrackEnabled.after", tl, "GetIsTrackEnabled", "audio", 1)
	call(calls, "Timeline.SetTrackEnable.restore", tl, "SetTrackEnable", "audio", 1, before ~= false)
	local after = call(calls, "Timeline.GetIsTrackEnabled.restored", tl, "GetIsTrackEnabled", "audio", 1)
	local detail = {
		before = bool_or_null(before), set_result = bool_or_null(set), readback = bool_or_null(mid),
		restored = bool_or_null(after),
	}
	return stage_result("C2", mid == false and after == (before ~= false), detail, tl)
end

local function enabled_states(calls, items, name)
	local out = {}
	for i = 1, #items do
		out[i] = call(calls, name, items[i], "GetClipEnabled")
	end
	return out
end

probe_stages.C3 = function(a, calls)
	local _, tl = probe_copy_current(calls)
	local item = seq(call(calls, "Timeline.GetItemListInTrack", tl, "GetItemListInTrack", "audio", 1))[1]
	if item == nil then
		return stage_result("C3", false, { reason = "no_audio_item" }, tl)
	end
	local linked = seq(call(calls, "TimelineItem.GetLinkedItems", item, "GetLinkedItems"))
	local before = call(calls, "TimelineItem.GetClipEnabled", item, "GetClipEnabled")
	local linked_before = enabled_states(calls, linked, "TimelineItem.GetClipEnabled.linked")
	local set = call(calls, "TimelineItem.SetClipEnabled", item, "SetClipEnabled", false)
	local mid = call(calls, "TimelineItem.GetClipEnabled.after", item, "GetClipEnabled")
	local linked_mid = enabled_states(calls, linked, "TimelineItem.GetClipEnabled.linked_after")
	local linked_changed = false
	for i = 1, #linked do
		if linked_mid[i] ~= linked_before[i] then
			linked_changed = true
		end
	end
	call(calls, "TimelineItem.SetClipEnabled.restore", item, "SetClipEnabled", before ~= false)
	if linked_changed then
		for i = 1, #linked do
			if linked_before[i] ~= nil then
				call(calls, "TimelineItem.SetClipEnabled.restore_linked", linked[i], "SetClipEnabled", linked_before[i])
			end
		end
	end
	local after = call(calls, "TimelineItem.GetClipEnabled.restored", item, "GetClipEnabled")
	local detail = {
		before = bool_or_null(before), set_result = bool_or_null(set), readback = bool_or_null(mid),
		restored = bool_or_null(after), linked_count = #linked, linked_changed = linked_changed,
	}
	return stage_result("C3", mid == false and after == (before ~= false), detail, tl)
end

probe_stages.C4 = function(a, calls)
	local _, tl = probe_copy_current(calls)
	local custom = "aih:probe:c4"
	local markers = call(calls, "Timeline.GetMarkers", tl, "GetMarkers")
	if type(markers) ~= "table" then
		markers = {}
	end
	local frame = nil
	for f = 60, 65 do
		if markers[f] == nil then
			frame = f
			break
		end
	end
	if frame == nil then
		return stage_result("C4", false, { reason = "no_free_frame" }, tl)
	end
	local added = call(calls, "Timeline.AddMarker", tl, "AddMarker", frame, "Blue", "AI 도우미 점검", "", 120, custom)
	local point_only = false
	if not added then
		added = call(calls, "Timeline.AddMarker.point", tl, "AddMarker", frame, "Blue", "AI 도우미 점검", "", 1, custom)
		point_only = added and true or false
	end
	local now = call(calls, "Timeline.GetMarkers.after", tl, "GetMarkers")
	local got = type(now) == "table" and now[frame] or nil
	local readback = type(got) == "table" and got.duration or nil
	local by_custom = call(calls, "Timeline.DeleteMarkerByCustomData", tl, "DeleteMarkerByCustomData", custom)
	local by_frame = NULL
	local left = marker_frames(calls, tl, custom, "Timeline.GetMarkers.check")
	if left ~= nil and #left > 0 then
		by_frame = call(calls, "Timeline.DeleteMarkerAtFrame", tl, "DeleteMarkerAtFrame", frame) and true or false
		left = marker_frames(calls, tl, custom, "Timeline.GetMarkers.final")
	end
	local detail = {
		frame = frame, added = added and true or false, point_only = point_only,
		duration_readback = nz(readback), range_ok = readback == 120,
		deleted_by_custom = bool_or_null(by_custom), deleted_by_frame = by_frame,
		left = left ~= nil and #left or NULL,
	}
	return stage_result("C4", (added and left ~= nil and #left == 0) and true or false, detail, tl)
end

probe_stages.C5 = function(a, calls)
	local tc = str_arg(a.tc, "tc")
	if not string.match(tc, "^%d%d:%d%d:%d%d[:;]%d%d$") then
		fail("bad_args", "tc")
	end
	local _, tl = probe_copy_current(calls)
	local page = current_page(calls)
	if page ~= nil and not PLAYHEAD_PAGES[page] then
		return stage_result("C5", false, { reason = "page", page = page }, tl)
	end
	local before = call(calls, "Timeline.GetCurrentTimecode", tl, "GetCurrentTimecode")
	local set = call(calls, "Timeline.SetCurrentTimecode", tl, "SetCurrentTimecode", tc)
	local readback = call(calls, "Timeline.GetCurrentTimecode.after", tl, "GetCurrentTimecode")
	if type(before) == "string" then
		call(calls, "Timeline.SetCurrentTimecode.restore", tl, "SetCurrentTimecode", before)
	end
	local after = call(calls, "Timeline.GetCurrentTimecode.restored", tl, "GetCurrentTimecode")
	local detail = {
		requested = tc, before = nz(before), set_result = bool_or_null(set), readback = nz(readback),
		restored = nz(after), page = nz(page),
	}
	return stage_result("C5", readback == tc, detail, tl)
end

local function all_items(calls, tl)
	local out = {}
	for _, kind in ipairs({ "video", "audio" }) do
		local n = call(calls, "Timeline.GetTrackCount", tl, "GetTrackCount", kind)
		if type(n) == "number" then
			for track = 1, n do
				local items = seq(call(calls, "Timeline.GetItemListInTrack", tl, "GetItemListInTrack", kind, track))
				for i = 1, #items do
					out[#out + 1] = items[i]
				end
			end
		end
	end
	return out
end

local function placed_row(calls, item)
	local row = {}
	local tt = call(calls, "TimelineItem.GetTrackTypeAndIndex", item, "GetTrackTypeAndIndex")
	if type(tt) == "table" then
		row.track_type, row.track = nz(tt[1]), nz(tt[2])
	end
	row.start = nz(call(calls, "TimelineItem.GetStart", item, "GetStart"))
	row["end"] = nz(call(calls, "TimelineItem.GetEnd", item, "GetEnd"))
	if type(row.start) == "number" and type(row["end"]) == "number" then
		row.length = row["end"] - row.start
	end
	row.linked = #seq(call(calls, "TimelineItem.GetLinkedItems", item, "GetLinkedItems"))
	return row
end

probe_stages.C6 = function(a, calls)
	local project, tl = probe_copy_current(calls)
	local pool = call(calls, "Project.GetMediaPool", project, "GetMediaPool")
	if pool == nil then
		fail("no_media_pool", "Project.GetMediaPool")
	end
	local start = call(calls, "Timeline.GetStartFrame", tl, "GetStartFrame")
	local items = all_items(calls, tl)
	local source = nil
	for i = 1, #items do
		source = call(calls, "TimelineItem.GetMediaPoolItem", items[i], "GetMediaPoolItem")
		if source ~= nil then
			break
		end
	end
	if source == nil or type(start) ~= "number" then
		return stage_result("C6", false, { reason = "no_source" }, tl)
	end
	local old_page = edit_page(calls)
	local deleted = NULL
	if #items > 0 then
		deleted = bool_or_null(call(calls, "Timeline.DeleteClips", tl, "DeleteClips", items, false))
	end
	local left = #all_items(calls, tl)
	local info = { mediaPoolItem = source, startFrame = 0, endFrame = 600, recordFrame = start }
	local placed = seq(call(calls, "MediaPool.AppendToTimeline", pool, "AppendToTimeline", { info }))
	local rows = array()
	for i = 1, #placed do
		rows[#rows + 1] = placed_row(calls, placed[i])
	end
	restore_page(calls, old_page)
	local semantics = NULL
	if rows[1] ~= nil and type(rows[1].length) == "number" then
		if rows[1].length == 600 then
			semantics = "exclusive"
		elseif rows[1].length == 601 then
			semantics = "inclusive"
		else
			semantics = "other"
		end
	end
	local detail = {
		page_switched_from = nz(old_page), items_before = #items, delete_result = deleted, items_left = left,
		record_frame = start, requested_frames = 600, placed = rows, end_semantics = semantics,
	}
	return stage_result("C6", #rows > 0, detail, tl)
end

probe_stages.C7 = function(a, calls)
	local path = str_arg(a.path, "path")
	local good, why = AIH.path_ok(path)
	if not good then
		fail("bad_path:" .. why, "path_guard")
	end
	local rest = string.lower(string.sub(to_backslash(path), #AIH.files_prefix() + 1))
	if string.sub(rest, 1, 6) ~= "probe\\" then
		fail("bad_path:not_probe", "path_guard")
	end
	local frames = int_arg(a.frames, "frames", 1)
	local project, tl = probe_copy_current(calls)
	local pool = call(calls, "Project.GetMediaPool", project, "GetMediaPool")
	if pool == nil then
		fail("no_media_pool", "Project.GetMediaPool")
	end
	local start = call(calls, "Timeline.GetStartFrame", tl, "GetStartFrame")
	local previous = call(calls, "MediaPool.GetCurrentFolder", pool, "GetCurrentFolder")
	local ok, detail = pcall(function()
		local bin = find_bin(calls, pool)
		call(calls, "MediaPool.SetCurrentFolder", pool, "SetCurrentFolder", bin)
		local known = {}
		local before = seq(call(calls, "Folder.GetClipList", bin, "GetClipList"))
		for i = 1, #before do
			local u = uid_of(calls, "MediaPoolItem.GetUniqueId", before[i])
			if u ~= nil then
				known[u] = true
			end
		end
		local got = seq(call(calls, "MediaPool.ImportMedia", pool, "ImportMedia", { path }))
		local item, imported, found_by = got[1], false, NULL
		if item ~= nil then
			local u = uid_of(calls, "MediaPoolItem.GetUniqueId", item)
			imported = not (u ~= nil and known[u])
			found_by = "import"
		else
			local clips = seq(call(calls, "Folder.GetClipList.after_import", bin, "GetClipList"))
			for i = 1, #clips do
				if same_file(clip_path(calls, clips[i]), path) then
					item, found_by = clips[i], "path"
					break
				end
			end
		end
		local d = { imported = imported, found_by = found_by, requested_frames = frames }
		if item == nil then
			d.reason = "import_failed"
			return d
		end
		st.probe.clip_path = path
		st.probe.clip_uid = uid_of(calls, "MediaPoolItem.GetUniqueId", item)
		st.probe.clip_imported = imported
		d.clip = clip_info(calls, item)
		d.clip_uid = nz(st.probe.clip_uid)
		if not call(calls, "Timeline.AddTrack", tl, "AddTrack", "audio", "stereo") then
			d.reason = "add_track_failed"
			return d
		end
		local index = call(calls, "Timeline.GetTrackCount", tl, "GetTrackCount", "audio")
		d.track_index = nz(index)
		if type(index) ~= "number" then
			d.reason = "track_count_failed"
			return d
		end
		call(calls, "Timeline.SetTrackName", tl, "SetTrackName", "audio", index, AIH.PROBE_TRACK)
		local info = {
			mediaPoolItem = item, startFrame = 0, endFrame = frames,
			mediaType = 2, trackIndex = index, recordFrame = type(start) == "number" and start or 0,
		}
		local placed = seq(call(calls, "MediaPool.AppendToTimeline", pool, "AppendToTimeline", { info }))
		d.appended = #placed
		if placed[1] ~= nil then
			local row = placed_row(calls, placed[1])
			d.placed = row
			d.placed_frames = nz(row.length)
			d.length_ok = row.length == frames
		end
		return d
	end)
	if previous ~= nil then
		call(calls, "MediaPool.SetCurrentFolder.restore", pool, "SetCurrentFolder", previous)
	end
	if not ok then
		error(detail, 0)
	end
	return stage_result("C7", detail.imported == true and detail.length_ok == true, detail, tl)
end

-- 정리: 원래 타임라인으로 돌아가서, 복사본의 지문이 expect_fingerprint와 같을 때만 복사본을 지운다.
-- 스크립트를 다시 눌러 기록(st.probe)이 없으면 창이 알려 준 번호·이름을 쓴다 (이름 꼬리표와 지문 검사는 같다).
probe_stages.C8 = function(a, calls)
	local expect = str_arg(a.expect_fingerprint, "expect_fingerprint", "")
	local rec = st.probe
	if rec == nil then
		rec = {
			original_uid = a.original_uid, original_name = a.original_name,
			copy_uid = a.copy_uid, copy_name = a.copy_name,
			clip_path = a.clip_path, clip_imported = a.clip_imported == true,
		}
	end
	if rec.copy_uid == nil and rec.copy_name == nil then
		fail("no_probe", "probe_copy")
	end
	local project = project_of(calls)
	if project == nil then
		fail("no_project", "ProjectManager.GetCurrentProject")
	end
	local detail = { steps = {} }
	local function step(name, fn)
		local ok, err = pcall(fn)
		detail.steps[name] = ok and "ok" or ("err:" .. short(type(err) == "table" and err.code or err))
	end
	local fp_before = nil
	step("read_current", function()
		local cur = call(calls, "Project.GetCurrentTimeline", project, "GetCurrentTimeline")
		if cur ~= nil then
			local uid, name = timeline_ident(calls, cur)
			if same_timeline(uid, name, rec.copy_uid, rec.copy_name) then
				fp_before = AIH.fingerprint(cur)
			end
		end
	end)
	step("switch_back", function()
		local original = find_timeline(calls, project, rec.original_uid, rec.original_name)
		detail.original_found = original ~= nil
		if original ~= nil then
			call(calls, "Project.SetCurrentTimeline", project, "SetCurrentTimeline", original)
		end
	end)
	local copy, fp = nil, nil
	step("find_copy", function()
		copy = find_timeline(calls, project, rec.copy_uid, rec.copy_name)
	end)
	detail.copy_found = copy ~= nil
	detail.deleted = false
	if copy ~= nil then
		step("check_copy", function()
			local uid, name = timeline_ident(calls, copy)
			local cur = call(calls, "Project.GetCurrentTimeline.check", project, "GetCurrentTimeline")
			local cuid, cname = nil, nil
			if cur ~= nil then
				cuid, cname = timeline_ident(calls, cur)
			end
			fp = AIH.fingerprint(copy) or fp_before
			detail.fingerprint_before_switch = nz(fp_before)
			if not is_probe_name(name) then
				detail.reason = "not_probe_name"
			elseif cur == nil or same_timeline(cuid, cname, uid, name) then
				detail.reason = "copy_is_current"
			elseif expect == "" then
				detail.reason = "no_expect"
			elseif fp == nil or fp ~= expect or (fp_before ~= nil and fp_before ~= expect) then
				detail.reason = "fingerprint_mismatch"
			else
				local done = call(calls, "MediaPool.DeleteTimelines", call(calls, "Project.GetMediaPool", project,
					"GetMediaPool"), "DeleteTimelines", { copy })
				detail.delete_result = bool_or_null(done)
				detail.deleted = find_timeline(calls, project, rec.copy_uid, rec.copy_name) == nil
			end
		end)
	else
		detail.reason = "copy_gone"
	end
	detail.clip_deleted = NULL
	if rec.clip_path ~= nil and rec.clip_imported == true and AIH.ours(rec.clip_path) then
		step("delete_clip", function()
			local pool = call(calls, "Project.GetMediaPool", project, "GetMediaPool")
			local bin = pool ~= nil and find_bin(calls, pool, true) or nil
			detail.clip_deleted = false
			if bin == nil then
				return
			end
			local clips = seq(call(calls, "Folder.GetClipList", bin, "GetClipList"))
			for i = 1, #clips do
				if same_file(clip_path(calls, clips[i]), rec.clip_path) then
					call(calls, "MediaPool.DeleteClips", pool, "DeleteClips", { clips[i] })
					local still = false
					local again = seq(call(calls, "Folder.GetClipList.check", bin, "GetClipList"))
					for j = 1, #again do
						if same_file(clip_path(calls, again[j]), rec.clip_path) then
							still = true
						end
					end
					detail.clip_deleted = not still
					break
				end
			end
		end)
	end
	step("restore", function()
		if rec.page ~= nil and current_page(calls) ~= rec.page then
			call(calls, "Resolve.OpenPage.restore", st.R, "OpenPage", rec.page)
		end
		local cur = call(calls, "Project.GetCurrentTimeline.restore", project, "GetCurrentTimeline")
		if type(rec.tc) == "string" and cur ~= nil and PLAYHEAD_PAGES[current_page(calls) or "edit"] then
			call(calls, "Timeline.SetCurrentTimecode.restore", cur, "SetCurrentTimecode", rec.tc)
		end
	end)
	if detail.deleted or not detail.copy_found then
		st.probe = nil
	end
	return {
		stage = "C8", ok = (detail.deleted or not detail.copy_found) and true or false,
		detail = detail, fingerprint = nz(fp),
	}
end

ops.probe_copy = function(a, calls)
	local stage = str_arg(a.stage, "stage")
	local fn = probe_stages[stage]
	if fn == nil then
		fail("bad_args", "stage")
	end
	local res = fn(a, calls)
	res.calls = calls
	res.probe = probe_record()
	return res
end

-- 원래 타임라인으로 돌아가기만 한다 (아무것도 고치지 않음). 기능 점검 C1이 적어 둔 원래 타임라인만 된다.
-- 스크립트를 다시 눌러 기록이 없으면, 지금 타임라인이 점검용 복사본일 때만 창이 알려 준 번호로 간다.
ops.switch_timeline = function(a, calls)
	local uid = a.uid ~= nil and str_arg(a.uid, "uid") or nil
	local name = a.name ~= nil and str_arg(a.name, "name") or nil
	if uid == nil and name == nil then
		fail("bad_args", "uid")
	end
	local project, cur = need_timeline(calls)
	local rec = st.probe
	local recorded = rec ~= nil and (rec.original_uid ~= nil or rec.original_name ~= nil)
	if recorded then
		if not same_timeline(uid, name, rec.original_uid, rec.original_name) then
			fail("not_original", "switch_timeline")
		end
	else
		local _, cname = timeline_ident(calls, cur)
		if not is_probe_name(cname) then
			fail("not_original", "switch_timeline")
		end
	end
	local target = find_timeline(calls, project, uid, name)
	if target == nil then
		fail("timeline_not_found", "switch_timeline")
	end
	call(calls, "Project.SetCurrentTimeline", project, "SetCurrentTimeline", target)
	local now_tl = call(calls, "Project.GetCurrentTimeline.after", project, "GetCurrentTimeline")
	local ruid, rname = nil, nil
	if now_tl ~= nil then
		ruid, rname = timeline_ident(calls, now_tl)
	end
	return {
		switched = same_timeline(ruid, rname, uid, name), readback_uid = nz(ruid), readback_name = nz(rname),
		recorded = recorded, calls = calls,
	}
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
