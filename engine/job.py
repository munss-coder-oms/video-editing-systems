"""영상 하나를 처음부터 끝까지 처리하는 작업.

영상 정보 읽기 → 음량 정리 → 리졸브용 타임라인(FCPXML) → 리포트·프로젝트 파일 저장.
명령줄과 데스크톱 앱이 모두 이 함수를 부른다. 원본 영상은 읽기만 한다 (G-06).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from . import __version__
from .balance import OUTPUT_SAMPLE_RATE, BalanceResult, StageCallback, balance
from .commands import Command, default_balance_commands
from .fcpxml import build_fcpxml, write_fcpxml
from .loudness import LoudnessReport
from .probe import MediaInfo, probe
from .project import Project

log = logging.getLogger("engine")

RESOLVE_GUIDE = """\
다빈치 리졸브(무료판)에서 불러오는 방법
========================================

1. 다빈치 리졸브를 열고 프로젝트를 새로 만들거나 기존 프로젝트를 엽니다.
2. 위 메뉴에서 [파일] → [가져오기] → [타임라인...]을 누릅니다.
   (영문 메뉴: File → Import → Timeline...)
3. 이 폴더의 "{fcpxml}" 파일을 선택합니다.
4. 창이 뜨면 "Automatically import source clips into media pool"(소스 클립을 미디어 풀로
   자동 가져오기)이 체크된 상태로 [OK]를 누릅니다.
5. V1에 원본 영상, A1에 음량을 정리한 오디오("{wav}")가 올라온 타임라인이 생깁니다.

문제가 생기면
- 원본 오디오가 따로 한 트랙 더 올라오면: 그 트랙은 소리를 끄거나(M 버튼) 지우세요.
  음량을 정리한 오디오는 "{wav}"입니다.
- 미디어가 오프라인(빨간 화면)으로 뜨면: 원본 영상이나 이 폴더를 옮기지 않았는지 확인하고,
  미디어 풀에서 클립을 오른쪽 클릭 → [Relink Selected Clips]로 다시 연결하세요.
- 타임라인 불러오기가 안 되면: 원본 영상과 "{wav}"를 미디어 풀에 직접 끌어다 놓고,
  영상은 V1, 정리된 오디오는 A1의 0초 위치에 놓으면 똑같습니다.
"""


@dataclass
class JobResult:
    output_dir: str
    media: MediaInfo
    balance: BalanceResult
    fcpxml: Optional[str]
    report_txt: str
    project_file: str

    def summary(self) -> str:
        return format_report(self.media, self.balance)


def default_output_dir(video: str | Path) -> Path:
    video = Path(video)
    return video.parent / f"{video.stem}_resolve"


def _regions_text(title: str, regions, limit: int = 30) -> List[str]:
    lines = [f"{title} ({len(regions)}곳)"]
    for r in regions[:limit]:
        lines.append(f"  - {r.describe()}")
    if len(regions) > limit:
        lines.append(f"  ... 외 {len(regions) - limit}곳")
    return lines


def format_report(media: MediaInfo, result: BalanceResult) -> str:
    b: LoudnessReport = result.before
    a: LoudnessReport = result.after
    lines = [
        f"파일: {Path(media.path).name}",
        f"정보: {media.summary()}",
        "",
        "음량 비교         처리 전      처리 후",
        f"평균 음량(LUFS)  {b.integrated:>8.1f}    {a.integrated:>8.1f}   (유튜브 기준 -14)",
        f"최대치(dBTP)     {b.true_peak:>8.1f}    {a.true_peak:>8.1f}   (-1 이하 권장)",
        f"음량 범위(LU)    {b.lra:>8.1f}    {a.lra:>8.1f}   (작을수록 고른 소리)",
        f"튀는 구간        {len(b.spikes):>8d}    {len(a.spikes):>8d}",
        f"작은 구간        {len(b.quiet):>8d}    {len(a.quiet):>8d}",
        "",
        f"마지막 음량 맞춤 증폭: {result.applied_gain_db:+.1f} dB",
        "",
    ]
    lines += _regions_text("처리 전 튀던 구간", b.spikes)
    lines.append("")
    lines += _regions_text("처리 전 작던 구간", b.quiet)
    lines.append("")
    lines += _regions_text("처리 후에도 튀는 구간 (채팅 기능이 생기면 여기서 더 다듬습니다)", a.spikes)
    return "\n".join(lines)


def _attach_log(output_dir: Path) -> logging.Handler:
    handler = logging.FileHandler(output_dir / "작업로그.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    return handler


def process_video(
    video: str | Path,
    *,
    output_dir: str | Path | None = None,
    strength: str = "medium",
    target_lufs: float = -14.0,
    commands: Optional[List[Command]] = None,
    on_stage: Optional[StageCallback] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> JobResult:
    video = Path(video).resolve()
    out = Path(output_dir).resolve() if output_dir else default_output_dir(video)
    out.mkdir(parents=True, exist_ok=True)
    handler = _attach_log(out)
    try:
        log.info("엔진 %s, 입력 %s", __version__, video)
        media = probe(video)
        log.info("영상 정보: %s", media.summary())

        cmds = commands if commands is not None else default_balance_commands(strength, target_lufs)
        wav = out / f"{video.stem}_balanced.wav"
        if wav.resolve() == video:
            raise ValueError("출력 파일이 원본과 같습니다.")

        result = balance(media, cmds, wav, on_stage=on_stage, is_cancelled=is_cancelled)
        log.info("처리 전: %s", result.before.summary())
        log.info("처리 후: %s", result.after.summary())
        log.info("필터: %s", result.filter_chain)

        fcpxml_path: Optional[Path] = None
        if media.has_video:
            fcpxml_path = out / f"{video.stem}_timeline.fcpxml"
            channels = min(media.audio_channels or 2, 8)
            write_fcpxml(
                fcpxml_path,
                build_fcpxml(
                    media,
                    wav,
                    audio_channels=channels,
                    audio_rate=OUTPUT_SAMPLE_RATE,
                    wav_duration=probe(wav).duration,
                ),
            )
            (out / "리졸브_불러오기_방법.txt").write_text(
                RESOLVE_GUIDE.format(fcpxml=fcpxml_path.name, wav=wav.name), encoding="utf-8"
            )

        report_txt = out / "음량_리포트.txt"
        report_txt.write_text(format_report(media, result), encoding="utf-8")

        project = Project(
            source=str(video),
            commands=cmds,
            media=media.to_dict(),
            outputs={
                "audio_wav": wav.name,
                "fcpxml": fcpxml_path.name if fcpxml_path else None,
                "report": report_txt.name,
            },
            reports={
                "before": result.before.to_dict(),
                "after": result.after.to_dict(),
                "applied_gain_db": result.applied_gain_db,
            },
        )
        project_file = project.save(out)
        log.info("완료: %s", out)
        return JobResult(
            output_dir=str(out),
            media=media,
            balance=result,
            fcpxml=str(fcpxml_path) if fcpxml_path else None,
            report_txt=str(report_txt),
            project_file=str(project_file),
        )
    except Exception:
        log.exception("처리 실패")
        raise
    finally:
        log.removeHandler(handler)
        handler.close()
