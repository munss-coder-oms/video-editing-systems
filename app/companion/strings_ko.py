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
SLOT_SUMMARY = {
    "mark_pauses": "{min_s}초 넘게 쉰 곳에 {color_word} 표시",
    "mark_spikes": "튀는 소리에 {color_word} 표시",
    "balance_voice": "목소리를 {target_lufs} LUFS로 고르게",
}
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
SLOT_SOON = "'{name}'은 다음 판에서 열려요. 지금은 연결만 확인했어요"
SLOT_SETTINGS = "⚙"
TIP_SLOT_SETTINGS = "이 버튼의 설정 바꾸기"
SLOT_SETTINGS_SOON = "설정 바꾸기는 다음 판에서 열려요"
TIP_SLOT_NOT_READY = "'소리 고르게'는 다음 판에서 쓸 수 있어요"

# ── 대화 칸 ─────────────────────────────────────────────────────────
CHAT_TITLE = "대화"
CHAT_COLLAPSE = "▾ 대화 접기"
CHAT_EXPAND = "▸ 대화 펴기"
CHAT_GREETING = "버튼을 누르거나 할 일을 적어 주세요. 리졸브에 넣기 전에 꼭 먼저 보여 드려요."
CHAT_PLACEHOLDER = "예: 3분 20초에 표시해줘"
CHAT_SEND = "보내기"
CHAT_SOON = "대화는 곧 열려요. 지금은 위의 자동화 버튼과 ⋯ > 연결 점검을 쓸 수 있어요."
CHAT_NOT_CONNECTED = "먼저 리졸브와 연결해 주세요 [연결 확인]"
CHAT_BRAIN_LINE = "답하는 쪽: 기본 도우미 (AI 아님) · 무료"
CHAT_ME = "나"
CHAT_HELPER = "도우미"

# ── 아래쪽 ──────────────────────────────────────────────────────────
BTN_UNDO = "↶ 되돌리기 ▾"
UNDO_NOTHING = "되돌릴 것이 없어요"
UNDO_ENTRY = "{at} {request} ({status})"
UNDO_SOON = "되돌리기는 다음 판에서 열려요"
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
LEFTOVER_CONFIRM = "점검용 복사본 '{name}'을 지울까요?"
LEFTOVER_DELETED = "점검용 복사본을 지웠어요"
LEFTOVER_CHANGED = "이 복사본은 점검 때와 달라져 있어서 지우지 않았어요. 필요 없으면 직접 지워 주세요"
LEFTOVER_NO_STATE = "점검 기록이 없어 도우미가 지우지 않아요. 필요 없으면 리졸브에서 직접 지워 주세요"
LEFTOVER_OTHER = "복사본을 지우지 못했어요 ({reason}). 필요 없으면 리졸브에서 직접 지워 주세요"
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
}
