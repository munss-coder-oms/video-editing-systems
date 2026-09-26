"""새 판 받기 (⋯ > 새 판 받기, update_windows.bat) 시험.

- 검은 창을 띄우는 명령 줄: 경로에 빈칸·괄호·작은따옴표·한글이 있어도 따옴표가 맞는다.
- 설치 폴더의 update_windows.bat이 아니라 임시 폴더의 복사본을 띄운다 (돌면서 자기를 덮어쓰지 않게).
- 리졸브에 넣는 중에는 받지 않는다. 윈도우가 아니면 메뉴 항목은 보이되 꺼져 있다.
- 판 번호가 바뀐 뒤 처음 켤 때 한 번만 알린다.
- .bat 파일은 ASCII와 CRLF. update_windows.bat은 코드 폴더만 /MIR로 바꾸고 다른 것은 지우지 않는다
  (tools\ffmpeg에 직접 넣은 FFmpeg와 samples의 영상도 남는다). 넣는 일은 새 판의 update_windows.bat이 한다.
- 가지가 GitHub에 없을 때(404)만 main을 받고, 지금보다 예전 판은 넣지 않는다.
- pause 뒤에 할 일은 같은 줄의 exit뿐이다 (기다리는 동안 새 판이 그 .bat을 바꿔도 엉뚱한 줄을 돌지 않게).
실제로 받고 풀고 넣는 일은 윈도우 CI(.github/workflows/windows.yml)가 한다.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path, PureWindowsPath

import pytest

from app import update as updater
from app.companion import strings_ko as S
from engine import __version__ as APP_VERSION

# install_dir()처럼 resolve() 없이 (네트워크 드라이브 Z:에서도 같은 경로가 되게)
ROOT = Path(os.path.abspath(__file__)).parent.parent
BAT = ROOT / "update_windows.bat"
# .github도: 테스트가 그 안의 검사 설정을 읽으므로 예전 것이 남으면 다음 설치의 테스트가 헷갈린다
CODE_FOLDERS = {"app", "engine", "resolve_scripts", "tests", "docs", ".github"}
# tools는 사용자가 직접 넣은 FFmpeg(tools\ffmpeg, engine/ffmpeg.py가 찾는 곳)를 빼고 /MIR.
# samples는 사용자의 영상을 넣는 곳이라 새 판의 파일만 넣고 아무것도 지우지 않는다
CMD = r"C:\Windows\system32\cmd.exe"


def bat_lines(path: Path = BAT) -> list:
    """주석(rem)을 뺀 줄."""
    lines = path.read_bytes().decode("ascii").split("\r\n")
    return [ln for ln in lines if not re.match(r"\s*rem\b", ln, re.I)]


# ---------------------------------------------------------------------------
# 검은 창 띄우기 (화면 없이)
# ---------------------------------------------------------------------------

def test_install_dir_is_the_folder_with_run_app_bat(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # 지금 폴더가 아니라 패키지 위치에서 찾는다
    assert updater.install_dir() == ROOT
    assert (ROOT / "run_app.bat").is_file() and (ROOT / updater.UPDATE_BAT).is_file()


def test_install_dir_keeps_the_path_the_app_was_started_from(tmp_path):
    """네트워크 드라이브(Z:)에 풀어 둔 폴더처럼: 실제 위치로 바꾸지 않고 켠 경로 그대로 돌려준다.

    resolve()는 윈도우에서 Z:를 \\\\서버\\공유로 바꾸고, cmd는 그 폴더로 cd하지 못해 새 판 받기의 설치가 실패한다.
    여기서는 폴더 바로가기(symlink)로 같은 모양을 만든다 (만들 수 없는 PC에서는 건너뛴다).
    """
    link = tmp_path / "Z drive"
    try:
        link.symlink_to(ROOT, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("이 PC에서는 폴더 바로가기(symlink)를 만들 수 없음")
    code = "import app.update as u; print(u.install_dir())"
    env = dict(os.environ, PYTHONPATH=str(link), PYTHONIOENCODING="utf-8")  # 한글 사용자 이름 폴더도
    out = subprocess.run([sys.executable, "-c", code], cwd=str(tmp_path), env=env, capture_output=True,
                         encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == str(link)


def test_update_module_does_not_need_qt():
    """띄우는 일은 화면 없이 시험할 수 있게 Qt를 모르는 모듈에 둔다."""
    code = "import sys, app.update; sys.exit('PySide6' in sys.modules)"
    assert subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), timeout=60).returncode == 0


def test_only_windows_launches():
    assert updater.supported("win32")
    assert not updater.supported("linux") and not updater.supported("darwin")


def cmd_split(line: str) -> list:
    """cmd.exe /s /c가 하는 대로: /c 뒤의 맨 앞과 맨 끝 따옴표를 벗기고, 따옴표 단위로 나눈다."""
    rest = line.split(" /c ", 1)[1]
    assert rest.startswith('"') and rest.endswith('"')
    return [quoted or bare for quoted, bare in re.findall(r'"([^"]*)"|(\S+)', rest[1:-1])]


def test_command_line_keeps_awkward_paths_in_quotes():
    bat = PureWindowsPath(r"C:\Users\홍길동\AppData\Local\video-editing-systems\update\update_windows.bat")
    install = PureWindowsPath(r"D:\내 영상 (2)\it's mine\video-editing-systems-claude-x")
    line = updater.command_line(bat, 4321, install, CMD)
    assert line == rf'"{CMD}" /d /s /c ""{bat}" 4321 "{install}""'
    assert cmd_split(line) == [str(bat), "4321", str(install)]


def test_command_line_refuses_a_quote_in_a_path():
    with pytest.raises(updater.UpdateError) as info:
        updater.command_line(PureWindowsPath('C:\\a"b\\update_windows.bat'), 1, PureWindowsPath(r"C:\x"), CMD)
    assert info.value.reason == "launch"


def make_install(tmp_path: Path) -> Path:
    install = tmp_path / "설치 폴더 (1)"
    install.mkdir()
    (install / updater.UPDATE_BAT).write_bytes(BAT.read_bytes())
    return install


def test_prepare_copies_the_bat_out_of_the_install_folder(tmp_path):
    install = make_install(tmp_path)
    work = tmp_path / "state" / "update"
    work.mkdir(parents=True)
    (work / updater.UPDATE_BAT).write_bytes(b"@echo old copy\r\n")
    copy = updater.prepare(install, work)
    assert copy == work / updater.UPDATE_BAT
    assert copy.read_bytes() == BAT.read_bytes()


def test_prepare_without_the_bat_says_missing(tmp_path):
    with pytest.raises(updater.UpdateError) as info:
        updater.prepare(tmp_path, tmp_path / "update")
    assert info.value.reason == "missing"


def windows_environ(tmp_path: Path) -> dict:
    env = r"C:\Users\u\miniconda3\envs\video-editing"
    return {
        "ComSpec": CMD,
        "PATH": ";".join([env, env + r"\Library\bin", env + r"\Scripts", r"C:\Windows\system32",
                          r"C:\Users\u\miniconda3\condabin", r"C:\Users\u\miniconda3\envs\video-editing-old"]),
        "CONDA_PREFIX": env, "CONDA_DEFAULT_ENV": "video-editing", "CONDA_SHLVL": "1",
        "CONDA_PROMPT_MODIFIER": "(video-editing) ", "NOPAUSE": "1", "AIH_UPDATE": "1",
        "AIH_UPDATE_ZIP": str(tmp_path / "given.zip"), "LOCALAPPDATA": str(tmp_path / "Local"),
    }


def test_start_update_runs_the_temp_copy_in_a_new_console(tmp_path):
    install = make_install(tmp_path)
    work = tmp_path / "Local" / "video-editing-systems" / "update"
    calls = []
    copy = updater.start_update(pid=4321, install=install, work=work,
                                popen=lambda *a, **kw: calls.append((a, kw)), environ=windows_environ(tmp_path))
    assert copy == work / updater.UPDATE_BAT and copy.is_file()
    assert len(calls) == 1
    (line,), kw = calls[0]
    assert cmd_split(line) == [str(copy), "4321", str(install)]
    assert line.startswith(f'"{CMD}" /d /s /c ')
    assert kw["cwd"] == str(work)
    flags = kw["creationflags"]
    assert flags & updater.CREATE_NEW_CONSOLE and flags & updater.CREATE_NEW_PROCESS_GROUP
    assert flags & updater.CREATE_BREAKAWAY_FROM_JOB
    env = kw["env"]
    # 켠 conda 환경은 걷어 낸다 (바탕화면에서 두 번 누른 것처럼). 시험용 ZIP 이름은 넘긴다
    for name in ("CONDA_PREFIX", "CONDA_DEFAULT_ENV", "CONDA_SHLVL", "CONDA_PROMPT_MODIFIER", "NOPAUSE", "AIH_UPDATE"):
        assert name not in env
    assert env["PATH"].split(";") == [r"C:\Windows\system32", r"C:\Users\u\miniconda3\condabin",
                                      r"C:\Users\u\miniconda3\envs\video-editing-old"]
    assert env["AIH_UPDATE_ZIP"] == str(tmp_path / "given.zip")
    assert env["AIH_UPDATE_COPY"] == "1"  # 띄우는 것은 임시 폴더의 복사본 (배치 파일이 자기를 또 복사하지 않게)


def test_start_update_without_job_breakaway(tmp_path):
    """작업 묶음 밖으로 빼지 못하게 막힌 PC에서는 그냥 띄운다."""
    install = make_install(tmp_path)
    flags = []

    def popen(cmd, **kw):
        flags.append(kw["creationflags"])
        if kw["creationflags"] & updater.CREATE_BREAKAWAY_FROM_JOB:
            raise PermissionError(5, "Access is denied")

    updater.start_update(pid=1, install=install, work=tmp_path / "update", popen=popen, environ={})
    assert len(flags) == 2 and not flags[1] & updater.CREATE_BREAKAWAY_FROM_JOB
    assert flags[1] & updater.CREATE_NEW_CONSOLE


def test_start_update_that_cannot_launch(tmp_path):
    install = make_install(tmp_path)

    def popen(cmd, **kw):
        raise FileNotFoundError(2, "cmd.exe not found")

    with pytest.raises(updater.UpdateError) as info:
        updater.start_update(pid=1, install=install, work=tmp_path / "update", popen=popen, environ={})
    assert info.value.reason == "launch" and "FileNotFoundError" in info.value.detail


def test_remember_version(tmp_path):
    state = tmp_path / "state"
    assert updater.remember_version(state, "0.2.1") is None  # 처음 켬: 적기만
    assert (state / updater.LAST_VERSION_FILE).read_text(encoding="utf-8").strip() == "0.2.1"
    assert updater.remember_version(state, "0.2.1") is None
    assert updater.remember_version(state, "0.2.2") == "0.2.2"
    assert updater.remember_version(state, "0.2.2") is None  # 한 번만
    (state / updater.LAST_VERSION_FILE).write_bytes(b"\xff\xfe")  # 깨진 기록은 바뀐 것으로 보고 다시 적는다
    assert updater.remember_version(state, "0.2.2") == "0.2.2"
    blocked = tmp_path / "file-not-folder"
    blocked.write_text("x", encoding="utf-8")
    assert updater.remember_version(blocked, "0.2.2") is None  # 적을 수 없으면 알리지 않는다 (매번 뜨지 않게)


# ---------------------------------------------------------------------------
# 창 (⋯ > 새 판 받기, 판이 바뀐 알림)
# ---------------------------------------------------------------------------

@pytest.fixture
def qapp():
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def make_window(qapp, tmp_path, monkeypatch):
    from tests.fakes import FakeLuaBridge

    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "ProgramData"))
    made = []

    def make(windows: bool = True):
        from app.companion.window import HelperWindow

        monkeypatch.setattr(updater, "supported", lambda platform=None: windows)
        w = HelperWindow(bridge=FakeLuaBridge(tmp_path), interactive=False, report_dir=tmp_path / "desktop",
                         auto_ping_ms=50, state_root=tmp_path / "state", process_check=None)
        w.show()
        qapp.processEvents()
        made.append(w)
        return w

    yield make
    for w in made:
        w.close()
    qapp.processEvents()


def wait_until(qapp, cond, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if cond():
            return
        time.sleep(0.005)
    raise AssertionError("정해진 시간 안에 끝나지 않음")


def recorder(w, answer: bool = True):
    asked, launched = [], []
    w.confirm = lambda *a: asked.append(a) or answer
    w.start_update = lambda **kw: launched.append(kw)
    return asked, launched


def test_menu_entry_off_windows_is_shown_but_disabled(make_window):
    w = make_window(windows=False)
    asked, launched = recorder(w)
    assert S.MENU_UPDATE in w.menu_entries()
    assert not w.update_action.isEnabled()
    assert w.update_action.toolTip() == S.TIP_UPDATE_WINDOWS_ONLY
    w.on_update()
    assert asked == [] and launched == [] and not w.closing
    assert w.message.text() == S.TIP_UPDATE_WINDOWS_ONLY


def test_update_asks_then_starts_the_black_window_and_closes(make_window, tmp_path):
    w = make_window()
    assert w.update_action.isEnabled() and w.update_action.toolTip() == S.TIP_UPDATE
    asked, launched = recorder(w)
    w.update_action.trigger()
    assert asked == [(S.UPDATE_CONFIRM_TITLE, S.UPDATE_CONFIRM, S.BTN_UPDATE)]
    assert launched == [{"pid": os.getpid(), "install": ROOT, "work": tmp_path / "state" / "update"}]
    # 여느 때처럼 닫는다: 작업 스레드를 멈추고 창을 닫는다 (설정과 일지는 바뀔 때마다 저장돼 있다)
    assert w.closing and not w.isVisible() and w.thread.isFinished()


def test_confirm_text_says_what_happens():
    for words in ("지금 쓰는 폴더", "닫혀요", "검은 창", "다시 열려요", "리졸브는 켜 둔", "AI_Helper_Connect"):
        assert words in S.UPDATE_CONFIRM
    assert S.BTN_UPDATE == "받기" and S.BTN_CANCEL == "취소"


def test_cancel_changes_nothing(make_window):
    w = make_window()
    asked, launched = recorder(w, answer=False)
    w.on_update()
    assert len(asked) == 1 and launched == [] and not w.closing and w.isVisible()


def test_refused_while_a_job_writes_to_resolve(qapp, make_window):
    w = make_window()
    asked, launched = recorder(w)
    gate = threading.Event()
    assert w.runs.jobs.start("apply", lambda ctx: gate.wait(10), lambda out: None, lambda exc: None,
                             cancellable=False)
    try:
        w.on_update()
        assert asked == [] and launched == [] and not w.closing
        assert w.message.text() == S.UPDATE_BUSY
    finally:
        gate.set()
    wait_until(qapp, lambda: not w.runs.busy)
    w.action = "probe"  # 기능 점검(점검용 복사본을 만든다)도 리졸브를 바꾼다
    w.on_update()
    assert asked == [] and launched == []
    w.action = None
    w.on_update()
    assert len(asked) == 1 and len(launched) == 1


def test_a_calculation_does_not_block_the_update(qapp, make_window):
    """계산(쉬는 곳 찾기)은 리졸브를 바꾸지 않고 닫을 때 멈추므로 막지 않는다."""
    w = make_window()
    asked, launched = recorder(w, answer=False)
    gate = threading.Event()
    assert w.runs.jobs.start("plan", lambda ctx: gate.wait(10), lambda out: None, lambda exc: None)
    try:
        w.on_update()
        assert len(asked) == 1
    finally:
        gate.set()
    wait_until(qapp, lambda: not w.runs.busy)


def test_launch_failure_keeps_the_window_open(make_window):
    w = make_window()
    w.confirm = lambda *a: True

    def fail(**kw):
        raise updater.UpdateError("missing", "no update_windows.bat")

    w.start_update = fail
    w.on_update()
    assert not w.closing and w.isVisible()
    assert w.message.text() == S.UPDATE_FAILED.format(reason=S.UPDATE_REASONS["missing"])
    assert "no update_windows.bat" in w.log_view.toPlainText()


def test_new_version_is_told_once_in_the_chat(make_window, tmp_path):
    note = S.VERSION_CHANGED.format(version=APP_VERSION)
    state = tmp_path / "state"
    state.mkdir()
    (state / updater.LAST_VERSION_FILE).write_text("0.0.1\n", encoding="utf-8")
    first = make_window()
    assert first.chat.log.toPlainText().count(note) == 1
    first.close()
    again = make_window()
    assert note not in again.chat.log.toPlainText()


def test_first_start_says_nothing_about_versions(make_window, tmp_path):
    w = make_window()
    assert S.VERSION_CHANGED.format(version=APP_VERSION) not in w.chat.log.toPlainText()
    assert (tmp_path / "state" / updater.LAST_VERSION_FILE).read_text(encoding="utf-8").strip() == APP_VERSION


# ---------------------------------------------------------------------------
# .bat 파일, 판 번호, 윈도우 CI
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", sorted(ROOT.glob("*.bat")), ids=lambda p: p.name)
def test_bat_files_are_ascii_with_crlf(path):
    data = path.read_bytes()
    assert data.isascii(), f"{path.name}: .bat 파일에는 영어만 (한글은 깨진다)"
    assert data.endswith(b"\r\n")
    assert data.count(b"\n") == data.count(b"\r\n") == data.count(b"\r"), f"{path.name}: 줄 끝이 CRLF가 아님"


def test_update_bat_gets_the_zip_from_update_source_or_aih_update_zip():
    text = BAT.read_bytes().decode("ascii")
    assert "update_source.txt" in text and "AIH_UPDATE_ZIP" in text
    assert "https://github.com/munss-coder-oms/video-editing-systems/archive/refs/heads/" in text
    assert "Invoke-WebRequest -UseBasicParsing" in text and "Tls12" in text
    assert "call :fetch main" in text  # 가지가 GitHub에 없으면 main
    assert "Expand-Archive" in text
    for name in ("environment.yml", "setup_windows.bat", r"app\__main__.py", "update_windows.bat"):
        assert f'if not exist "%NEW%\\{name}" goto :bad_zip' in text
    source = (ROOT / updater.SOURCE_FILE).read_text(encoding="utf-8").splitlines()
    assert len(source) == 1 and re.fullmatch(r"[A-Za-z0-9._/-]+", source[0])


def test_update_bat_tries_main_only_when_the_branch_is_gone():
    """받다가 끊기거나(와이파이, GitHub 오류) 늦어서 실패하면 main을 받지 않는다 (main이 예전 판일 수 있다).

    가지가 GitHub에 없을 때(404, 합친 뒤 지운 가지)만 main을 받는다.
    """
    lines = bat_lines()
    i = lines.index('call :fetch "%BRANCH%"')
    j = lines.index("call :fetch main")
    assert lines[i + 1] == "if not errorlevel 1 goto :unpack"
    assert lines[i + 2] == "if not errorlevel 4 goto :no_zip"
    assert i < j and lines[j + 1] == "if errorlevel 1 goto :no_zip"
    fetch = lines[lines.index(":fetch"):lines.index(":cleanup")]
    ps = next(ln for ln in fetch if ln.startswith("powershell "))
    assert "try { Invoke-WebRequest" in ps
    assert "catch { $r = $_.Exception.Response; if ($r -and [int]$r.StatusCode -eq 404) { exit 4 }; throw }" in ps
    k = fetch.index(ps)
    assert fetch[k + 1:k + 5] == ["if errorlevel 5 exit /b 1", "if errorlevel 4 exit /b 4", "if errorlevel 1 exit /b 1",
                                  'if not exist "%UPD_ZIP%" exit /b 1']


def bat_powershell(marker: str) -> str:
    """update_windows.bat에서 marker가 든 PowerShell 줄의 -Command 글."""
    line = next(ln for ln in bat_lines() if ln.startswith("powershell ") and marker in ln)
    assert line.count('"') == 2 and line.endswith('"')  # 안쪽에 큰따옴표가 없어야 cmd가 따옴표를 헷갈리지 않는다
    return line.split(' -Command "', 1)[1][:-1]


def test_update_bat_refuses_an_older_version_before_copying():
    lines = bat_lines()
    check = next(ln for ln in lines if ln.startswith("powershell ") and "__version__" in ln)
    k = lines.index(check)
    assert lines[k + 1:k + 3] == ["if errorlevel 3 goto :older_zip", "if errorlevel 1 goto :bad_zip"]
    assert k < lines.index(r'call "%UPD_APPLY%" apply "%NEW%" "%INSTALL_DIR%"')
    assert "older than the one in this folder" in lines[lines.index(":older_zip") + 1]


def test_update_bat_version_check_in_powershell(tmp_path):
    """넣기 전 판 비교를 PowerShell로 실제로 돌려 본다 (윈도우, 또는 pwsh가 있는 곳).

    0 = 넣어도 됨(같거나 새 판, 지금 폴더의 판을 모름), 3 = 예전 판, 2 = 받은 것에 판 번호가 없음.
    """
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if exe is None:
        pytest.skip("PowerShell이 없음")
    code = bat_powershell("__version__")

    def folder(name: str, version) -> Path:
        d = tmp_path / name
        (d / "engine").mkdir(parents=True)
        if version is not None:
            (d / "engine" / "__init__.py").write_text(f'"""엔진."""\n\n__version__ = "{version}"\n', encoding="utf-8")
        return d

    def run(install: Path, new: Path) -> int:
        env = dict(os.environ, INSTALL_DIR=str(install), NEW=str(new))
        return subprocess.run([exe, "-NoProfile", "-Command", code], env=env, capture_output=True,
                              timeout=60).returncode

    assert run(ROOT, folder("same", APP_VERSION)) == 0  # 이 저장소의 engine/__init__.py 모양 그대로 읽는다
    assert run(ROOT, folder("main 0.1.0", "0.1.0")) == 3
    assert run(folder("installed", "0.2.1"), folder("newer", "0.10.0")) == 0  # 글자가 아니라 판 번호로 비교
    assert run(ROOT, folder("no version", None)) == 2


def test_update_bat_waits_for_the_app_with_ping_not_timeout():
    lines = bat_lines()
    assert any('(\'tasklist /fi "PID eq %UPD_PID%" /fo csv /nh 2^>nul\')' in ln for ln in lines)
    assert "ping -n 2 127.0.0.1 >nul" in lines
    assert not any(re.search(r"(^|[\s&(])timeout\b", ln, re.I) for ln in lines)


def test_update_bat_mirrors_only_the_code_folders():
    lines = bat_lines()
    loops = [ln for ln in lines if ln.endswith("do call :mirror %%F")]
    assert len(loops) == 1
    assert set(re.search(r"in \(([^)]*)\)", loops[0]).group(1).split()) == CODE_FOLDERS
    # tools는 ffmpeg 폴더를 빼고 (/XD로 뺀 폴더는 /MIR도 지우지 않는다), samples는 지우지 않고 넣기만
    assert 'call :mirror tools "/XD ffmpeg"' in lines
    assert "call :merge samples" in lines
    calls = [ln for ln in lines if re.match(r"(for .* do )?call :(mirror|merge) ", ln)]
    assert len(calls) == 3
    start = lines.index(":apply_mode")
    assert all(start < lines.index(ln) < lines.index(":mirror") for ln in calls)  # 넣기는 apply에서만
    robo = [ln for ln in lines if ln.lower().startswith("robocopy ")]
    mirror = [ln for ln in robo if "/MIR" in ln.upper()]
    assert mirror == [r'robocopy "%NEW%\%~1" "%INSTALL_DIR%\%~1" /MIR /IS /IT /R:2 /W:1 /NFL /NDL /NJH /NJS /NP %~2']
    rest = [ln for ln in robo if ln not in mirror]
    assert rest == [r'robocopy "%NEW%" "%INSTALL_DIR%" /IS /IT /R:2 /W:1 /NFL /NDL /NJH /NJS /NP',
                    r'robocopy "%NEW%\%~1" "%INSTALL_DIR%\%~1" /IS /IT /R:2 /W:1 /NFL /NDL /NJH /NJS /NP']
    for ln in robo:
        assert not re.search(r"\s/(PURGE|MOV|MOVE|S|E)\b", ln, re.I), ln  # 맨 위 폴더에서는 지우지 않는다
        assert '\\"' not in ln  # "C:\x\"는 robocopy에서 따옴표를 먹는다
    after = [lines[i + 1] for i, ln in enumerate(lines) if ln in robo]
    assert all(ln.startswith("if errorlevel 8 ") for ln in after)  # 8 이상이 실패
    # 지우는 것은 임시 ZIP과 풀어 둔 폴더, 넣기에 쓴 새 판의 복사본뿐
    for ln in lines:
        if re.search(r"\b(del|rmdir|rd|erase)\b", ln, re.I):
            assert re.search(r'"%UPD_(ZIP|EXTRACT|APPLY)%"', ln) and "INSTALL_DIR" not in ln, ln


def test_update_bat_lets_the_new_version_copy_itself_in():
    """넣는 일은 새 판의 update_windows.bat이 한다 (apply). 그래야 새 판에 새로 생긴 맨 위 폴더도 들어가고,
    새 판이 지키기로 한 폴더도 새 판의 규칙대로 남는다. 돌고 있는 이 파일도, 설치 폴더에서 바뀌는 파일도 아닌
    임시 폴더의 따로 된 파일(apply_update.bat)로 부른다.
    """
    lines = bat_lines()
    # apply로 불리면 다른 일 없이 바로 넣기로 간다 (예전 판의 창이 부르는 약속: apply 새판폴더 설치폴더)
    assert lines.index('if /i "%~1"=="apply" goto :apply_mode') < lines.index('set "UPD_PID=%~1"')
    assert r'set "UPD_APPLY=%WORK%\apply_update.bat"' in lines
    i = lines.index(r'copy /y "%NEW%\update_windows.bat" "%UPD_APPLY%" >nul')
    assert lines[i + 1:i + 4] == ["if errorlevel 1 goto :copy_failed",
                                  r'call "%UPD_APPLY%" apply "%NEW%" "%INSTALL_DIR%"',
                                  "if errorlevel 1 goto :copy_failed"]
    # 받은 새 판에 apply가 없으면 받은 파일이 이상한 것으로 보고 아무것도 바꾸지 않는다
    check = r'findstr /b /l /c:":apply_mode" "%NEW%\update_windows.bat" >nul 2>&1'
    assert lines.index(check) < i and lines[lines.index(check) + 1] == "if errorlevel 1 goto :bad_zip"
    body = [ln for ln in lines[lines.index(":apply_mode"):lines.index(":mirror")] if ln.strip()]
    assert body[1:3] == ['set "NEW=%~2"', 'set "INSTALL_DIR=%~3"']
    assert "call :tidy_install_dir" in body and body[-1] == "exit /b 0"
    assert not any(re.search(r"setup_windows|goto :fail|pause", ln) for ln in body)  # 넣기만 하고 끝


def test_update_bat_copies_every_folder_of_the_program():
    """저장소의 맨 위 폴더는 모두 넣는 목록에 있어야 한다 (새로 만든 폴더를 목록에 빠뜨리면 새 판 받기로 오지 않는다).

    ZIP으로 받은 폴더(git 저장소가 아님)에서는 사용자가 만든 폴더와 구별할 수 없어 건너뛴다.
    """
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=str(ROOT), capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git이 없음")
    files = [f for f in out.stdout.decode("utf-8", "replace").split("\0") if f]
    if out.returncode != 0 or not files:
        pytest.skip("git 저장소가 아님 (ZIP으로 받은 폴더)")
    folders = {f.split("/", 1)[0] for f in files if "/" in f}
    assert folders == CODE_FOLDERS | {"tools", "samples"}


PAUSE = re.compile(r"(^|[\s&(])pause\b", re.I)


@pytest.mark.parametrize("path", sorted(ROOT.glob("*.bat")), ids=lambda p: p.name)
def test_bat_pause_is_followed_only_by_exit_on_the_same_line(path):
    """cmd.exe는 pause에서 기다리던 .bat을 다시 읽을 때 전과 같은 바이트 위치부터 읽는다. 그동안 새 판 받기가 그
    파일을 바꾸면 엉뚱한 줄부터 돈다 (도우미 창이 하나 더 뜨는 등). 그래서 pause 뒤에 할 일은 같은 줄의 exit뿐.

    괄호 묶음 안은 cmd가 묶음을 통째로 미리 읽으므로 괜찮다. update_windows.bat의 :no_pid는 이어서 받아야 하는데,
    그동안 바뀔 수 있는 것은 임시 폴더의 복사본이고 앱은 그 자리에 설치 폴더의 같은 파일을 복사한다.
    """
    depth, label, pauses = 0, "", 0
    for ln in bat_lines(path):
        s = ln.strip()
        if s.startswith(":"):
            label = s.split()[0].lower()
        if s.startswith(")"):
            depth -= 1
        if depth == 0 and PAUSE.search(s) and not s.lower().startswith("echo"):
            pauses += 1
            if (path.name, label) != (updater.UPDATE_BAT, ":no_pid"):
                assert re.search(r"(^|&\s*)pause\s*&\s*exit /b \S+$", s, re.I), f"{path.name}: {s}"
        if s.endswith("("):
            depth += 1
    assert depth == 0
    assert pauses or path.name == "find_conda.bat"


def test_update_bat_never_runs_from_the_install_folder():
    lines = bat_lines()
    # 앱이 복사해 띄우는 곳(state_dir()/update)과 배치 파일의 WORK가 같아야 복사본이 자기를 알아본다
    from engine.resolve_link.paths import APP_DIR_NAME

    assert f'set "WORK=%LOCALAPPDATA%\\{APP_DIR_NAME}\\update"' in lines
    assert updater.update_dir(Path("state")) == Path("state") / "update"
    assert r'copy /y "%~f0" "%WORK%\update_windows.bat" >nul' in lines
    # 복사본을 부르고 같은 줄에서 끝낸다 (새 판이 이 파일을 덮어써도 더 읽지 않게)
    assert r'call "%WORK%\update_windows.bat" "%UPD_PID%" "%INSTALL_DIR%" & if errorlevel 1 (exit /b 1) else exit /b 0' in lines
    assert r'call "%INSTALL_DIR%\run_app.bat" & pause & exit /b 0' in lines
    assert lines.index(r'if /i "%~dp0"=="%WORK%\" goto :worker') < lines.index(":worker")
    setup = [ln for ln in lines if "setup_windows.bat" in ln and ln.startswith("call ")]
    assert setup == [r'call "%INSTALL_DIR%\setup_windows.bat"']
    i = lines.index(setup[0])
    assert r'set "AIH_UPDATE=1"' in lines[i - 4:i]


PATH_VARS = r"%(INSTALL_DIR|WORK|NEW|UPD_ZIP|UPD_EXTRACT|UPD_APPLY|AIH_UPDATE_ZIP)(:[^%]*)?%"


def test_update_bat_keeps_every_path_in_quotes():
    """빈칸·괄호·작은따옴표·한글이 든 폴더에서도 되게: 경로 변수는 늘 따옴표 안에서만 쓴다."""
    for ln in bat_lines():
        for m in re.finditer(PATH_VARS, ln):
            assert ln[:m.start()].count('"') % 2 == 1, ln


def test_setup_skips_the_tests_for_a_quick_update():
    lines = bat_lines(ROOT / "setup_windows.bat")
    i = lines.index("if defined AIH_UPDATE goto :skip_tests")
    assert lines[i + 1].startswith("echo [2/4] Running automatic tests")
    assert any("python -m pytest -q" in ln for ln in lines[i:i + 4])
    j = lines.index(":skip_tests")
    assert "skipped for a quick update" in lines[j + 1] and "check_setup.bat" in lines[j + 1]
    assert lines[j + 2] == ":tests_done"


def test_app_version_matches_pyproject():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^version = "([^"]+)"', text, re.M).group(1) == APP_VERSION


def test_windows_ci_checks_the_update():
    path = ROOT / ".github" / "workflows" / "windows.yml"
    if not path.is_file():
        pytest.skip("검사 설정 파일이 없는 폴더")
    text = path.read_text(encoding="utf-8")
    # 단계 이름은 따옴표 없는 YAML 값이라 ": "가 들어가면 파일 전체를 읽지 못해 검사가 시작되지 않는다
    for m in re.finditer(r"^\s*(?:- )?name: (.*)$", text, re.M):
        value = m.group(1).strip()
        if not value.startswith(("'", '"')):
            assert ": " not in value and not value.endswith(":"), value
    for words in ("git archive --format=zip --prefix=video-editing-systems-update-test/",
                  r"app\zz_stale_for_update_test.py", "my_notes_update_test.txt", "AIH_UPDATE_ZIP",
                  r"tools\ffmpeg\bin\keep_update_test.txt", r"samples\my_video_update_test.txt",
                  "call update_windows.bat", "Automatic tests skipped for a quick update",
                  # 앱과 같은 방법 (start_update, 닫히는 PID를 기다림), 새 판의 update_windows.bat이 넣은 새 폴더,
                  # 고쳐 둔 파일이 새 판의 것으로, 예전 판은 넣지 않음, 가지가 없을 때만 main
                  "updater.start_update(", "AIH_UPDATE_WAIT", "Waiting for the AI helper window to close",
                  r"zz_new_folder_update_test\new_file.txt", "Get-FileHash", "old-version.zip", "older than",
                  "no-such-branch-update-test", "no longer on GitHub"):
        assert words in text, words
    # pwsh 단계 끝에 러너가 'exit $LASTEXITCODE'를 붙인다. 일부러 실패하는 명령의 결과를 $rc로 받아 스스로 보는
    # 단계는 마지막 줄이 exit 0이어야 한다 (아니면 확인이 다 맞아도 그 명령의 1로 단계가 실패한다)
    steps = re.split(r"^\s*- (?:name|uses): ", text, flags=re.M)
    checked = 0
    for step in steps:
        if "shell: pwsh" in step and "$rc = $LASTEXITCODE" in step:
            body = [ln.strip() for ln in step.splitlines() if ln.strip()]
            assert body[-1] == "exit 0", body[0]
            checked += 1
    assert checked >= 3
