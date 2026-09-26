"""도우미 창에 보이는 한국어 글 전부 (설계 부록 A).

화면 모듈(window, header_view, automation_view, chat_view, undo_view, check_page, connection)은
글을 직접 쓰지 않고 여기서 가져온다 (tests/test_panel.py가 소스를 읽어 확인한다).
쉬운 말로, 짧게. 색만으로 뜻을 전하지 않는다 (●/○/! 옆에 늘 낱말).
"""

from engine.resolve_link import SCRIPT_NAME

WINDOW_TITLE = "AI 편집 도우미"

# ── 머리말: 연결 상태 ───────────────────────────────────────────────
DOT_CONNECTED = "●"
DOT_CHECKING = "◌"
DOT_OFF = "○"
DOT_WARN = "!"
DOT_FILE = "◐"

STATUS_CONNECTED = "연결됨"
STATUS_CHECKING = "확인하는 중…"
STATUS_NOT_CONNECTED = "연결 안 됨"
STATUS_RESOLVE_QUIT = "리졸브가 꺼졌어요"
STATUS_BUSY = "리졸브가 바빠요"
STATUS_OLD_SCRIPT = "스크립트를 한 번 더 눌러 주세요"
STATUS_FILE_MODE = "파일로 주고받는 중"

CONNECT_HINT = f"리졸브에서 한 번만 눌러 주세요: Workspace → Scripts → {SCRIPT_NAME}"
OLD_SCRIPT_HINT = f"리졸브 쪽 스크립트가 예전 판이에요. Scripts → {SCRIPT_NAME}를 한 번 더 눌러 주세요"
BUSY_HINT = "열린 대화 상자가 있으면 닫아 주세요"
RESOLVE_QUIT_HINT = "리졸브를 다시 켜면 자동으로 다시 찾아요"

BTN_CHECK_CONNECTION = "연결 확인"
BTN_RETRY = "다시 시도"
BTN_MORE = "⋯"
TIP_MORE = "연결 점검, 결과 저장, 설정, 도움말"
TIP_CHECK_CONNECTION = "리졸브와 이어져 있는지 지금 확인해요"

SUMMARY_NONE = "리졸브에서 열린 타임라인을 아직 몰라요"
SUMMARY_NO_PROJECT = "열린 프로젝트 없음 → 리졸브에서 프로젝트를 열어 주세요"
SUMMARY_NO_TIMELINE = "열린 타임라인 없음 → 리졸브에서 타임라인을 열어 주세요"
SUMMARY_LINE = "{timeline} · {length} · {fps}fps · 소리 트랙 {audio}개"
SUMMARY_LENGTH_UNKNOWN = "길이 모름"
DETAILS_SHOW = "▸"
DETAILS_HIDE = "▾"
TIP_DETAILS = "리졸브에서 열려 있는 것 자세히 보기"

INFO_TITLE = "리졸브에서 열려 있는 것"
INFO_VERSION = "리졸브 버전"
INFO_PROJECT = "프로젝트"
INFO_TIMELINE = "타임라인"
INFO_FPS = "프레임 속도"
INFO_CLIPS = "클립 수"
INFO_FIRST_CLIP = "첫 클립 파일"
INFO_EMPTY = "-"
INFO_UNKNOWN_VERSION = "(판 모름)"
INFO_RESOLVE = "리졸브"
INFO_NO_NAME = "(이름 없음)"
INFO_DROP = " (드롭 프레임)"
INFO_CLIPS_LINE = "영상 {video}개, 소리 {audio}개"
INFO_CLIPS_TRUNCATED = " (많아서 일부만 셈)"

PROBE_COPY_WARNING = "! 지금 열린 타임라인은 점검용 복사본이에요"
BTN_BACK_TO_ORIGINAL = "원래 타임라인으로"
PROBE_COPY_NO_RECORD = "원래 타임라인을 리졸브에서 직접 골라 주세요"
SWITCHED_BACK = "원래 타임라인 '{name}'으로 돌아왔어요"
SWITCH_FAILED = "원래 타임라인으로 돌아가지 못했어요: {reason}"
SWITCH_NOT_CONFIRMED = "옮긴 뒤 다시 읽어 보니 '{name}'이 열려 있어요. 리졸브에서 확인해 주세요"

# ── 자동화 버튼 ─────────────────────────────────────────────────────
SLOT_DEFAULT_NAMES = {1: "쉬는 곳 표시", 2: "튀는 소리 표시", 3: "소리 고르게"}
KIND_NAMES = {
    "mark_pauses": "쉬는 곳 표시", "mark_spikes": "튀는 소리 표시", "balance_voice": "소리 고르게",
    "name_tracks": "OBS 트랙 정리", "make_subtitles": "자막 만들기", "final_check": "마무리 점검",
    "duck_music": "배경음악 낮추기",
}
KIND_NOT_READY = "{name} (준비 중)"
SLOT_SUMMARY = {
    "mark_pauses": "{min_s}초 넘게 쉰 곳에 {color_word} 표시",
    "mark_spikes": "평소 말소리보다 {above_lu}dB 넘게 · {color_word} 표시",
    "balance_voice": "목소리를 {target_lufs} LUFS로 고르게",
}
SLOT_SUMMARY_NOT_READY = "{name} · 준비 중"
SLOT_SCOPE_IN_OUT = " · In~Out만"
COLOR_WORDS = {
    "Blue": "파란", "Red": "빨간", "Yellow": "노란", "Green": "초록", "Purple": "보라", "Cyan": "하늘색",
    "Pink": "분홍", "Fuchsia": "자홍", "Rose": "장미색", "Lavender": "연보라", "Sky": "하늘", "Mint": "민트",
    "Lemon": "레몬", "Sand": "모래색", "Cocoa": "갈색", "Cream": "크림색",
}
SLOT_NEVER_RUN = "아직 안 했어요"
SLOT_DISABLED_NOT_CONNECTED = "연결되면 쓸 수 있어요"
SLOT_DISABLED_NOT_READY = "준비 중이에요"
SLOT_DISABLED_BUSY = "끝나면 할 수 있어요"
SLOT_DISABLED_PROBE_COPY = "원래 타임라인으로 돌아가면 쓸 수 있어요"
SLOT_DISABLED_OLD_SCRIPT = "스크립트를 한 번 더 누르면 쓸 수 있어요"
SLOT_RUNNING = "하는 중…"
SLOT_SETTINGS = "⚙"
TIP_SLOT_SETTINGS = "이 버튼의 설정 바꾸기"
# 설계 B2.1: "다음 판"이라고 쓰지 않는다 (사용자 설명서에서 그 말은 이번 판을 가리킨다)
TIP_SLOT_NOT_READY = "'{name}'은 아직 준비 중이에요. ⚙에서 다른 일로 바꿀 수 있어요"
SLOT_RECEIPT = "✓ {at} · {color_word} 표시 {n}개"
SLOT_RECEIPT_PARTIAL = "! {at} · {placed}/{expected}개만 넣음"
SLOT_RECEIPT_UNDONE = "↶ {at} 뺐어요"
SLOT_RECEIPT_STOPPED = "멈췄어요"
SLOT_RECEIPT_NONE = "{at} · 찾은 곳 없음"
SLOT_RECEIPT_WAITING = "카드를 확인해 주세요"
SLOT_RECEIPT_FAILED = "! 끝내지 못했어요"

