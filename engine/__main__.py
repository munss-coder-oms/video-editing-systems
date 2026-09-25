"""명령줄 실행.

    python -m engine info  영상.mp4
    python -m engine balance 영상.mp4 [--strength weak|medium|strong] [--target -14] [--out 폴더] [--tracks 1,2]
"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .commands import STRENGTHS
from .ffmpeg import FFmpegError
from .job import process_video
from .probe import probe


def _print_progress(stage: str, value: float) -> None:
    sys.stderr.write(f"\r[{value * 100:5.1f}%] {stage:<20}")
    sys.stderr.flush()


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(prog="python -m engine", description="영상 편집 자동화 엔진")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_info = sub.add_parser("info", help="영상 정보 보기")
    p_info.add_argument("video")
    p_info.add_argument("--json", action="store_true")

    p_bal = sub.add_parser("balance", help="음량 자동 정리 + 리졸브용 파일 만들기")
    p_bal.add_argument("video")
    p_bal.add_argument("--strength", choices=STRENGTHS, default="medium")
    p_bal.add_argument("--target", type=float, default=-14.0, help="목표 평균 음량 (LUFS)")
    p_bal.add_argument("--out", help="결과 폴더 (기본: 영상 옆 <이름>_resolve, 이미 결과가 있으면 _2, _3)")
    p_bal.add_argument("--tracks", help="쓸 오디오 트랙 번호 (1부터, 쉼표로 구분). 기본: 모든 트랙 섞기")

    args = parser.parse_args(argv)
    try:
        if args.cmd == "info":
            info = probe(args.video)
            print(json.dumps(info.to_dict(), ensure_ascii=False, indent=2) if args.json else info.summary())
            return 0
        if args.cmd == "balance":
            tracks = None
            if args.tracks:
                try:
                    tracks = [int(t) - 1 for t in args.tracks.split(",") if t.strip()]
                except ValueError:
                    raise ValueError("--tracks에는 1,2처럼 트랙 번호를 적어 주세요.") from None
            result = process_video(
                args.video,
                output_dir=args.out,
                strength=args.strength,
                target_lufs=args.target,
                audio_tracks=tracks,
                on_stage=_print_progress,
            )
            sys.stderr.write("\n")
            print(result.summary())
            print(f"\n결과 폴더: {result.output_dir}")
            return 0
    except (FFmpegError, FileNotFoundError, ValueError) as exc:
        sys.stderr.write(f"\n오류: {exc}\n")
        return 1
    except OSError as exc:
        sys.stderr.write(f"\n오류: 파일을 읽거나 쓸 수 없습니다. --out으로 다른 결과 폴더를 지정해 보세요.\n{exc}\n")
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
