"""python -m tools.preview --out DIR [--build 2.1] [--generated-at "2026-09-26 14:00"] [--limit N]

설치 없이 보는 도우미 창 미리보기를 만든다: DIR/steps.json, DIR/img/*.png, DIR/index.html.
FFmpeg가 있어야 한다 (소리 계산). DIR/cache에는 녹화 흉내와 계산 결과를 두고 다시 쓴다 (올리지 않는다).
단계 하나라도 안 되면 어느 단계에서 무엇이 안 됐는지 적고 0이 아닌 값으로 끝난다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path


def _size(text: str):
    w, _, h = text.lower().partition("x")
    return int(w), int(h)


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        # 윈도우에서 출력을 파일로 받으면 cp949라 ⚙ 같은 글자에서 멈출 수 있다: 못 쓰는 글자는 ?로
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="python -m tools.preview", description="도우미 창 미리보기 만들기")
    ap.add_argument("--out", type=Path, help="만들 폴더")
    ap.add_argument("--build", default="2.1", help="빌드 이름 (제목에 씀)")
    ap.add_argument("--generated-at", help="만든 때 (없으면 지금 시각)")
    ap.add_argument("--limit", type=int, help="앞에서 몇 단계만")
    ap.add_argument("--scaled-shot", type=Path, help=argparse.SUPPRESS)  # 배율 150% 단계 (build.py가 부른다)
    ap.add_argument("--size", default="380x640", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    if args.scaled_shot is not None:
        # Qt 환경(배율)은 부른 쪽이 정해 두었다
        from .build import scaled_shot

        print(json.dumps(scaled_shot(args.scaled_shot, _size(args.size))))
        return 0
    if args.out is None:
        ap.error("--out이 필요해요")

    with tempfile.TemporaryDirectory(prefix="aih-preview-qt-") as qt_dir:
        from .build import PreviewError, build, qt_env
        from .director import SceneError

        os.environ.update(qt_env(Path(qt_dir)))  # QApplication을 만들기 전에
        generated = args.generated_at or time.strftime("%Y-%m-%d %H:%M")
        try:
            data = build(args.out, build_label=args.build, generated_at=generated, limit=args.limit)
        except SceneError as exc:
            print(f"미리보기를 만들지 못했어요. 단계 {exc.step}: {exc.what}", file=sys.stderr)
            return 1
        except PreviewError as exc:
            print(f"미리보기를 만들지 못했어요: {exc}", file=sys.stderr)
            return 2
    print(f"{len(data['steps'])}단계 → {Path(args.out).resolve() / 'index.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