# ── 진행 (설계 B2.5 3, 부록 A) ──────────────────────────────────────
STAGE_NAMES = ("소리 꺼내는 중", "소리 크기 재는 중", "표시할 곳 고르는 중")
PROGRESS_LINE = "{i}/{n} {stage}"
PROGRESS_ETA_S = "약 {s}초 남음"
PROGRESS_ETA_M = "약 {m}분 남음"
PROGRESS_FREE = "리졸브는 그대로 쓰셔도 돼요"
PROGRESS_SEP = " · "
BTN_STOP = "멈추기"
TIP_STOP = "계산을 멈춰요. 리졸브에는 아직 아무것도 하지 않았어요"
STOPPED = "멈췄어요. 리졸브는 그대로예요"
APPLYING = "넣는 중… {i}/{n} · 취소할 수 없어요"
UNDOING = "빼는 중… · 취소할 수 없어요"
SCANNING = "도우미가 넣은 것을 찾는 중…"
READING = "리졸브에서 다시 읽는 중…"

# ── ⚙ 설정 쪽 (설계 B2.1, B2.2) ─────────────────────────────────────
SETTINGS_TITLE = "'{name}' 설정"
SETTINGS_KIND = "할 일"
SETTINGS_NAME = "버튼 이름"
SETTINGS_ORDER = "순서 바꾸기"
BTN_MOVE_UP = "▲ 위로"
BTN_MOVE_DOWN = "▼ 아래로"
BTN_RESTORE_PREVIOUS = "이전 설정으로 되돌리기"
BTN_SAVE = "저장"
SETTINGS_PREVIEW = "버튼 아래 요약: {summary}"
SETTINGS_NOT_READY = "이 일은 아직 준비 중이에요. 저장하면 버튼에 '준비 중'으로 보여요"
SETTINGS_SAVED = "'{name}' 설정을 저장했어요"
SETTINGS_UNCHANGED = "바뀐 것이 없어요"
SETTINGS_SAVE_FAILED = "버튼 설정을 저장하지 못했어요: {error}"
SETTINGS_RESTORED = "'{name}'을 이전 설정으로 되돌렸어요"
SETTINGS_MOVED = "버튼 순서를 바꿨어요"
PARAM_LABELS = {
    "min_s": "쉰 길이", "pad_s": "앞뒤 여유", "below_lu": "쉰 것으로 볼 크기", "as_range": "구간으로 표시",
    "color": "표시 색", "name": "표시 이름", "max": "최대 개수", "scope": "적용 범위", "above_lu": "튀는 정도",
    "merge_s": "하나로 셀 간격", "target_lufs": "목표 크기", "true_peak": "가장 큰 소리", "peaks": "튀는 소리 누르기",
    "lift": "작은 소리 올리기", "originals": "원래 클립", "mark_tamed": "누른 곳 표시", "marker_color": "표시 색",
}
PARAM_HELP = {
    "min_s": "말이 이보다 오래 끊긴 곳만 찾아요 (0.5~5초)",
    "pad_s": "표시 앞뒤로 이만큼 남겨요",
    "below_lu": "평소 말소리보다 이만큼 작으면 쉰 것으로 봐요 (−50 LUFS보다 작으면 늘 쉰 것)",
    "as_range": "끄면 시작 자리에 점 표시만 넣어요",
    "name": "표시 이름 앞부분 (예: 쉼 2.4초)",
    "max": "이보다 많으면 긴 것(큰 것)부터 넣어요",
    "scope": "In~Out은 리졸브에서 I/O 키로 정한 구간이에요",
    "above_lu": "3dB: 조금 · 6dB: 확실히 · 10dB: 절반쯤으로 들려요",
    "merge_s": "이 간격 안의 튀는 소리는 하나로 셉니다",
}
PARAM_UNITS = {
    "min_s": "초", "pad_s": "초", "merge_s": "초", "below_lu": "dB", "above_lu": "dB", "max": "개",
    "target_lufs": " LUFS", "true_peak": " dBTP",
}
CHOICE_LABELS = {
    "scope": {"whole": "전체", "in_out": "In~Out"},
    "peaks": {"light": "조금", "medium": "보통", "strong": "세게"},
    "lift": {"off": "안 함", "light": "조금", "medium": "보통", "strong": "세게"},
    "originals": {"disable": "꺼 두기", "keep": "그대로 두기"},
}
IN_OUT_LOCKED = "점검 뒤 켜져요"
TIP_IN_OUT_LOCKED = "⋯ → 연결 점검 → [기능 점검]에서 In~Out 읽기가 되는지 확인한 뒤에 켜져요"

