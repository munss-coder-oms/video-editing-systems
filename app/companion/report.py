"""결과 파일 (AI도우미_결과_<yyyymmdd-hhmm>.txt, 버튼 이름은 그대로 [결과 저장]).

사용자가 이 파일을 그대로 보내 주면, 사용자 PC의 리졸브에서 어떤 함수가 되고 안 되는지 알 수 있다.
맨 위에는 단계마다 됨/안 됨을 쉬운 말로, 아래에는 리졸브가 준 답을 그대로(JSON) 적는다.
2판(v2)에는 화면 크기, 기능 점검(C1~C8)과 점검 기록, 소리 파일의 스트림마다 ffprobe 값(start_time 포함),
자동화 버튼 설정과 순서, 일지 요약, 걸린 시간, 답하는 쪽을 더 적는다.
사용자가 일부러 보내는 파일이라 경로는 가리지 않고 그대로 적는다. 화면(Qt) 코드는 없다.

결과를 모으는 일(collect_env)은 파일만 읽는다. 리졸브에 묻지 않으므로 작업 중에도 저장할 수 있다.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import platform
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from engine import __version__ as engine_version
from engine.resolve_link import SCRIPT_NAME, SCRIPT_VERSION
from engine.resolve_link.protocol import REQUEST_FILENAME, parse_responses

REPORT_PREFIX = "AI도우미_결과_"
REPORT_TITLE = "AI 편집 도우미 - 결과"
MAX_FFPROBE_FILES = 3  # 결과 파일에 스트림 정보를 적는 소리 파일 수 (ffprobe는 파일마다 1초 안팎)
README_LINES = 40  # 리졸브 Scripting README.txt에서 적는 줄 수

# 결과 파일 맨 위에 적는 단계 (이름, 제목)
STEPS = (
    ("connect", "① 연결"),
    ("marker", "② 표시 찍기"),
    ("audio", "③ 소리 넣기"),
    ("cleanup", "시험 흔적 지우기"),
)
# 그 밖의 일 (한 일 목록과 리졸브의 답 제목)
TITLES = dict(STEPS, probe="기능 점검", switch="원래 타임라인으로", leftover="점검용 복사본 지우기",
              slot="자동화 버튼", plan="자동화 버튼 계산", apply="리졸브에 넣기", undo="되돌리기",
              remove_all="도우미가 넣은 것 모두 빼기")
MANUAL_TITLES = {"M3": "Ctrl+Z 시험 (M3)", "M2": "자르기 시험 (M2)"}


def report_name(now: Optional[datetime.datetime] = None) -> str:
    """AI도우미_결과_20260925-1432.txt (저장할 때의 분까지. 같은 분에 다시 저장하면 덮어쓴다)."""
    return f"{REPORT_PREFIX}{(now or datetime.datetime.now()):%Y%m%d-%H%M}.txt"


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


@dataclass
class StepRecord:
    ok: bool
    summary: str
    data: Dict[str, Any]  # 리졸브가 준 답 그대로 (ping, state, marker ...)
    error: Optional[Dict[str, Any]] = None
    at: str = field(default_factory=_now)


class TestSession:
    """창을 연 뒤 한 일과 결과. 결과 파일은 이것으로 만든다."""

    def __init__(self) -> None:
        self.started = _now()
        self.steps: Dict[str, StepRecord] = {}  # 단계마다 마지막 결과 (맨 위 요약용)
        self.records: List[Tuple[str, StepRecord]] = []  # 모든 시도 (같은 단계를 여러 번 해도 답을 다 남긴다)
        self.history: List[str] = []  # 단계를 할 때마다 한 줄 (같은 단계를 여러 번 해도 남는다)
        self.auto_attempts = 0  # 연결 전 자동 확인 횟수
        self.install: Dict[str, Any] = {}  # 스크립트 설치 결과
        self.probe_runs: List[Dict[str, Any]] = []  # 기능 점검 (ProbeRun.to_dict)
        self.timings: List[Tuple[str, float]] = []  # 버튼 작업마다 걸린 시간 (이름, 초)
        self.runs: List[Dict[str, Any]] = []  # 자동화 버튼: 계산·넣기·되돌리기·모두 빼기마다 한 줄
        self.voice: List[Dict[str, Any]] = []  # 목소리 고르기 (물음과 답)
        self.manual: Dict[str, Dict[str, Any]] = {}  # 확인 질문 M2, M3의 답 (M3은 다시 읽은 표시 수도)

    def record(self, name: str, ok: bool, summary: str, data: Dict[str, Any],
               error: Optional[Dict[str, Any]] = None) -> StepRecord:
        rec = StepRecord(ok, summary, data, error)
        self.steps[name] = rec
        self.records.append((name, rec))
        self.history.append(
            f"{rec.at} {TITLES.get(name, name)}: {'됨' if ok else '안 됨'} - {_one_line(summary)}"
        )
        return rec

    def timing(self, name: str, seconds: float) -> None:
        self.timings.append((name, round(float(seconds), 3)))


def step_lines(session: TestSession) -> List[str]:
    """맨 위 요약: "① 연결: 됨 - ..." 꼴."""
    lines = []
    for name, title in STEPS:
        rec = session.steps.get(name)
        if rec is None:
            lines.append(f"{title}: 아직 안 함")
        else:
            lines.append(f"{title}: {'됨' if rec.ok else '안 됨'} - {_one_line(rec.summary)}")
    return lines


def app_version(root: Optional[Path] = None) -> str:
    """git 짧은 해시. ZIP으로 받아 .git이 없으면 "unknown" (git 프로그램 없이 파일만 읽는다)."""
    root = root if root is not None else Path(__file__).resolve().parents[2]
    git = root / ".git"
    try:
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        sha = head
        if head.startswith("ref:"):
            ref = head[4:].strip()
            ref_file = git / ref
            if ref_file.is_file():
                sha = ref_file.read_text(encoding="utf-8").strip()
            else:
                sha = ""
                for line in (git / "packed-refs").read_text(encoding="utf-8").splitlines():
                    parts = line.split()
                    if len(parts) == 2 and parts[1] == ref:
                        sha = parts[0]
                        break
        if re.fullmatch(r"[0-9a-f]{40}", sha):
            return sha[:7]
    except (OSError, UnicodeDecodeError):
        pass
    return "unknown"


# 결과 파일의 "코드 지문"에 넣는 파일 (ZIP으로 받아 git 정보가 없어도 어떤 코드가 돌았는지 알 수 있게)
_FINGERPRINT_GLOBS = (
    "resolve_scripts/*.lua",
    "engine/resolve_link/*.py",
    "app/companion/*.py",
    "app/__main__.py",
)


def code_fingerprint(root: Optional[Path] = None) -> str:
    """리졸브 연결 코드의 짧은 지문 (sha1 앞 12자리). 파일을 하나도 못 읽으면 "unknown"."""
    root = root if root is not None else Path(__file__).resolve().parents[2]
    digest = hashlib.sha1()
    count = 0
    for pattern in _FINGERPRINT_GLOBS:
        for path in sorted(root.glob(pattern)):
            try:
                data = path.read_bytes()
            except OSError:
                continue
            # 줄 끝(CRLF/LF)이 달라도 같은 코드면 같은 지문
            digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
            digest.update(data.replace(b"\r\n", b"\n") + b"\0")
            count += 1
    return digest.hexdigest()[:12] if count else "unknown"


def _qt_versions() -> str:
    try:
        import PySide6
        from PySide6.QtCore import qVersion

        return f"PySide6 {PySide6.__version__} (Qt {qVersion()})"
    except Exception as exc:  # 결과 파일은 어떤 경우에도 만든다
        return f"PySide6 불러오기 실패: {exc}"


def system_lines() -> List[str]:
    windows = platform.platform()
    if sys.platform == "win32":
        try:
            v = sys.getwindowsversion()
            windows += f" (빌드 {v.build})"
        except Exception:
            pass
    return [
        f"날짜: {_now()}",
        f"앱 버전: {app_version()} (엔진 {engine_version}, 스크립트 {SCRIPT_VERSION})",
        f"코드 지문: {code_fingerprint()}",
        f"앱 폴더: {Path(__file__).resolve().parents[2]}",
        f"윈도우: {windows}",
        f"파이썬: {sys.version.split()[0]} ({sys.executable})",
        f"화면 라이브러리: {_qt_versions()}",
    ]


def find_fatal_responses(paths: Iterable[Path]) -> List[Dict[str, Any]]:
    """Fusion.prefs에 남은 번호 0번 답 (스크립트가 시작하자마자 멈춘 이유: loadfile 없음 등)."""
    found = []
    for path in paths:
        try:
            text = Path(path).read_bytes().decode("latin-1")
        except OSError:
            continue
        for resp in parse_responses(text):
            if resp.id == 0:
                found.append({"file": str(path), "owner": resp.owner, "answer": resp.data})
    return found


# ── 결과를 모으기 (파일만 읽는다: 짧은 작업 스레드에서) ─────────────────────

_MAILBOX_HEX_RE = re.compile(r'AIH\.MAILBOX_HEX\s*=\s*"([0-9A-Fa-f]*)"')
_VERSION_RE = re.compile(r'AIH\.VERSION\s*=\s*"([^"\r\n]*)"')
_CLAIM_RE = re.compile(r'\bClaim\s*=\s*"?(\d+)"?')


def _stamp(seconds: float) -> str:
    return datetime.datetime.fromtimestamp(seconds).strftime("%Y-%m-%d %H:%M:%S")


def script_details(paths, mailbox) -> List[Dict[str, Any]]:
    """설치된 스크립트마다 안에 적힌 판과 우체통 (예전 스크립트나 다른 우체통을 누른 것인지 알아보려고)."""
    out = []
    for path in paths:
        info: Dict[str, Any] = {"file": str(path)}
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            info["error"] = str(exc)
            out.append(info)
            continue
        m, v = _MAILBOX_HEX_RE.search(text), _VERSION_RE.search(text)
        info["version"] = v.group(1) if v else None
        info["mailbox"] = None
        if m and len(m.group(1)) % 2 == 0:
            # 설치 프로그램과 같은 규칙: 윈도우는 ANSI 코드 페이지, 그 밖은 UTF-8
            encoding = "mbcs" if sys.platform == "win32" else "utf-8"
            info["mailbox"] = bytes.fromhex(m.group(1)).decode(encoding, "replace")
        info["same_mailbox"] = info["mailbox"] is not None and (
            os.path.normcase(info["mailbox"]) == os.path.normcase(str(mailbox)))
        out.append(info)
    return out


def prefs_details(bridge, paths) -> List[Dict[str, Any]]:
    """Fusion.prefs 후보마다 바뀐 시각, 크기, Claim, 들어 있는 답 (답을 기다리다 그만둔 요청의 답이면 late)."""
    try:
        timed_out = dict(getattr(bridge, "timed_out", None) or {})
    except RuntimeError:  # 작업 스레드가 마침 고치는 중: 늦은 답 표시만 빠진다
        timed_out = {}
    note_late = getattr(bridge, "note_late_answers", None)
    out = []
    for path in paths:
        info: Dict[str, Any] = {"file": str(path)}
        try:
            st = Path(path).stat()
            text = Path(path).read_bytes().decode("latin-1")
        except OSError as exc:
            info["error"] = str(exc)
            out.append(info)
            continue
        info["mtime"], info["size"] = _stamp(st.st_mtime), st.st_size
        claims = _CLAIM_RE.findall(text)
        info["claim"] = claims[-1] if claims else None
        responses = parse_responses(text)
        if note_late is not None:
            try:
                note_late(Path(path), responses)
            except RuntimeError:
                pass
        info["responses"] = [
            {"id": r.id, "owner": r.owner, "ok": r.data.get("ok"), "error": r.data.get("error"),
             "late": r.id in timed_out}
            for r in responses
        ]
        out.append(info)
    return out


def request_file_info(mailbox) -> Dict[str, Any]:
    """우체통에 요청 파일이 남아 있는지 (창이 답을 기다리는 중이 아니면 보통 없다)."""
    path = Path(mailbox) / REQUEST_FILENAME
    try:
        st = path.stat()
        text = path.read_text(encoding="ascii", errors="replace")
    except OSError:
        return {"file": str(path), "exists": False}
    return {"file": str(path), "exists": True, "age_seconds": round(time.time() - st.st_mtime, 1),
            "size": st.st_size, "text": text[:300]}


def stream_info(paths: Sequence[str], limit: int = MAX_FFPROBE_FILES) -> List[Dict[str, Any]]:
    """소리 파일마다 ffprobe로 본 오디오 스트림 (번호, 코덱, 채널, 이름, start_time). 파일은 limit개까지."""
    from engine.probe import probe

    out: List[Dict[str, Any]] = []
    seen = set()
    for raw in paths:
        if not isinstance(raw, str) or not raw or raw in seen:
            continue
        seen.add(raw)
        if len(out) >= limit:
            break
        info: Dict[str, Any] = {"file": raw}
        try:
            media = probe(raw)
        except FileNotFoundError:
            info["error"] = "파일 없음 (이 PC에서 그 경로를 찾지 못함)"
        except Exception as exc:  # noqa: BLE001 - ffprobe가 없거나 깨진 파일: 이유만 적는다
            info["error"] = f"{type(exc).__name__}: {_one_line(exc)[:300]}"
        else:
            info.update({
                "duration": media.duration, "format_start": media.format_start,
                "video_start": media.video_start if media.has_video else None,
                "streams": [
                    {"index": t.index, "codec": t.codec, "channels": t.channels, "sample_rate": t.sample_rate,
                     "title": t.title, "language": t.language, "start_time": t.start, "decodable": t.decodable}
                    for t in media.audio_tracks
                ],
            })
        out.append(info)
    return out


def scripting_readme(lines: int = README_LINES) -> Optional[Dict[str, Any]]:
    """%PROGRAMDATA%\\Blackmagic Design\\DaVinci Resolve\\Support\\Developer\\Scripting\\README.txt 앞부분."""
    base = os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
    path = Path(base) / "Blackmagic Design" / "DaVinci Resolve" / "Support" / "Developer" / "Scripting" / "README.txt"
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            head = [next(f, "").rstrip("\r\n") for _ in range(lines)]
    except OSError:
        return None
    while head and head[-1] == "":
        head.pop()
    return {"file": str(path), "head": head}


def saved_records(state_root: Optional[Path], caps_key: Optional[Tuple[Any, Any]] = None) -> Dict[str, Any]:
    """앱이 저장해 둔 것: 기능 점검 기록(caps), 남은 복사본 기록(probe_state), 일지 요약 20개."""
    from engine.edits.journal import all_journals
    from engine.resolve_link.caps import CapabilityStore
    from engine.resolve_link.probe import ProbeStateStore

    out: Dict[str, Any] = {}
    root = Path(state_root) if state_root is not None else None
    caps_dir = root / "caps" if root is not None else None
    try:
        if caps_key is not None and (caps_key[0] or caps_key[1]):
            caps = CapabilityStore(caps_dir).load(caps_key[0], caps_key[1])
            out["caps"] = {"file": str(caps.path), "lines": caps.summary_lines(), "data": caps.data}
        state = ProbeStateStore(caps_dir / "probe_state.json" if caps_dir is not None else None).load()
        out["probe_state"] = state.to_dict() if state is not None else None
        summaries: List[Dict[str, Any]] = []
        for journal in all_journals(root):
            for row in journal.summaries(20):
                summaries.append(dict(row, timeline=journal.timeline.get("name"), key=journal.key))
        out["journals"] = summaries[-20:]
    except Exception as exc:  # noqa: BLE001 - 기록을 못 읽어도 결과 파일은 만든다
        out["records_error"] = f"{type(exc).__name__}: {exc}"
    return out


def collect_env(bridge, out: Dict[str, Any], *, audio_paths: Sequence[str] = (),
                state_root: Optional[Path] = None, caps_key: Optional[Tuple[Any, Any]] = None) -> None:
    """결과 파일에 넣을 설치·파일 상태. 리졸브에는 묻지 않고 파일만 읽는다 (작업 중에도 저장할 수 있게).

    윈도우 판을 읽을 때 파이썬이 'ver' 명령을 따로 실행한다 (창에서 하면 1.2초 멈춤, 2026-09-25).
    그래서 창이 아니라 짧은 작업 스레드에서 모은다.
    """
    from engine.resolve_link.bridge import prefs_backups
    from engine.resolve_link.paths import installed_scripts, resolve_exe_versions, resolve_utility_dirs

    out["system"] = system_lines()
    scripts = installed_scripts()
    out["installed_scripts"] = [str(p) for p in scripts]
    out["script_details"] = script_details(scripts, bridge.mailbox)
    out["utility_dirs"] = [str(p) for p in resolve_utility_dirs()]
    out["resolve_exe"] = resolve_exe_versions()
    candidates = list(bridge.prefs_candidates())
    out["prefs_candidates"] = [str(p) for p in candidates]
    out["prefs_details"] = prefs_details(bridge, candidates)
    out["fatal"] = find_fatal_responses(candidates)
    out["backups"] = [str(p) for p in prefs_backups(bridge.backup_dir())]
    out["request"] = request_file_info(bridge.mailbox)
    out["scripting_readme"] = scripting_readme()
    out.update(saved_records(state_root, caps_key))
    out["ffprobe"] = stream_info(list(audio_paths))


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str)


def _json1(value: Any) -> str:
    """한 줄 JSON (짧은 값)."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _probe_lines(run: Dict[str, Any]) -> List[str]:
    """기능 점검 한 번: 단계마다 됨/안 됨, 지문, C7 가져오기, C8 정리."""
    lines = [f"시작: {run.get('started_at')}"]
    if run.get("refused"):
        lines.append(f"하지 않음: {run['refused']}")
    read = run.get("read") or {}
    if read:
        exists = read.get("exists") if isinstance(read.get("exists"), dict) else {}
        missing = sorted(k for k, v in exists.items() if v is False)
        lines.append(f"읽기 점검(probe_read): 함수 {len(exists)}개 확인, 없음 {len(missing)}개"
                     + (f" ({', '.join(missing[:20])})" if missing else "")
                     + (f", 오류 {read.get('error')}" if read.get("error") else ""))
    for stage, r in sorted((run.get("stages") or {}).items()):
        if not isinstance(r, dict):
            continue
        detail = r.get("detail") if isinstance(r.get("detail"), dict) else {}
        extra = []
        if r.get("fingerprint"):
            extra.append(f"지문 {r['fingerprint']}")
        if stage == "C7":
            # 새로 가져왔는지, 리졸브가 본 클립 속성(FPS/Duration/Frames), 놓인 길이(GetEnd - GetStart)
            for key in ("imported", "requested_frames", "placed_frames", "length_ok"):
                if key in detail:
                    extra.append(f"{key}={detail[key]}")
            if isinstance(detail.get("clip"), dict):
                extra.append("clip=" + json.dumps(detail["clip"], ensure_ascii=False, sort_keys=True, default=str))
        if r.get("error"):
            extra.append(f"오류 {r.get('error')}")
        lines.append(f"{stage}: {'됨' if r.get('ok') else '안 됨'}" + (f" ({', '.join(map(str, extra))})" if extra else ""))
    cleanup = run.get("cleanup") or {}
    if cleanup:
        d = cleanup.get("detail") if isinstance(cleanup.get("detail"), dict) else {}
        lines.append(f"C8 정리: 복사본 {'지움' if d.get('deleted') else '남음'}"
                     f"{' (' + str(d.get('reason')) + ')' if d.get('reason') else ''}, "
                     f"점검용 소리 클립 {'지움' if d.get('clip_deleted') else '안 지움'}")
    lines.append(f"점검용 복사본 남음: {'예' if run.get('leftover') else '아니오'}")
    if run.get("seconds"):
        lines.append("걸린 시간(초): " + ", ".join(f"{k} {v}" for k, v in run["seconds"].items()))
    return lines


