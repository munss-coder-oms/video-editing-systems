"""연결 시험 결과 파일 (AI도우미_연결시험_결과.txt).

사용자가 이 파일을 그대로 보내 주면, 사용자 PC의 리졸브에서 어떤 함수가 되고 안 되는지 알 수 있다.
맨 위에는 단계마다 됨/안 됨을 쉬운 말로, 아래에는 리졸브가 준 답을 그대로(JSON) 적는다.
사용자가 일부러 보내는 파일이라 경로는 가리지 않고 그대로 적는다. 화면(Qt) 코드는 없다.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import platform
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from engine import __version__ as engine_version
from engine.resolve_link import SCRIPT_NAME, SCRIPT_VERSION
from engine.resolve_link.protocol import parse_responses

REPORT_NAME = "AI도우미_연결시험_결과.txt"

# 결과 파일 맨 위에 적는 단계 (이름, 제목)
STEPS = (
    ("connect", "① 연결"),
    ("marker", "② 표시 찍기"),
    ("audio", "③ 소리 넣기"),
    ("cleanup", "시험 흔적 지우기"),
)


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

    def record(self, name: str, ok: bool, summary: str, data: Dict[str, Any],
               error: Optional[Dict[str, Any]] = None) -> StepRecord:
        rec = StepRecord(ok, summary, data, error)
        self.steps[name] = rec
        self.records.append((name, rec))
        self.history.append(
            f"{rec.at} {dict(STEPS).get(name, name)}: {'됨' if ok else '안 됨'} - {_one_line(summary)}"
        )
        return rec


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


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str)


def build_report(session: TestSession, link: Dict[str, Any], env: Dict[str, Any]) -> str:
    """결과 파일 전체 글.

    link: 브리지가 기억하는 것 (우체통, 답한 Fusion.prefs, 스크립트 판 ...)
    env: 결과를 저장할 때 살펴본 것 (설치된 스크립트, Fusion.prefs 후보, 0번 답 ...)
    """
    lines = ["AI 도우미 - 리졸브 연결 시험 결과", "=" * 40, ""]
    lines += step_lines(session)
    lines += ["", "이 파일을 그대로 보내 주세요.", "", "[이 PC]"]
    lines += system_lines()
    mailbox = link.get("mailbox")
    lines += [
        "",
        "[연결]",
        f"창을 연 시각: {session.started}",
        f"지금 연결됨: {'예' if link.get('connected') else '아니오'}",
        f"연결 전 자동 확인 횟수: {session.auto_attempts}",
        f"우체통 폴더: {mailbox}",
    ]
    if isinstance(mailbox, str) and mailbox and not mailbox.isascii():
        lines.append("  (영문 경로가 아닙니다. 리졸브 안의 스크립트가 이 폴더를 못 열 수 있습니다)")
    if link.get("mailbox_long"):
        lines.append(f"우체통 폴더(긴 이름): {link['mailbox_long']}")
    if link.get("auto_note"):
        lines.append(f"자동 확인: {link['auto_note']}")
    lines += [
        f"답한 Fusion.prefs: {link.get('prefs_path') or '아직 답 없음'}",
        f"리졸브에서 도는 스크립트 판: {link.get('script_version') or '모름'} (설치한 판: {SCRIPT_VERSION})",
        f"스크립트 반복 표시(owner): {link.get('owner') or '모름'}",
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
        if d:
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

    if link.get("late_answers"):
        lines += ["", "[답을 기다리다 그만둔 뒤에 온 답]", _json(link["late_answers"])]

    lines += ["", "[한 일]"]
    lines += session.history or ["아직 아무 단계도 하지 않았습니다."]
    titles = dict(STEPS)
    counts: Dict[str, int] = {}
    for name, rec in session.records:
        counts[name] = counts.get(name, 0) + 1
        lines += ["", f"---- {titles.get(name, name)} {counts[name]}번째 ({rec.at}) 리졸브의 답 ----"]
        if rec.error:
            lines += ["오류:", _json(rec.error)]
        lines.append(_json(rec.data))
    if link.get("last_response") is not None:
        lines += ["", "---- 리졸브의 마지막 답 (원래 모양) ----", _json(link["last_response"])]
    return "\n".join(lines) + "\n"


def save_report(text: str, folder: Path) -> Path:
    """결과 파일을 쓴다. 메모장이 인코딩을 헷갈리지 않게 BOM을 붙인 UTF-8로 저장한다."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / REPORT_NAME
    path.write_text(text, encoding="utf-8-sig", newline="\r\n" if os.name == "nt" else "\n")
    return path