# ── 카드 (설계 B4.5) ───────────────────────────────────────────────
CARD_TITLE_FOUND = "다 찾았어요. 넣을까요?"
CARD_TITLE_NONE = "찾은 곳이 없어요"
CARD_NONE_PAUSES = "{min_s}초 넘게 쉰 곳이 없어요. ⚙에서 쉰 길이를 줄여 볼 수 있어요"
CARD_NONE_SPIKES = "평소 말소리보다 {above_lu}dB 넘게 튀는 곳이 없어요"
ROW_WHAT = "할 일"
ROW_WHEN = "언제"
ROW_HOW = "얼마나"
ROW_TRACK = "트랙"
ROW_COUNT = "몇 곳"
ROW_RESOLVE = "리졸브"
ROW_KEEP = "그대로"
ROW_VOICE = "목소리"
WHAT_PAUSES = "{min_s}초 넘게 쉰 곳 표시"
WHAT_SPIKES = "평소 말소리보다 {above_lu}dB 넘게 튀는 곳 표시"
WHEN_WHOLE = "전체 {length}"
WHEN_RANGE = "{a}~{b} 안쪽만"
HOW_PAUSES = "쉰 길이 합계 {total}"
HOW_SPIKES = "평소 말소리보다 최대 {max}dB 큼 (+{max}dB)"
COUNT_LINE = "{n}곳"
COUNT_MORE = "▸ {n}곳 더 보기"
COUNT_LESS = "▾ 접기"
RESOLVE_RANGE = "{color_word} 구간 표시 {n}개"
RESOLVE_POINT = "{color_word} 점 표시 {n}개"
RESOLVE_REPLACE = "이전 {color_word} 표시 {n}개는 빼고 넣어요"
KEEP_MARKERS = "소리·클립·내가 찍은 표시는 그대로예요. 자르지 않아요"
LEN_MIN_SEC = "{m}분 {s}초"
LEN_HOUR_MIN = "{h}시간 {m}분"
LEN_SEC = "{s}초"
VOICE_ROW = "소리 {n} ({why})"
VOICE_REMEMBERED = "지난번 선택"
VOICE_JUST_PICKED = "방금 고름"
BTN_CHANGE_VOICE = "바꾸기"
TIP_CHANGE_VOICE = "목소리를 다시 골라요"
DOUBLED_VOICE = "지금 리졸브에서 목소리가 두 번 들리고 있을 수 있어요"
WARNINGS = {
    "too_many_pauses": "너무 많아요 ({found}곳). 긴 것부터 {cap}곳만 넣어요. 쉰 길이를 늘릴까요? (⚙)",
    "too_many_spikes": "너무 많아요 ({found}곳). 큰 것부터 {cap}곳만 넣어요",
    "missing_files": "이 PC에서 찾을 수 없는 원본 파일 {n}개는 뺐어요",
    "speed": "속도를 바꾼 클립 {n}개는 뺐어요",
    "unreadable_items": "위치를 읽지 못한 클립 {n}개는 뺐어요",
    "offset_mismatch": "클립 {n}개는 시작 위치 값이 서로 달라 원본 기준으로 계산했어요",
    "other_layout": "소리 구성이 다른 녹화 {n}개는 뺐어요 (그 녹화의 목소리를 아직 고르지 않았어요)",
    "unmapped": "어느 소리인지 알 수 없는 클립 {n}개는 뺐어요",
    "in_out_off": "In~Out은 기능 점검 뒤에 켜져요. 이번에는 전체에서 찾았어요",
}
BTN_APPLY = "리졸브에 넣기"
BTN_UNDO_ONE = "되돌리기"
BTN_RESUME = "이어서 넣기"
BTN_REMOVE_PLACED = "넣은 것 빼기"
BTN_REPLAN = "다시 계산"
BTN_FORCE = "그래도 넣기"
BTN_REPLACE = "바꾸기"
BTN_ADD_MORE = "더하기"
BTN_CLOSE = "닫기"
CARD_CANCELLED = "취소했어요. 리졸브는 그대로예요"
CARD_SUPERSEDED = "바뀜 · 새 카드를 봐 주세요"
CARD_REPLACED = "이 표시는 새 표시로 바뀌었어요 (뺐어요)"
TIMELINE_CHANGED = "계산할 때와 타임라인이 달라졌어요"
OTHER_TIMELINE_APPLY = "계산할 때 연 타임라인 '{name}'이 지금 열려 있지 않아요. 그 타임라인을 열고 다시 눌러 주세요"
RERUN_QUESTION = "이전 {color_word} 표시 {n}개를 새것으로 바꿀까요?"

# ── 영수증 (설계 B2.5 6) ────────────────────────────────────────────
RECEIPT_OK = "✓ {at} 넣었어요 · {color_word} 표시 {n}개"
RECEIPT_PARTIAL = "{expected}개 중 {placed}개만 넣었어요"
RECEIPT_NONE = "넣지 못했어요. 리졸브는 그대로예요"
RECEIPT_ERROR = "리졸브의 답: {reason}"
RECEIPT_POINT = "길이 있는 표시가 안 돼서 점 표시로 넣었어요"
RECEIPT_SKIPPED = "이미 있던 {n}개는 다시 넣지 않았어요"
RECEIPT_REPLACED = "이전 {color_word} 표시 {n}개를 뺐어요"
RECEIPT_NO_READBACK = "리졸브에서 다시 읽지 못해 넣었다는 답만 적었어요"
RECEIPT_DUR_DIFF = "표시 길이가 보낸 것과 조금 달라요 (결과 파일에 적었어요)"
RECEIPT_UNDONE = "↶ {at} 뺐어요 · {color_word} 표시 {n}개"
RECEIPT_UNKNOWN = "들어갔는지 아직 몰라요"
CLEAR_RECEIPT_UNKNOWN = "지워졌는지 아직 몰라요"
RECEIPT_UNKNOWN_DETAIL = "리졸브의 답이 끊겼어요. 도우미 꼬리표로 다시 확인해서 이 카드와 되돌리기 목록을 고칠게요"
RECEIPT_CHECKING = "확인하는 중…"
BTN_RECHECK = "다시 확인"
SLOT_RECEIPT_UNKNOWN = "? 확인 중"
UNDO_SKIPPED = "이미 없어서 건너뛴 것 {n}개"
UNDO_LEFT = "{n}개는 빼지 못했어요"
UNDO_FAILED = "빼지 못했어요: {reason}"
UNDO_MISSING = "기록에 없는 일이라 빼지 않았어요"
APPLY_FAILED = "넣지 못했어요: {reason}"
PLAN_FAILED = "계산하지 못했어요: {reason}"
PLAN_REFUSED = {
    "no_project": "리졸브에서 프로젝트를 열어 주세요",
    "no_timeline": "리졸브에서 타임라인을 열어 주세요",
    "probe_copy": "지금 열린 타임라인은 점검용 복사본이에요. 원래 타임라인으로 돌아간 뒤 눌러 주세요",
    "timeline_unreadable": "타임라인의 시작·끝·속도를 읽지 못했어요. [연결 확인] 뒤 다시 눌러 주세요",
    "no_audio_items": "켜진 소리 트랙에 소리 클립이 없어요",
    "files_missing": "소리 클립의 원본 파일을 이 PC에서 찾을 수 없어요",
    "no_streams": "원본 파일에서 소리를 읽을 수 없어요",
    "no_voice_items": "고른 목소리(소리 {n})가 타임라인의 켜진 트랙에 없어요. 카드의 [바꾸기]로 다시 골라 주세요",
    "no_in_out": "리졸브에서 In~Out 구간을 정해 주세요 (I/O 키). 또는 ⚙에서 적용 범위를 '전체'로 바꿔 주세요",
    "not_ready": "이 일은 아직 준비 중이에요",
    "range_outside": "말씀하신 구간이 타임라인 밖이에요",
    "unmapped_tracks": "소리 클립 {n}개가 녹화 파일의 몇 번째 소리인지 알 수 없어요. 녹화 파일을 넣을 때 생긴 소리 트랙을 "
                       "모두 그대로 두고(따로 지우거나 자르지 않고) 다시 눌러 주세요",
}
BTN_PICK_AGAIN = "목소리 다시 고르기"