def build_report(session: TestSession, link: Dict[str, Any], env: Dict[str, Any]) -> str:
    """결과 파일 전체 글.

    link: 창과 브리지가 기억하는 것 (우체통, 답한 Fusion.prefs, 스크립트 판, 화면 크기, 자동화 버튼 ...)
    env: 결과를 저장할 때 살펴본 파일 (설치된 스크립트, Fusion.prefs 후보, 0번 답, ffprobe ...)
    """
    lines = [REPORT_TITLE, "=" * 40, ""]
    lines += step_lines(session)
    for run in session.probe_runs[-1:]:
        stages = run.get("stages") or {}
        ok = sum(1 for r in stages.values() if isinstance(r, dict) and r.get("ok"))
        lines.append(f"기능 점검: {'하지 않음 (' + str(run['refused']) + ')' if run.get('refused') else f'됨 {ok}개 · 안 됨 {len(stages) - ok}개'}")
    lines += ["", "이 파일을 그대로 보내 주세요.", "", "[이 PC]"]
    # 보통은 작업 스레드에서 모아 둔 것 (collect_env). 없을 때만 여기서 읽는다.
    lines += env.get("system") or system_lines()
    screen = link.get("screen")
    if screen:
        lines += ["", "[화면]", _json1(screen)]
    mailbox = link.get("mailbox")
    lines += [
        "",
        "[연결]",
        f"창을 연 시각: {session.started}",
        f"지금 연결됨: {'예' if link.get('connected') else '아니오'}",
        f"연결 상태: {link.get('status') or '모름'}",
        f"연결 전 자동 확인 횟수: {session.auto_attempts}",
        f"우체통 폴더: {mailbox}",
    ]
    if isinstance(mailbox, str) and mailbox and not mailbox.isascii():
        lines.append("  (영문 경로가 아닙니다. 리졸브 안의 스크립트가 이 폴더를 못 열 수 있습니다)")
    if link.get("mailbox_long"):
        lines.append(f"우체통 폴더(긴 이름): {link['mailbox_long']}")
    if link.get("auto_note"):
        lines.append(f"자동 확인: {link['auto_note']}")
    known = link.get("known_ops")
    lines += [
        f"답한 Fusion.prefs: {link.get('prefs_path') or '아직 답 없음'}",
        f"리졸브에서 도는 스크립트 판: {link.get('script_version') or '모름'} (설치한 판: {SCRIPT_VERSION})",
        f"스크립트가 할 수 있는 일(ops): {', '.join(sorted(known)) if known else '모름'}",
        f"스크립트 반복 표시(owner): {link.get('owner') or '모름'}",
        f"요청 수: {link.get('request_count', '모름')}, 다시 읽은 수: {link.get('read_retries', '모름')}, "
        f"큰 답 뒤 확인 ping: {link.get('cleanup_pings', '모름')}",
        f"확인 횟수: 창을 다시 볼 때 {link.get('focus_pings', 0)}번, Resolve.exe 보기 {link.get('process_checks', 0)}번",
        link.get("brain") or "답하는 쪽: 모름",
        "",
        "[스크립트 설치]",
    ]
    install = session.install or {}
    if install.get("error"):
        lines.append(f"설치 실패: {install['error']}")
    elif install.get("message"):
        lines.append(install["message"])
    for path in env.get("installed_scripts") or []:
        lines.append(f"설치된 파일: {path}")
    for info in env.get("script_details") or []:
        lines.append(f"  {info.get('file')}: 판 {info.get('version') or '모름'}, 우체통 {info.get('mailbox') or '모름'}"
                     f"{'' if info.get('same_mailbox') else ' (이 창의 우체통과 다름)'}")
    if not env.get("installed_scripts"):
        lines.append(f"설치된 {SCRIPT_NAME}.lua를 찾지 못했습니다.")
    for path in env.get("utility_dirs") or []:
        lines.append(f"스크립트 메뉴 폴더 후보: {path}")
    for exe, version in env.get("resolve_exe") or []:
        lines.append(f"설치된 리졸브: {exe} (판 {version or '모름'})")
    lines += ["", "[Fusion.prefs 후보]"]
    details = {d.get("file"): d for d in env.get("prefs_details") or []}
    for path in env.get("prefs_candidates") or []:
        lines.append(str(path))
        d = details.get(str(path))
        if d and d.get("error"):
            lines.append(f"  읽지 못함: {d['error']}")
        elif d:
            lines.append(f"  바뀐 시각 {d.get('mtime')}, 크기 {d.get('size')}, Claim {d.get('claim') or '없음'}")
            for resp in d.get("responses") or []:
                lines.append(f"  답 {resp.get('id')} (주인 {resp.get('owner')}): "
                             f"{'ok' if resp.get('ok') else resp.get('error')}{' - 늦게 온 답' if resp.get('late') else ''}")
    if not env.get("prefs_candidates"):
        lines.append("찾지 못했습니다.")
    for path in env.get("backups") or []:
        lines.append(f"백업: {path}")
    request = env.get("request")
    if request:
        lines += ["", "[우체통의 요청 파일]", _json(request)]
    if env.get("fatal"):
        lines += ["", "[스크립트가 시작하자마자 멈춘 기록 (0번 답)]", _json(env["fatal"])]
    if env.get("error"):
        lines += ["", f"[살펴보다 난 오류] {env['error']}"]

    if link.get("timeline"):
        lines += ["", "[리졸브에서 열려 있는 것 (마지막으로 읽은 것)]", _json(link["timeline"])]
    if env.get("ffprobe"):
        lines += ["", "[소리 파일의 스트림 (ffprobe)]"]
        for f in env["ffprobe"]:
            lines.append(str(f.get("file")))
            if f.get("error"):
                lines.append(f"  {f['error']}")
                continue
            lines.append(f"  길이 {f.get('duration')}초, 파일 시작 {f.get('format_start')}, 영상 시작 {f.get('video_start')}")
            for st in f.get("streams") or []:
                lines.append(f"  소리 {st.get('index')}: {st.get('codec')} {st.get('channels')}ch {st.get('sample_rate')}Hz"
                             f", 이름 {st.get('title') or '-'}, start_time {st.get('start_time')}")
    elif link.get("audio_paths") is not None:
        lines += ["", "[소리 파일의 스트림 (ffprobe)]", "타임라인의 소리 클립 경로를 아직 읽지 못했습니다."]

    lines += ["", "[기능 점검]"]
    if session.probe_runs:
        for i, run in enumerate(session.probe_runs, 1):
            lines.append(f"-- {i}번째")
            lines += _probe_lines(run)
    else:
        lines.append("이번에는 하지 않았습니다.")
    caps = env.get("caps")
    if caps:
        lines += ["", f"[기능 점검 기록] {caps.get('file')}"]
        lines += caps.get("lines") or []
    if env.get("probe_state"):
        lines += ["", "[남은 점검용 복사본 기록 (probe_state.json)]", _json(env["probe_state"])]
    if link.get("slots"):
        lines += ["", "[자동화 버튼 (보이는 순서)]"]
        for s in link["slots"]:
            lines.append(f"{s.get('slot')}번 {s.get('name')} ({s.get('kind')}): {_json1(s.get('params'))}"
                         f"{' / 이전 설정 있음' if s.get('previous') else ''}")
    lines += ["", "[자동화 버튼 실행]"]
    lines += [_json1(row) for row in session.runs] or ["이번에는 하지 않았습니다."]
    lines += ["", "[목소리 고르기]"]
    lines += [_json1(row) for row in session.voice] or ["이번에는 묻지 않았습니다."]
    if link.get("voice"):
        lines.append(f"저장된 목소리: {_json1(link['voice'])}")
    if link.get("perf"):
        lines.append(f"걸린 시간 어림 (1분에 몇 초): {_json1(link['perf'])}")
    if link.get("analysis_cache"):
        lines.append(f"음량 분석 저장소: {_json1(link['analysis_cache'])}")
    lines += ["", "[확인 질문]"]
    for key, title in MANUAL_TITLES.items():
        rec = session.manual.get(key)
        lines.append(f"{title}: {_json1(rec) if rec else '답 없음'}")
    lines += ["", "[되돌리기 기록 (최근 20개)]"]
    lines += [_json1(row) for row in env.get("journals") or []] or ["없음"]
    if env.get("records_error"):
        lines.append(f"기록을 읽다 난 오류: {env['records_error']}")
    if session.timings:
        lines += ["", "[걸린 시간 (초)]"]
        lines += [f"{TITLES.get(name, name)}: {sec}" for name, sec in session.timings]
    readme = env.get("scripting_readme")
    if readme:
        lines += ["", f"[리졸브 Scripting README.txt 앞부분] {readme.get('file')}"]
        lines += readme.get("head") or []

    if link.get("late_answers"):
        lines += ["", "[답을 기다리다 그만둔 뒤에 온 답]", _json(link["late_answers"])]

    lines += ["", "[한 일]"]
    lines += session.history or ["아직 아무 단계도 하지 않았습니다."]
    counts: Dict[str, int] = {}
    for name, rec in session.records:
        counts[name] = counts.get(name, 0) + 1
        lines += ["", f"---- {TITLES.get(name, name)} {counts[name]}번째 ({rec.at}) 리졸브의 답 ----"]
        if rec.error:
            lines += ["오류:", _json(rec.error)]
        lines.append(_json(rec.data))
    if link.get("last_response") is not None:
        lines += ["", "---- 리졸브의 마지막 답 (원래 모양) ----", _json(link["last_response"])]
    return "\n".join(lines) + "\n"


def save_report(text: str, folder: Path, now: Optional[datetime.datetime] = None) -> Path:
    """결과 파일을 쓴다. 메모장이 인코딩을 헷갈리지 않게 BOM을 붙인 UTF-8로 저장한다."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / report_name(now)
    path.write_text(text, encoding="utf-8-sig", newline="\r\n" if os.name == "nt" else "\n")
    return path
