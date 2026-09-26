"""미리보기 만들기: 녹화 흉내 → 가짜 리졸브와 진짜 도우미 창 → 단계마다 누르고 찍기 → steps.json, index.html.

- 창은 이 PC의 설정·기록을 건드리지 않게 임시 폴더(설정, 기록, 결과 파일, APPDATA 등)에서 띄운다.
- 소리를 트는 일과 새 판 받기의 검은 창만 막는다 (listen.PLAYER, start_update). 나머지는 창이 하는 그대로
  (계산, 카드, 넣기, 되돌리기). ⋯ > 새 판 받기는 윈도우에서처럼 켜 둔다.
- 녹화 흉내와 소리 계산 결과는 출력 폴더의 cache에 두고 다시 쓴다 (올릴 것은 index.html, steps.json, img).
- 화면 배율 150% 단계는 배율이 다른 Qt가 필요해서 이 모듈을 따로 띄워(--scaled-shot) 찍는다.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from PySide6.QtWidgets import QApplication

import engine
from app import update as updater
from app.companion import listen
from app.companion import strings_ko as S
from app.companion.cards import ProposalCard
from app.companion.connection import AUTO_PING_MS
from app.companion.window import HelperWindow
from engine.analysis_cache import AnalysisCache
from engine.resolve_link import SCRIPT_VERSION

from . import media as media_mod
from . import page
from .director import Director, Image, SceneError
from .scenes import SECTIONS, SCENES, Flow, Scene
from .world import World

ROOT = Path(__file__).resolve().parents[2]
STEPS_VERSION = 1
WINDOW = (420, 900)  # 1920×1080 화면(100%)에서 창이 붙는 크기에 가깝게: 너비 420, 버튼이 줄로 보이는 높이
SCALED_SIZE = (380, 640)  # 150% 배율의 1920×1080 화면: 논리 1280×720에서 작업 표시줄과 제목 줄을 뺀 창
SCALED_DISPLAY = 1.5
HIDPI = 2  # 그림을 2배 픽셀로 찍는다 (쪽에서는 논리 크기로 놓아 선명하게)
ENV_KEYS = ("APPDATA", "PROGRAMDATA", "LOCALAPPDATA", "AIH_PREFS_FILE")


class PreviewError(RuntimeError):
    """미리보기를 만들 수 없음 (FFmpeg 없음 등)."""


def title_for(build_label: str) -> str:
    return f"AI 도우미 {build_label} 미리보기"


def qt_env(folder: Path, display_scale: float = 1.0) -> Dict[str, str]:
    """offscreen Qt 환경: 1920×1080 논리 화면(배율 display_scale), 그림은 HIDPI배 픽셀.

    윈도우 경로의 드라이브 글자(C:)는 Qt가 플랫폼 인자 구분(:)으로 읽어서, 상대 경로를 쓸 수 없으면
    화면 설정 파일 없이(기본 offscreen 화면, 1배 픽셀) 띄운다.
    """
    folder.mkdir(parents=True, exist_ok=True)
    cfg = folder / "offscreen.json"
    screen = {"name": "Preview", "x": 0, "y": 0, "width": 1920 * HIDPI, "height": 1080 * HIDPI,
              "logicalDpi": 96, "logicalBaseDpi": 96, "dpr": 1}
    cfg.write_text(json.dumps({"screens": [screen]}), encoding="utf-8")
    path: Optional[str] = str(cfg)
    if ":" in path:
        try:
            path = os.path.relpath(cfg)
        except ValueError:
            path = None
    if path is None or ":" in path:
        return {"QT_QPA_PLATFORM": "offscreen", "QT_SCALE_FACTOR": f"{display_scale:g}"}
    return {"QT_QPA_PLATFORM": f"offscreen:configfile={path}", "QT_SCALE_FACTOR": f"{display_scale * HIDPI:g}"}


@contextlib.contextmanager
def sandbox(played: Optional[List[Any]] = None) -> Iterator[Path]:
    """임시 폴더와 환경 변수 (창의 설정·기록·결과 파일이 이 PC에 남지 않게). 소리는 틀지 않는다.

    ⋯ > 새 판 받기는 윈도우에만 켜지므로, 다른 곳에서 만들어도 윈도우의 창처럼 켜 둔다 (누르면 묻는 창까지).
    """
    tmp = Path(tempfile.mkdtemp(prefix="aih-preview-"))
    saved = {k: os.environ.get(k) for k in ENV_KEYS}
    old_player = listen.PLAYER
    old_supported = updater.supported
    updater.supported = lambda platform=None: True
    os.environ["APPDATA"] = str(tmp / "Roaming")
    os.environ["PROGRAMDATA"] = str(tmp / "ProgramData")
    os.environ["LOCALAPPDATA"] = str(tmp / "Local")
    os.environ["AIH_PREFS_FILE"] = str(tmp / "Fusion.prefs")
    listen.PLAYER = lambda path: (played.append(path) if played is not None else None) or True
    try:
        yield tmp
    finally:
        listen.PLAYER = old_player
        updater.supported = old_supported
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp, ignore_errors=True)


def open_window(tmp: Path, world: World, cache: Optional[Path] = None) -> HelperWindow:
    """진짜 도우미 창 (리졸브 대신 가짜 브리지, 결과 파일은 임시 '바탕 화면'). 크기는 Director.resize로."""
    window = HelperWindow(bridge=world.fake, interactive=False, report_dir=tmp / "바탕 화면",
                          auto_ping_ms=AUTO_PING_MS, state_root=tmp / "state", process_check=world.is_running)
    if cache is not None:
        window.runs.cache = AnalysisCache(cache / "analysis")
    window.start_update = _no_update
    window.show()
    return window


def _no_update(**_kwargs: Any) -> None:
    """미리보기에서는 새 판 받기의 검은 창을 띄우지 않는다 (묻는 창에서 [취소]만 누른다)."""
    raise updater.UpdateError("launch", "미리보기에서는 띄우지 않음")


def close_window(app: QApplication, window: Optional[HelperWindow]) -> None:
    if window is None:
        return
    window.close()
    window.deleteLater()
    app.processEvents()


def scaled_image(d: Director, alt: str) -> Image:
    """배율 150% 창을 따로 띄워 찍는다 (이 모듈의 --scaled-shot)."""
    png = d.img_dir / f"{d.prefix}-window.png"
    with tempfile.TemporaryDirectory(prefix="aih-preview-qt-") as tmp:
        env = dict(os.environ)
        env.update(qt_env(Path(tmp), SCALED_DISPLAY))
        w, h = SCALED_SIZE
        cmd = [sys.executable, "-m", "tools.preview", "--scaled-shot", str(png), "--size", f"{w}x{h}"]
        try:
            proc = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=180,
                                  encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired as exc:
            raise d.fail("배율 150% 창이 3분 안에 끝나지 않음") from exc
    if proc.returncode != 0 or not png.is_file():
        tail = "\n".join((proc.stderr or proc.stdout or "").strip().splitlines()[-8:])
        raise d.fail(f"배율 150% 창을 찍지 못함:\n{tail}")
    info = json.loads(proc.stdout.strip().splitlines()[-1])
    if info.get("layout") != "tight":
        raise d.fail(f"배율 150% 창이 좁은 모양이 아님 ({info.get('layout')})")
    # 쪽에서는 100% 화면의 그림과 같은 잣대로: 150% 화면의 실제 픽셀 크기
    return Image(f"img/{png.name}", "window", int(round(w * SCALED_DISPLAY)), int(round(h * SCALED_DISPLAY)), alt)


def scaled_shot(png: Path, size: Tuple[int, int]) -> Dict[str, Any]:
    """(따로 띄운 쪽) 지금 Qt 배율에서 창을 size로 띄워 연결하고 "여기 표시해줘" 카드까지 찍는다."""
    app = QApplication.instance() or QApplication([])
    window = None
    with sandbox() as tmp:
        try:
            world = World(tmp / "resolve")
            world.fake.online = True
            window = open_window(tmp, world)
            d = Director(app, window, world, tmp / "out")
            d.begin(0, "화면 배율")
            d.settle(0.3)
            d.resize(*size)
            d.wait(lambda: window.connected and "Timeline 1" in window.header.summary.text(), "연결됨", 20)
            d.idle()
            flow = Flow(d)
            card = flow.chat("여기 표시해줘", ProposalCard, "표시 카드", title=S.CARD_TITLE_PROPOSE)
            d.show_in_chat(card)
            d.settle(0.2)
            pixmap = window.grab()
            png.parent.mkdir(parents=True, exist_ok=True)
            if not pixmap.save(str(png), "PNG"):
                raise d.fail(f"그림을 저장하지 못함: {png}")
            screen = window.screen()
            return {"w": window.width(), "h": window.height(), "dpr": pixmap.devicePixelRatio(),
                    "layout": window.layout_name,
                    "screen": [screen.geometry().width(), screen.geometry().height()] if screen else None}
        finally:
            close_window(app, window)


def _clear_images(img_dir: Path) -> None:
    if img_dir.is_dir():
        for p in img_dir.glob("*.png"):
            p.unlink()


def unreferenced_images(img_dir: Path, steps: List[Dict[str, Any]]) -> List[str]:
    """img에 있지만 어느 단계의 그림에도 없는 PNG (찍고 넣지 않은 것: 올려도 쪽에서 보이지 않는다)."""
    used = {img["src"].split("/")[-1] for s in steps for img in s["images"]}
    return sorted(p.name for p in Path(img_dir).glob("*.png") if p.name not in used)


def build(out_dir: Path, *, build_label: str = "2.1", generated_at: str, limit: Optional[int] = None,
          log: Callable[[str], None] = print) -> Dict[str, Any]:
    """미리보기를 out_dir에 만든다. limit: 앞에서 몇 단계만 (시험용). 돌려주는 값은 steps.json 내용."""
    out_dir = Path(out_dir)
    scenes: List[Scene] = list(SCENES[:limit] if limit else SCENES)
    cache = out_dir / "cache"
    media = tone = None
    if any(s.media for s in scenes):
        if not media_mod.have_ffmpeg():
            raise PreviewError("FFmpeg를 찾지 못해 미리보기 녹화를 만들 수 없어요 (setup_windows.bat 또는 tools/ffmpeg)")
        log("미리보기 녹화 준비 (처음 한 번은 15초쯤)…")
        media = media_mod.make_obs_recording(cache)
        tone = media_mod.make_test_tone(cache)
    _clear_images(out_dir / "img")
    app = QApplication.instance() or QApplication([])
    section_titles = dict(SECTIONS)
    steps: List[Dict[str, Any]] = []
    window = None
    played: List[Any] = []
    with sandbox(played) as tmp:
        try:
            world = World(tmp / "resolve", media=media, tone=tone)
            window = open_window(tmp, world, cache if media is not None else None)
            d = Director(app, window, world, out_dir)
            d.settle(0.3)
            d.resize(*WINDOW)
            flow = Flow(d)
            flow.played = played
            flow.scaled = lambda alt: scaled_image(d, alt)
            for n, sc in enumerate(scenes, start=1):
                d.begin(n, sc.title)
                log(f"{n:2d}. {sc.title}")
                step = sc.run(flow)
                if d.unexpected:
                    raise d.fail("기다리지 않은 묻는 창: " + ", ".join(d.unexpected))
                if not step.images:
                    raise d.fail("그림이 없음")
                steps.append({
                    "id": f"s{n}", "n": n, "key": sc.key, "section": sc.section,
                    "section_title": section_titles[sc.section], "guide_step": sc.guide, "title": sc.title,
                    "do": step.do, "see": step.see, "note": " ".join(t for t in [step.note, *d.notes] if t),
                    "issues": [*step.issues, *d.issues],
                    "images": [img.to_dict() for img in step.images],
                })
        finally:
            close_window(app, window)
    unused = unreferenced_images(out_dir / "img", steps)
    if unused:
        raise PreviewError("찍었지만 어느 단계에도 넣지 않은 그림: " + ", ".join(unused))
    data = {
        "version": STEPS_VERSION,
        "build": build_label,
        "title": title_for(build_label),
        "app_version": engine.__version__,
        "script_version": SCRIPT_VERSION,
        "generated_at": generated_at,
        "window": {"w": WINDOW[0], "h": WINDOW[1]},
        "sections": [{"id": k, "title": t} for k, t in SECTIONS if any(s["section"] == k for s in steps)],
        "steps": steps,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "steps.json").write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    page.write(out_dir, data)
    return data


__all__ = ["PreviewError", "SceneError", "build", "qt_env", "scaled_shot", "title_for", "unreferenced_images"]