# ── 목소리 고르기 (설계 B2.3) ───────────────────────────────────────
VOICE_TITLE = "어느 소리가 내 목소리인가요?"
VOICE_INTRO = "녹화 파일 안에 소리가 {n}개 있어요. [▶ 3초 듣기]로 들어 보고 골라 주세요"
VOICE_CHANGED = "소리 모양이 지난번과 달라요. 목소리를 다시 골라 주세요"
VOICE_CHANGE = "목소리를 다시 골라 주세요"
VOICE_MIX = "소리 {n}은 나머지를 모두 섞은 것처럼 보여요 (추정)"
VOICE_STREAM = "소리 {n}"
VOICE_CHANNELS = {1: "모노", 2: "스테레오"}
VOICE_CHANNELS_N = "{n}채널"
VOICE_ON_TIMELINE = "타임라인에서 켜짐"
VOICE_OFF_TIMELINE = "타임라인에 없음"
VOICE_QUIET = "거의 조용함"
VOICE_MIX_MARK = "전체 소리 (추정)"
VOICE_PREVIOUS_MARK = "지난번 선택"
BTN_LISTEN = "▶ 3초 듣기"
BTN_PICK = "이걸로"
VOICE_PICKED = "목소리 = 소리 {n}. 다음부터는 카드에 적어 두고 묻지 않을게요"
LISTENING = "소리 {n}을 트는 중 (3초)"
LISTEN_FAILED = "소리를 꺼내지 못했어요: {reason}"
LISTEN_NO_PLAYER = "이 PC에서 소리를 틀지 못했어요"

# ── 확인 질문 (설계 B7.3 M2, M3) ───────────────────────────────────
M3_STEP = "시험: 리졸브 타임라인의 빈 곳 클릭 → Ctrl+Z 한 번 (시험용 프로젝트에서만)"
M3_QUESTION = "도우미 표시가 어떻게 됐나요?"
M3_ANSWERS = {"all_gone": "모두 사라짐", "some": "일부만", "same": "그대로", "other": "다른 것이 되돌아감"}
M2_QUESTION = "평소 하시는 방법으로 앞부분 한 곳을 잘라 낸 뒤, 뒤쪽 빨간 표시가 같이 움직였나요?"
M2_NOTE = "시험용 타임라인에서만 해 주세요 (안 해도 돼요)"
M2_ANSWERS = {"moved": "같이 움직였어요", "stayed": "그대로예요"}
BTN_LATER = "나중에"
MANUAL_ANSWERED = "답: {answer}"
MANUAL_COUNTS = "다시 읽어 보니 도우미 표시 {found}개 (넣은 것 {expected}개)"
MANUAL_LATER = "⋯ → 연결 점검 → [확인 질문 다시 보기]로 다시 띄울 수 있어요"
BTN_MANUAL_CHECKS = "확인 질문 다시 보기"
TIP_MANUAL_CHECKS = "Ctrl+Z 시험과 자르기 시험 질문을 대화 칸에 다시 띄워요"

# ── 대화 칸 ─────────────────────────────────────────────────────────
CHAT_TITLE = "대화"
CHAT_COLLAPSE = "▾ 대화 접기"
CHAT_EXPAND = "▸ 대화 펴기"
CHAT_GREETING = "버튼을 누르거나 할 일을 적어 주세요. 리졸브에 넣기 전에 꼭 먼저 보여 드려요."
CHAT_PLACEHOLDER = "예: 3분 20초에 표시해줘"
CHAT_SEND = "보내기"
CHAT_SOON = "대화는 곧 열려요. 지금은 위의 자동화 버튼과 ⋯ > 연결 점검을 쓸 수 있어요."
CHAT_BUSY = "지금 하는 일이 끝나면 이어서 할게요"
CHAT_CHECKING = "리졸브에서 타임라인을 확인하는 중…"
CHAT_NO_TIMELINE = "리졸브에서 타임라인을 열어 주세요"
CHAT_NO_TIMELINE_INFO = "타임라인의 시작·끝·속도를 읽지 못했어요. [연결 확인] 뒤 다시 보내 주세요"
CHAT_OLD_SCRIPT = OLD_SCRIPT_HINT
CHAT_PROBE_COPY = "지금 열린 타임라인은 점검용 복사본이에요. 원래 타임라인으로 돌아간 뒤 보내 주세요"
CHAT_NO_MATCH = "이 말은 아직 못 알아들어요. 이렇게 말해 보세요:"
CHAT_CHIPS_HINT = "누르면 입력 칸에 채워요. 고친 뒤 [보내기]를 눌러 주세요"
CHAT_NOT_CONNECTED = "먼저 리졸브와 연결해 주세요 [연결 확인]"
CHAT_BRAIN_LINE = "답하는 쪽: 기본 도우미 (AI 아님) · 무료"
CHAT_ME = "나"
CHAT_HELPER = "도우미"

