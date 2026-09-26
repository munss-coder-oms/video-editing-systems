"""새 판 받기 (⋯ > 새 판 받기, update_windows.bat) 시험.

- 검은 창을 띄우는 명령 줄: 경로에 빈칸·괄호·작은따옴표·한글이 있어도 따옴표가 맞는다.
- 설치 폴더의 update_windows.bat이 아니라 임시 폴더의 복사본을 띄운다 (돌면서 자기를 덮어쓰지 않게).
- 리졸브에 넣는 중에는 받지 않는다. 윈도우가 아니면 메뉴 항목은 보이되 꺼져 있다.
- 판 번호가 바뀐 뒤 처음 켤 때 한 번만 알린다.
- .bat 파일은 ASCII와 CRLF. update_windows.bat은 코드 폴더만 /MIR로 바꾸고 다른 것은 지우지 않는다.
실제로 받고 풀고 넣는 일은 윈도우 CI(.github/workflows/windows.yml)가 한다.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path, PureWindowsPath

import pytest

from app import update as updater
from app.companion import strings_ko as S
from engine import __version__ as APP_VERSION

ROOT = Path(__file__).resolve().parent.parent
BAT = ROOT / "update_windows.bat"
# .github도: 테스트가 그 안의 검사 설정을 읽으므로 예전 것이 남으면 다음 설치의 테스트가 헷갈린다
CODE_FOLDERS = {"app", "engine", "resolve_scripts", "tests", "tools", "docs", "samples", ".github"}
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
    assert "call :fetch main" in text  # 가지를 받지 못하면 main
    assert "Expand-Archive" in text
    for name in ("environment.yml", "setup_windows.bat", r"app\__main__.py"):
        assert f'if not exist "%NEW%\\{name}" goto :bad_zip' in text
    source = (ROOT / updater.SOURCE_FILE).read_text(encoding="utf-8").splitlines()
    assert len(source) == 1 and re.fullmatch(r"[A-Za-z0-9._/-]+", source[0])


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
    robo = [ln for ln in lines if ln.lower().startswith("robocopy ")]
    mirror = [ln for ln in robo if "/MIR" in ln.upper()]
    assert mirror == [r'robocopy "%NEW%\%~1" "%INSTALL_DIR%\%~1" /MIR /IS /IT /R:2 /W:1 /NFL /NDL /NJH /NJS /NP']
    rest = [ln for ln in robo if ln not in mirror]
    assert rest == [r'robocopy "%NEW%" "%INSTALL_DIR%" /IS /IT /R:2 /W:1 /NFL /NDL /NJH /NJS /NP']
    for ln in robo:
        assert not re.search(r"\s/(PURGE|MOV|MOVE|S|E)\b", ln, re.I), ln  # 맨 위 폴더에서는 지우지 않는다
        assert '\\"' not in ln  # "C:\x\"는 robocopy에서 따옴표를 먹는다
    after = [lines[i + 1] for i, ln in enumerate(lines) if ln in robo]
    assert all(ln.startswith("if errorlevel 8 ") for ln in after)  # 8 이상이 실패
    # 지우는 것은 임시 ZIP과 풀어 둔 폴더뿐
    for ln in lines:
        if re.search(r"\b(del|rmdir|rd|erase)\b", ln, re.I):
            assert re.search(r'"%UPD_(ZIP|EXTRACT)%"', ln) and "INSTALL_DIR" not in ln, ln


def test_update_bat_never_runs_from_the_install_folder():
    lines = bat_lines()
    # 앱이 복사해 띄우는 곳(state_dir()/update)과 배치 파일의 WORK가 같아야 복사본이 자기를 알아본다
    from engine.resolve_link.paths import APP_DIR_NAME

    assert f'set "WORK=%LOCALAPPDATA%\\{APP_DIR_NAME}\\update"' in lines
    assert updater.update_dir(Path("state")) == Path("state") / "update"
    assert r'copy /y "%~f0" "%WORK%\update_windows.bat" >nul' in lines
    # 복사본을 부르고 같은 줄에서 끝낸다 (새 판이 이 파일을 덮어써도 더 읽지 않게)
    assert r'call "%WORK%\update_windows.bat" "%UPD_PID%" "%INSTALL_DIR%" & exit /b' in lines
    assert r'call "%INSTALL_DIR%\run_app.bat" & pause & exit /b 0' in lines
    assert lines.index(r'if /i "%~dp0"=="%WORK%\" goto :worker') < lines.index(":worker")
    setup = [ln for ln in lines if "setup_windows.bat" in ln and ln.startswith("call ")]
    assert setup == [r'call "%INSTALL_DIR%\setup_windows.bat"']
    i = lines.index(setup[0])
    assert r'set "AIH_UPDATE=1"' in lines[i - 4:i]


PATH_VARS = r"%(INSTALL_DIR|WORK|NEW|UPD_ZIP|UPD_EXTRACT|AIH_UPDATE_ZIP)(:[^%]*)?%"


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
                  "call update_windows.bat", "Automatic tests skipped for a quick update"):
        assert words in text, words
