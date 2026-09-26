"""[▶ 3초 듣기] (설계 B2.3): 소리 스트림에서 말소리가 가장 큰 3초를 WAV로 꺼내 이 PC에서 틀어 준다.

리졸브는 건드리지 않는다. 꺼내기(FFmpeg)는 짧은 작업 스레드에서 하고, 트는 것은 기다리지 않는다
(윈도우: winsound SND_ASYNC). 윈도우가 아니면 Qt 소리(QSoundEffect)를 써 보고, 없으면 조용히 넘어간다.
시험에서는 PLAYER를 바꿔 끼운다.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Callable, List, Optional

from engine.resolve_link.paths import state_dir
from engine.timeline.audio_map import excerpt_wav

_effects: List[object] = []  # QSoundEffect가 소리를 다 내기 전에 사라지지 않게 붙들어 둔다


def _winsound_play(path: Path) -> bool:
    import winsound  # 윈도우에만 있다

    winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    return True


def _qt_play(path: Path) -> bool:
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtMultimedia import QSoundEffect
    except ImportError:
        return False
    effect = QSoundEffect()
    effect.setSource(QUrl.fromLocalFile(str(path)))
    effect.play()
    _effects[:] = _effects[-3:] + [effect]
    return True


def default_player(path: Path) -> bool:
    """틀기 시작했으면 True (끝날 때까지 기다리지 않는다)."""
    if sys.platform == "win32":
        return _winsound_play(path)
    return _qt_play(path)


PLAYER: Callable[[Path], bool] = default_player


def listen_dir() -> Path:
    return state_dir() / "cache" / "listen"


def make_excerpt(path: str, stream: int, start: float, folder: Optional[Path] = None, media=None) -> Path:
    """작업 스레드에서: 3초 WAV를 만든다 (같은 스트림은 같은 이름으로 덮어쓴다)."""
    folder = Path(folder) if folder is not None else listen_dir()
    # 틀고 있는 파일을 덮어쓰지 않게 매번 새 이름으로 (지난 것은 몇 개만 남기고 지운다)
    try:
        old = sorted(folder.glob("listen_*.wav"), key=lambda p: p.stat().st_mtime)[:-4]
    except OSError:
        old = []
    for f in old:
        try:
            f.unlink()
        except OSError:
            pass
    name = f"listen_{int(stream)}_{int(time.time() * 1000)}.wav"
    return excerpt_wav(path, stream, start, folder / name, media=media)


def play(path: Path) -> bool:
    try:
        return bool(PLAYER(Path(path)))
    except Exception:  # noqa: BLE001 - 소리를 못 틀어도 창은 그대로
        return False