# ── 대화: 기본 도우미의 답 (설계 B4.2, 부록 A Refusals) ─────────────
# 두뇌(engine/chat/rules.py)는 code와 값만 돌려주고, 글은 여기서 고른다.
CHAT_REPLIES = {
    "studio": "유료판(Studio) 기능이라 무료판에서는 부를 수 없어요. 부르면 리졸브에 창이 떠서 연결이 멈춰요",
    "keyframe": ("리졸브의 키프레임·EQ·컴프레서는 스크립트로 바꿀 수 없어요. "
                 "서서히 줄인 새 소리를 'AI 소리' 트랙에 넣는 방법은 나중 판에서 될 수 있어요"),
    "needs_ai": "웃음소리처럼 어떤 소리인지 가려 듣는 일은 기본 도우미가 못 해요. 큰 소리가 난 곳은 튀는 소리 표시로 찾을 수 있어요:",
    "cut_no_place": ("무료판 리졸브에서는 도우미가 클립을 직접 자르거나 옮길 수 없어요. "
                     "쉬는 곳에 표시를 해 두면 보면서 자르실 수 있어요:"),
    "out_of_scope": ("이건 도우미가 하지 않아요. 할 수 있는 일: 표시 넣기 · 쉬는 곳/튀는 소리 표시 · "
                     "도우미 표시 지우기 · 재생 위치 옮기기 · 되돌리기 · 자동화 버튼에 저장"),
    "not_ready": "원래 클립을 켜고 끄는 일은 아직 안 돼요. '소리 고르게'가 준비되면 같이 해 드릴게요",
    "subtitles_later": "자막 만들기는 아직 준비 중이에요",
    "own_markers": "직접 넣으신 표시는 도우미가 지우지 않아요. 도우미가 넣은 표시만 지울 수 있어요:",
    "find_track": "트랙을 골라서 찾기는 아직 안 돼요. 트랙 없이 말씀하시면 목소리로 고른 소리에서 찾아요:",
    "audio_not_ready": "소리 조절은 아직 안 돼요. 시간을 같이 말씀해 주시면 그 자리에 노란 표시를 해 둘 수 있어요",
    "balance_not_ready": "소리 고르게는 아직 준비 중이에요 (자동화 3)",
    "time_outside": "그 시간은 타임라인 밖이에요 (타임라인 길이 {length})",
    "time_no_playhead": "리졸브의 재생 위치를 읽지 못했어요. [연결 확인] 뒤 다시 보내 주세요",
    "time_bad_tc": "리졸브 시간 '{raw}'을 읽지 못했어요",
    "time_reversed": "끝이 시작보다 앞이에요. 앞의 시간부터 말씀해 주세요",
    "time_in_out": "In~Out 구간은 기능 점검에서 읽을 수 있다고 확인된 뒤에, 쉬는 곳·튀는 소리 찾기에만 써요. 시간으로 말씀해 주세요",
    "time_empty": "시간을 알아듣지 못했어요",
    "time_ambiguous": "시간을 두 가지로 읽을 수 있어요",
}
CHAT_STUDIO_SUBTITLES = "도우미의 '자막 만들기'가 준비되면 그걸로 해 드릴게요"
CHAT_QUESTIONS = {
    "negation": "'{word}'처럼 빼는 말은 아직 알아듣지 못해요. 할 일만 말씀해 주세요:",
    "what_to_do": "무엇을 할까요? 이렇게 말해 보세요:",
    "what_to_do_time": "{time}에 무엇을 할까요?",
    "verb_only": "'{clause}'가 무엇을 가리키는지 모르겠어요. 한 번에 하나씩 다시 말씀해 주세요:",
    "clear_what": "무엇을 지울까요? 도우미가 넣은 표시만 지울 수 있어요:",
    "mark_where": "어디에 표시할까요? 시간이나 '여기'를 같이 말씀해 주세요:",
    "tc_ambiguous": "'{raw}'을 영상 시간({elapsed})과 리졸브 시간({tc})으로 둘 다 읽을 수 있어요. 어느 쪽인가요?",
    "over_gain": "한 번에 키울 수 있는 건 12dB까지예요. 12dB로 할까요?",
    "undo_all": "되돌리기는 방금 넣은 것 하나씩만 해요. 도우미가 넣은 것을 모두 빼려면 이렇게 말해 주세요:",
    "recent_clear": "방금 넣은 것을 통째로 뺄까요, 그 표시를 모두 지울까요? 하나를 골라 다시 말씀해 주세요:",
}
BTN_OVER_GAIN_YES = "12dB로"
CHAT_HELP = (
    "기본 도우미(AI 아님, 무료)가 알아듣는 말이에요. 리졸브에 넣기 전에 꼭 카드로 먼저 보여 드려요.\n"
    "· 표시: 3분 20초에 빨간 표시해줘 / 여기 표시해줘 (메모는 '자막 확인')\n"
    "· 쉬는 곳: 5분~6분에서 2초 넘게 쉰 곳 표시해줘\n"
    "· 튀는 소리: 3분쯤 튀는 소리 표시해줘\n"
    "· 지우기: 도우미가 넣은 파란 표시 지워줘 (내가 찍은 표시는 그대로)\n"
    "· 재생 위치: 3분 20초로 가줘\n"
    "· 되돌리기: 방금 거 취소\n"
    "· 버튼에 저장: 이대로 자동화 3에 저장해줘\n"
    "· 상태: 지금 타임라인 몇 분이야?"
)
CHAT_STATUS = "{timeline} · 길이 {length} · {fps}fps · 재생 위치 {playhead} ({tc})"
CHAT_STATUS_NO_PLAYHEAD = "{timeline} · 길이 {length} · {fps}fps"
CHAT_JUMP_DONE = "재생 위치를 {at}로 옮겼어요 ({tc})"
CHAT_JUMP_PAGE = "리졸브가 미디어나 퓨전 화면이면 재생 위치를 옮길 수 없어요. 편집 화면 등으로 바꿔 주세요"
CHAT_JUMP_OUTSIDE = "그 시간은 타임라인 밖이라 옮기지 않았어요"
CHAT_JUMP_UNCONFIRMED = "옮기라고 했는데 다시 읽어 보니 재생 위치가 {readback}이에요. 리졸브에서 {tc}를 직접 입력해 주세요"
CHAT_JUMP_FAILED = "재생 위치를 옮기지 못했어요: {reason}"
CHAT_JUMP_OTHER_TIMELINE = "이 표시는 '{name}'에 있어요. 그 타임라인을 열고 다시 눌러 주세요"
CHAT_UNDO_NONE = "이 타임라인에는 뺄 것이 없어요"
CHAT_OFFER_DECLINED = "알겠어요. 리졸브는 그대로예요"
CHAT_REFUSED_ALSO = "함께 말씀하신 것 가운데: {text}"
# 입력 칸을 채우는 말 (누르면 채우기만 하고 보내지 않는다)
CHIPS = {
    "example:mark_time": "{time}에 빨간 표시해줘",
    "example:mark_here": "여기 표시해줘",
    "example:range_pauses": "5분~6분에서 2초 넘게 쉰 곳 표시해줘",
    "example:undo": "방금 거 취소",
    "example:pauses": "쉬는 곳 표시해줘",
    "example:spikes": "튀는 소리 표시해줘",
    "example:spikes_at": "{time}쯤 튀는 소리 표시해줘",
    "example:clear_at": "{time}에 있는 도우미 표시 지워줘",
    "example:clear_ours": "도우미가 넣은 파란 표시 지워줘",
    "example:jump_time": "{time}로 가줘",
    "example:remove_all": "도우미가 넣은 거 모두 빼줘",
}
CHIP_LEFTOVER = "나머지도 다시 말하기"
# 때에 맞춘 예문 (설계 B1.5): 연결된 뒤, 넣은 뒤. 누르면 입력 칸만 채운다
CHAT_TRY_CONNECTED = "연결됐어요. 이렇게 적어 볼 수 있어요"
CHIPS_AFTER_CONNECT = ("여기 표시해줘", "2초 넘게 쉰 곳 표시", "튀는 소리 표시해줘")
CHAT_TRY_AFTER_APPLY = "빼고 싶으면 이렇게 적어도 돼요"
CHIPS_AFTER_APPLY = ("방금 거 취소", "표시만 다 지워줘")
CHIP_TC_ELAPSED = "영상 {time}"
CHIP_TC_RESOLVE = "리졸브 시간 {tc}"
SPOKEN_HMS = "{h}시간 {m}분 {s}초"
SPOKEN_MS = "{m}분 {s}초"
SPOKEN_S = "{s}초"

# ── 대화 카드: 값의 출처, 범위 지킴이, 고치기 (설계 B4.3~B4.5) ─────────
PROVENANCE = {"said": "말씀하신 값", "default": "기본값", "setting": "설정값", "found": "도우미가 찾음"}
PROVENANCE_SLOT = "설정값 ({name})"
TAGGED = "{value} · {source}"
GUARD = {
    "outside_range": "요청하신 범위 밖이 바뀌어서 넣을 수 없어요",
    "outside_tracks": "말씀하지 않은 트랙({tracks})이 바뀌어서 넣을 수 없어요",
    "more_than_asked": "요청보다 많아요: {n}곳",
    "whole": "전체 {length}에 적용돼요",
    "half": "타임라인의 {percent}%에 적용돼요",
    "leftover": "못 알아들은 부분: \"{text}\"",
}
CARD_NOTES = {
    "tc_read": "리졸브 시간 {tc} → 영상 {at}로 봤어요",
    "clipped_end": "타임라인 끝까지만 봤어요",
    "around": "말씀하신 시각 앞뒤 {seconds}초 안에서 찾아요",
    "gain_clamped": "한 번에 줄일 수 있는 건 {max}dB까지라 {max}dB로 적었어요",
    "min_s_clamped": "쉰 길이는 {lo}~{hi}초 사이로만 찾을 수 있어서 말씀하신 {said}초 대신 {used}초로 찾아요",
    "above_clamped": "튀는 정도는 {lo}~{hi}dB 사이로만 찾을 수 있어서 말씀하신 {said}dB 대신 {used}dB로 찾아요",
    "too_many_marks": "표시는 한 번에 {cap}개까지만 넣어요 ({n}개 가운데)",
    "count_kept": "말씀하신 대로 큰 것부터 {kept}곳만 넣어요 ({found}곳 가운데)",
}
CARD_TITLE_PROPOSE = "이렇게 넣을까요?"
CARD_TITLE_CLEAR = "이렇게 지울까요?"
CARD_TITLE_CLEAR_NONE = "지울 도우미 표시가 없어요"
CARD_TITLE_UNDO = "이걸 뺄까요?"
CARD_TITLE_SAVE = "자동화 버튼에 저장할까요?"
WHAT_MARK = "{color_word} 표시 '{name}'"
WHAT_MARKS = "표시 {n}개 넣기"
WHEN_POINT = "{at}"
WHEN_SPAN = "{a}~{b}"
WHEN_ITEMS = "{a}부터 {b}까지 {n}곳"
WHEN_AROUND = "{a}~{b} (앞뒤 5초)"
HOW_POINT = "점 표시 (길이 없음)"
HOW_SPAN = "구간 표시, 길이 {length}"
HOW_MIXED = "점 {points}개 · 구간 {spans}개"
TRACK_NONE = "트랙과 상관없는 타임라인 표시"
RESOLVE_MARKS = "{colors} 표시 {n}개"
COLOR_JOIN = "·"
ITEM_LINE = "{at}  {color_word} '{name}'  {tc}"
ITEM_SOURCE = "시각 {at} · 색 {color} · 이름 {name}"
FOUND_BY_HELPER = "도우미가 찾음"
EDIT_TIME = "시각"
EDIT_MIN_S = "쉰 길이"
EDIT_ABOVE = "튀는 정도"
EDIT_DB = "적을 크기"
EDIT_VALUE_S = "{v}초"
EDIT_VALUE_DB = "{v}dB"
BTN_MINUS = "−"
BTN_PLUS = "+"
TIP_MINUS_TIME = "0.1초 앞으로"
TIP_PLUS_TIME = "0.1초 뒤로"
TIP_MINUS_VALUE = "하나 줄이기 (다시 계산해요. 리졸브는 그대로예요)"
TIP_PLUS_VALUE = "하나 늘리기 (다시 계산해요. 리졸브는 그대로예요)"
BTN_VIEW = "리졸브에서 보기"
BTN_VIEW_SHORT = "보기"
TIP_VIEW = "리졸브의 재생 위치만 이곳으로 옮겨요 (편집이 아니에요)"
EDITED = "고침"
SAVE_ROW = "이대로 자동화 버튼에 저장 ▸"
SAVE_SLOT_CHOICE = "자동화 {n} ({name})"
SAVE_DONE = "자동화 {n}을 바꿨어요"
SAVE_RANGE_DROPPED = "구간({a}~{b})은 저장하지 않아요. 버튼은 전체(또는 In~Out)에 적용돼요"
SAVE_NOTHING = "저장할 카드가 없어요. 쉬는 곳이나 튀는 소리 카드가 나온 뒤에 말씀해 주세요"
SAVE_CONFIRM = "자동화 {n}을 '{what}'으로 바꿔요. 지금 설정: {now}"
SAVE_CHOOSE = "어느 버튼에 저장할까요?"
SAVE_KIND_NAME = "{name} ({summary})"
OFFER_TITLES = {
    "cut": "무료판 리졸브에서는 도우미가 클립을 직접 자르거나 옮길 수 없어요. 그 자리에 보라 표시를 해 둘까요?",
    "audio": "구간 소리 조절은 아직 안 돼요. 그 자리에 노란 표시를 해 둘까요?",
}
BTN_OFFER_YES = "표시해 두기"
BTN_OFFER_NO = "괜찮아요"
CLEAR_WHAT_ALL = "도우미가 넣은 표시 지우기"
CLEAR_WHAT_FILTER = "도우미가 넣은 {what} 지우기"
CLEAR_COLORS = "{colors} 표시"
CLEAR_KINDS = {"mark_pauses": "쉬는 곳 표시", "mark_spikes": "튀는 소리 표시", "mark": "대화로 넣은 표시"}
CLEAR_RESOLVE = "도우미 표시 {n}개를 지워요"
CLEAR_KEEP = "내가 찍은 표시와 고르지 않은 도우미 표시는 그대로예요"
CLEAR_STRADDLE = "구간에 걸친 도우미 표시 {n}개는 그대로 둬요"
CLEAR_NONE = "말씀하신 도우미 표시가 없어요 (이 타임라인의 도우미 표시 {n}개)"
CLEAR_RECEIPT = "✓ {at} 지웠어요 · 도우미 표시 {n}개"
CLEAR_RECEIPT_PARTIAL = "{expected}개 중 {n}개만 지웠어요"
CLEAR_RECEIPT_NONE = "지우지 못했어요. 리졸브는 그대로예요"
CLEAR_CLOSED = "이 표시를 넣은 일 {n}개는 되돌리기 목록에서 '뺌'이 됐어요"
CLEAR_UNDONE = "↶ {at} 되돌렸어요 · 지운 도우미 표시 {n}개를 다시 넣었어요"
CLEAR_UNDO_LEFT = "{n}개는 다시 넣지 못했어요"
CLEAR_UNDO_SKIPPED = "이미 있어서 건너뛴 것 {n}개"
UNDO_WHAT_CLEAR = "{request} · 지운 도우미 표시 {n}개를 다시 넣어요"
CLEARING = "지우는 중… · 취소할 수 없어요"
CARD_CLEARED = "이 표시는 '도우미 표시 지우기'로 지웠어요"
UNDO_STARTED = "빼는 중이에요. 결과는 위 카드와 되돌리기 목록에 나와요"
SAVE_SLOT_BUTTON = "자동화 {n}"
JUMPING = "재생 위치를 옮기는 중…"
UNDO_WHAT_MARKS = "{request} · 도우미 표시 {n}개를 빼요"

# ── 아래쪽: 되돌리기 (설계 B6.3) ────────────────────────────────────
BTN_UNDO = "↶ 되돌리기 ▾"
UNDO_NOTHING = "되돌릴 것이 없어요"
UNDO_ENTRY = "{at} {request} ({status})"
UNDO_STATUS = {"applying": "넣는 중", "applied": "넣음", "partial": "일부 넣음", "undone": "뺌",
               "undo_failed": "빼지 못함"}
UNDO_ALL = "도우미가 넣은 것 모두 빼기"
UNDO_HINT = "도우미가 넣은 것은 여기 되돌리기로 빼 주세요"
UNDO_HINT_WARN = "리졸브의 Ctrl+Z는 직접 하신 편집을 되돌릴 수 있어요"
UNDO_CONFIRM_TITLE = "되돌리기"
UNDO_CONFIRM = "이걸 뺄까요? 빠지는 것: {what}"
UNDO_WHAT = "{request} · 도우미 표시 {n}개"
BTN_REMOVE = "빼기"
UNDO_DONE_LINE = "↶ {request}: 도우미 표시 {n}개를 뺐어요"
REMOVE_ALL_TITLE = "도우미가 넣은 것 모두 빼기"
REMOVE_ALL_CONFIRM = "이 타임라인에서 도우미가 넣은 것 모두 빼기 ({n}개)"
REMOVE_ALL_DETAIL = "도우미 표시 {markers}개 · 옛 시험 표시 {legacy}개 · 옛 시험 트랙 {tracks}개. 내가 찍은 표시와 내 클립은 그대로예요"
REMOVE_ALL_NOTHING = "이 타임라인에는 도우미가 넣은 것이 없어요"
EDIT_PAGE_QUESTION = "편집(Edit) 화면으로 바꿔서 뺄까요?"
EDIT_PAGE_DETAIL = "옛 시험 소리 트랙 'AI 도우미 시험'은 편집 화면에서만 뺄 수 있어요. 빼고 나면 원래 화면으로 돌아가요"
BTN_SWITCH_REMOVE = "바꿔서 빼기"
BTN_MARKERS_ONLY = "표시만 빼기"
REMOVE_ALL_DONE = "도우미가 넣은 것을 뺐어요: 도우미 표시 {markers}개 · 옛 시험 표시 {legacy}개"
REMOVE_ALL_TRACK_DONE = "'AI 도우미 시험' 트랙도 뺐어요"
REMOVE_ALL_TRACK_LEFT = "빈 'AI 도우미 시험' 트랙은 직접 지워 주세요"
REMOVE_ALL_TRACK_KEPT = "'AI 도우미 시험' 트랙은 그대로 뒀어요 (표시만 뺐어요)"
REMOVE_ALL_TRACK_PAGE = "편집 화면이 아니어서 'AI 도우미 시험' 트랙은 빼지 못했어요"
REMOVE_ALL_TRACK_FAILED = "'AI 도우미 시험' 트랙을 빼지 못했어요 ({reason})"
REMOVE_ALL_TRACK_NOT_OURS = "'AI 도우미 시험' 트랙에 도우미 것이 아닌 클립이 있어서 그대로 뒀어요"
REMOVE_ALL_LEFT = "도우미 표시 {n}개는 빼지 못했어요"
REMOVE_ALL_OTHER = "확인할 때와 다른 타임라인이 열려 있어서 아무것도 빼지 않았어요"
BTN_REPORT = "결과 저장"
TIP_REPORT = "문제가 있을 때 이 파일을 보내 주세요"

# ── ⋯ 메뉴 ─────────────────────────────────────────────────────────
MENU_CHECK_PAGE = "연결 점검"
MENU_REPORT = "결과 저장"
MENU_SETTINGS = "설정"
MENU_ON_TOP = "항상 위에"
MENU_TEXT_SIZE = "글자 크기"
MENU_TEXT_SCALE = "{scale}%"
MENU_HELP = "도움말"
HELP_TEXT = (
    "1) 리졸브에서 Workspace → Scripts → AI_Helper_Connect를 한 번 눌러 주세요.\n"
    "2) 초록 불(● 연결됨)이 켜지면 자동화 버튼을 쓸 수 있어요.\n"
    "3) 리졸브에 넣기 전에 꼭 먼저 보여 드려요. 넣은 것은 [↶ 되돌리기]로 뺄 수 있어요.\n"
    "4) 문제가 있으면 [결과 저장]을 눌러 생긴 파일을 보내 주세요."
)

# ── 연결 점검 쪽 ─────────────────────────────────────────────────────
CHECK_TITLE = "연결 점검"
BTN_BACK = "← 돌아가기"
CHECK_INTRO = "시험은 새 프로젝트에서 해 주세요. 영상 하나를 타임라인에 올려 두면 됩니다."
BTN_PROBE = "기능 점검"
TIP_PROBE = "점검용 복사본에서 리졸브 기능이 되는지 확인해요 (1~2분)"
BTN_TEST_MARKER = "표시 찍기 시험"
BTN_TEST_AUDIO = "소리 넣기 시험"
BTN_TEST_CLEANUP = "시험 흔적 지우기"
TIP_TEST_MARKER = "재생 위치(빨간 세로줄)에 노란 표시를 하나 넣습니다."
TIP_TEST_AUDIO = "새 오디오 트랙을 만들고 재생 위치에 3초짜리 '삐' 소리를 넣습니다."
TIP_TEST_CLEANUP = "이 창이 넣은 시험 표시와 시험 오디오 트랙만 지웁니다. 트랙은 편집(Edit) 화면에서만 지웁니다."
LOG_PLACEHOLDER = "한 일과 결과가 여기에 나옵니다."
BTN_DELETE_LEFTOVER = "남은 점검용 복사본 지우기"
PROBE_CONFIRM_TITLE = "기능 점검"
PROBE_CONFIRM = "시험용 프로젝트에서 눌러 주세요. 점검용 복사본을 잠깐 만들었다가 지워요 (1~2분). 내 타임라인은 건드리지 않아요"
BTN_START = "시작"
BTN_CANCEL = "취소"
BTN_DELETE = "지우기"
LEFTOVER_CONFIRM_TITLE = "점검용 복사본"
LEFTOVER_CONFIRM = ("점검용 복사본 '{name}'을 지울까요?\n"
                    "그 복사본이 지금 열려 있으면 원래 타임라인으로 옮긴 뒤 지워요. 다른 것은 바꾸지 않아요")
LEFTOVER_DELETED = "점검용 복사본을 지웠어요"
LEFTOVER_CHANGED = "이 복사본은 점검 때와 달라져 있어서 지우지 않았어요. 필요 없으면 직접 지워 주세요"
LEFTOVER_NO_STATE = "점검 기록이 없어 도우미가 지우지 않아요. 필요 없으면 리졸브에서 직접 지워 주세요"
LEFTOVER_OTHER = "복사본을 지우지 못했어요 ({reason}). 필요 없으면 리졸브에서 직접 지워 주세요"
LEFTOVER_OTHER_PROJECT = "점검용 복사본은 '{project}' 프로젝트에 있어요. 그 프로젝트를 연 뒤 다시 눌러 주세요"
LEFTOVER_UNKNOWN = "리졸브의 타임라인 목록을 읽지 못해 지우지 않았어요. 잠시 뒤 다시 눌러 주세요"
LEFTOVER_OPEN_PROJECT = "다른 프로젝트"
PROBE_SAVING = "기능 점검 전에 결과를 먼저 저장하는 중…"
PROBE_RUNNING = "기능 점검 중… {stage}"
PROBE_STAGE_NAMES = {
    "read": "읽기 점검", "C1": "1/8 복사본 만들기", "C2": "2/8 트랙 끄기", "C3": "3/8 클립 끄기",
    "C4": "4/8 구간 표시", "C5": "5/8 재생 위치", "C6": "6/8 다시 넣기", "C7": "7/8 새 소리 가져오기",
    "C8": "8/8 정리",
}
PROBE_DONE = "기능 점검을 마쳤어요. 됨 {ok}개 · 안 됨 {bad}개. [결과 저장]을 눌러 파일을 보내 주세요"
PROBE_DONE_LEFTOVER = "기능 점검을 마쳤지만 점검용 복사본이 남았어요. 연결 점검에서 지울 수 있어요"
PROBE_REFUSED = {
    "no_timeline": "리졸브에서 타임라인을 열어 주세요",
    "on_probe_copy": "지금 열린 타임라인이 점검용 복사본이에요. 원래 타임라인으로 돌아간 뒤 해 주세요",
    "leftover": "지난 점검의 복사본이 남아 있어요. 먼저 [남은 점검용 복사본 지우기]를 해 주세요",
}
PROBE_FAILED = "기능 점검을 끝내지 못했어요: {reason}"

# ── 창 메시지 ───────────────────────────────────────────────────────
REQUESTING = "{title}: 리졸브에 요청하는 중..."
STEP_LINE = "{title}: {result} - {summary}"
RESULT_OK = "됨"
RESULT_BAD = "안 됨"
COLLECTING = "결과를 모으는 중..."
REPORT_SAVED = "결과를 저장했습니다. 이 파일을 보내 주세요.\n{path}"
REPORT_SAVED_LOG = "결과 저장: {path}"
REPORT_FAILED = "결과를 저장하지 못했습니다: {error}"
NON_ASCII_MAILBOX = "우체통 폴더 경로에 영문이 아닌 글자가 있어 리졸브가 이 폴더를 못 읽을 수 있습니다: {path}"
UNREAD_TEXT = (
    "리졸브가 답을 적은 것 같은데 이 창이 그 답을 읽지 못했습니다. 리졸브 설정 파일을 자꾸 건드리지 않도록 "
    "자동 확인을 멈췄습니다. [결과 저장]을 눌러 바탕 화면에 생긴 파일을 보내 주세요."
)
SLOW_ANSWER_TEXT = "리졸브의 답이 늦게 옵니다. 자동 확인에서 답을 더 오래 기다립니다."
AUTO_PROBLEM = "자동 연결 확인 중 문제: {message}"
OLD_SCRIPT_LOG = f"리졸브에서 예전 스크립트({{version}})가 돌고 있습니다. Workspace → Scripts → {SCRIPT_NAME}를 한 번 더 눌러 주세요."
AUTO_NOTE_UNREAD = "Fusion.prefs가 바뀌었는데 답을 읽지 못해 자동 확인을 멈춤"
RECONCILED = "지난번에 넣다가 멈춘 것을 확인했어요: {summary}"
RECONCILED_UNDONE = "넣다가 멈춘 것은 들어가지 않았어요"
RECONCILED_APPLIED = "모두 들어가 있었어요 ({found}개)"
RECONCILED_PARTIAL = "일부만 들어갔어요 ({found}/{expected}개). 카드나 되돌리기 목록에서 빼거나 이어서 넣을 수 있어요"
RECONCILED_CLEAR = "지난번에 지우다가 멈춘 것을 확인했어요: {summary}"
RECONCILED_CLEAR_APPLIED = "모두 지워져 있었어요 ({found}개)"
RECONCILED_CLEAR_PARTIAL = "일부만 지워졌어요 ({found}/{expected}개). 되돌리기로 다시 넣을 수 있어요"
RECONCILED_CLEAR_UNDONE = "지우다가 멈춘 것은 지워지지 않았어요"
SETTINGS_MOVED_BAD = "설정 파일을 읽지 못해 옮겨 두고 기본값으로 시작했어요: {path}"

STEP_TITLES = {
    "connect": "연결 확인",
    "marker": "표시 찍기 시험",
    "audio": "소리 넣기 시험",
    "cleanup": "시험 흔적 지우기",
    "probe": "기능 점검",
    "switch": "원래 타임라인으로",
    "leftover": "점검용 복사본 지우기",
    "slot": "자동화 버튼",
    "plan": "자동화 버튼 계산",
    "apply": "리졸브에 넣기",
    "undo": "되돌리기",
    "resume": "이어서 넣기",
    "scan": "도우미가 넣은 것 찾기",
    "remove_all": "도우미가 넣은 것 모두 빼기",
    "manual": "확인 질문",
    "listen": "3초 듣기",
}
