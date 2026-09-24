"""영상 하나를 처음부터 끝까지 처리하는 작업.

영상 정보 읽기 → 음량 정리 → 리졸브용 타임라인(FCPXML) → 리포트·프로젝트 파일 저장.
명령줄과 데스크톱 앱이 모두 이 함수를 부른다. 원본 영상은 읽기만 한다 (G-06).
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from . import __version__
from .balance import OUTPUT_CHANNELS, OUTPUT_SAMPLE_RATE, BalanceResult, StageCallback, balance
from .commands import Command, default_balance_commands
from .ffmpeg import Cancelled
from .fcpxml import build_fcpxml, timeline_size, write_fcpxml
from .loudness import LoudnessReport
from .probe import MediaInfo, probe
from .project import PROJECT_FILENAME, Project

log = logging.getLogger("engine")

RESOLVE_GUIDE = """\
다빈치 리졸브(무료판)에서 불러오는 방법
========================================

1. 다빈치 리졸브를 열고 프로젝트를 새로 만듭니다. (기존 프로젝트도 되지만, 새 프로젝트가
   프레임 속도·해상도를 이 영상에 맞추기 쉽습니다.)
2. 위 메뉴에서 [파일] → [가져오기] → [타임라인...]을 누릅니다.
   (영문 메뉴: File → Import → Timeline...)
3. 이 폴더의 "{fcpxml}" 파일을 선택합니다.
4. 창이 뜨면 "Automatically import source clips into media pool"(소스 클립을 미디어 풀로
   자동 가져오기)이 체크된 상태로 [OK]를 누릅니다. "Automatically set project settings"
   (프로젝트 설정 자동 맞춤) 항목이 보이면 그것도 체크합니다.
5. 영상 트랙(V1)에 원본 영상, 오디오 트랙에 음량을 정리한 오디오("{wav}")가 올라온
   타임라인이 생깁니다.

확인할 것
- 오디오 트랙에는 "{wav_stem}" 클립 하나만 있어야 합니다. 원본 영상 이름의 오디오 클립이
  함께 있으면 소리가 겹쳐 커집니다. 그 트랙은 소리를 끄거나(M 버튼) 지우세요.
- 미디어가 오프라인(빨간 화면)으로 뜨면: 원본 영상이나 이 폴더를 옮기지 않았는지 확인하고,
  미디어 풀에서 클립을 오른쪽 클릭 → [Relink Selected Clips]로 다시 연결하세요.
- 타임라인 불러오기가 안 되면: 원본 영상과 "{wav}"를 미디어 풀에 직접 끌어다 놓고,
  영상은 V1, 정리된 오디오는 A1의 맨 앞(0초)에 놓으면 똑같습니다. 원본 영상의 오디오는 지우세요.
"""

AUDIO_ONLY_GUIDE = """\
오디오 파일을 처리했습니다
==========================

영상이 아니라서 타임라인 파일(.fcpxml)은 만들지 않았습니다.
다빈치 리졸브의 미디어 풀에 "{wav}"를 끌어다 놓고 타임라인에 올려 쓰세요.
"""

# 윈도우 경로 길이 제한(260자)에 덜 걸리도록 폴더·파일 이름에 쓰는 영상 이름 길이를 줄인다.
_NAME_LIMIT = 40


@dataclass
class JobResult:
    output_dir: str
    media: MediaInfo
    balance: BalanceResult
    fcpxml: Optional[str]
    report_txt: str
    project_file: str
    warnings: List[str] = field(default_factory=list)

    def summary(self) -> str:
        return format_report(self.media, self.balance, self.warnings)


def short_name(stem: str) -> str:
    name = stem[:_NAME_LIMIT].rstrip(" .")
    return name or "video"


def default_output_dir(video: str | Path, parent: str | Path | None = None) -> Path:
    """결과 폴더: 영상 옆(또는 고른 폴더 안)의 '<영상 이름>_resolve'."""
    video = Path(video)
    base = Path(parent) if parent else video.parent
    return base / f"{short_name(video.stem)}_resolve"


def free_output_dir(base: str | Path) -> Path:
    """이미 끝난 결과가 들어 있는 폴더는 건드리지 않고 '_2', '_3' 폴더를 새로 쓴다.

    리졸브에 이미 불러온 타임라인은 예전 WAV를 가리키므로, 덮어쓰면 그 타임라인의 소리가
    말없이 바뀐다. 카메라가 카드마다 C0001.MP4 같은 같은 이름을 다시 쓰는 경우도 막는다.
    """
    base = Path(base)
    candidate, n = base, 2
    while (candidate / PROJECT_FILENAME).exists():
        candidate = base.with_name(f"{base.name}_{n}")
        n += 1
    return candidate


def media_warnings(media: MediaInfo, tracks: Sequence[int]) -> List[str]:
    """리졸브로 넘길 때 사용자가 알아야 할 점."""
    notes: List[str] = []
    count = len(media.audio_tracks)
    if count > 1:
        if len(tracks) == count:
            notes.append(
                f"이 영상에는 오디오 트랙이 {count}개 있어 모두 섞어서 정리했습니다 "
                "(예: 게임 소리 + 마이크). 한 트랙만 쓰려면 앱의 '오디오 트랙'에서 고르세요."
            )
        else:
            names = ", ".join(f"{t + 1}번" for t in tracks)
            notes.append(f"오디오 트랙 {count}개 중 {names}만 썼습니다.")
    if media.has_video:
        native, timeline = media.native_fps, media.fps
        if native > 0 and abs(float(native) / float(timeline) - 1) > 0.001:
            notes.append(
                f"리졸브 타임라인은 {float(timeline):.3f}fps로 만들었습니다 (원본 {float(native):.3f}fps). "
                "리졸브 무료판이 쓸 수 있는 속도 중 가장 가까운 값입니다."
            )
        if media.variable_frame_rate:
            notes.append(
                "휴대폰 영상처럼 프레임 간격이 일정하지 않은 영상(가변 프레임)입니다. 리졸브에서 긴 영상은 "
                "뒤로 갈수록 소리와 입 모양이 조금씩 어긋날 수 있습니다. 어긋나면 알려 주세요."
            )
        size = timeline_size(media.width, media.height)
        if size != (media.width, media.height):
            notes.append(
                f"리졸브 무료판 한도(UHD)에 맞춰 타임라인 크기를 {size[0]}x{size[1]}로 만들었습니다 "
                f"(원본 {media.width}x{media.height}). 영상은 원본 화질 그대로 들어갑니다."
            )
    return notes


def _regions_text(title: str, regions, limit: int = 30) -> List[str]:
    lines = [f"{title} ({len(regions)}곳)"]
    for r in regions[:limit]:
        lines.append(f"  - {r.describe()}")
    if len(regions) > limit:
        lines.append(f"  ... 외 {len(regions) - limit}곳")
    return lines


def format_report(media: MediaInfo, result: BalanceResult, notes: Sequence[str] = ()) -> str:
    b: LoudnessReport = result.before
    a: LoudnessReport = result.after
    lines = [
        f"파일: {Path(media.path).name}",
        f"정보: {media.summary()}",
        "",
    ]
    alerts = [*notes, *result.warnings]
    if alerts:
        lines.append("알림")
        lines += [f"  - {n}" for n in alerts]
        lines.append("")
    lines += [
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
    audio_tracks: Optional[Sequence[int]] = None,
    on_stage: Optional[StageCallback] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> JobResult:
    """영상 하나를 처리한다.

    output_dir: 결과를 넣을 폴더 (기본: 영상 옆 '<이름>_resolve'). 이미 끝난 결과가 있으면
    '_2', '_3'처럼 새 폴더를 만든다. 실패하거나 취소하면 이번에 만든 결과 파일은 지운다.
    """
    # resolve() 대신 abspath: 네트워크 드라이브(Z: 등)가 \\서버\... 경로로 바뀌지 않게 한다.
    video = Path(os.path.abspath(video))
    base = Path(os.path.abspath(output_dir)) if output_dir else default_output_dir(video)
    out = free_output_dir(base)
    created = not out.exists()
    out.mkdir(parents=True, exist_ok=True)
    handler = _attach_log(out)
    written: List[Path] = []
    outcome = "failed"
    try:
        log.info("엔진 %s, 입력 %s", __version__, video)
        media = probe(video)
        log.info("영상 정보: %s", media.summary())

        cmds = commands if commands is not None else default_balance_commands(strength, target_lufs)
        name = short_name(video.stem)
        wav = out / f"{name}_balanced.wav"
        if os.path.normcase(str(wav)) == os.path.normcase(str(video)):
            raise ValueError("출력 파일이 원본과 같습니다.")

        written.append(wav)
        result = balance(
            media, cmds, wav, audio_tracks=audio_tracks, on_stage=on_stage, is_cancelled=is_cancelled
        )
        notes = media_warnings(media, result.audio_tracks)
        log.info("처리 전: %s", result.before.summary())
        log.info("처리 후: %s", result.after.summary())
        log.info("필터: %s", result.filter_chain)
        for note in [*notes, *result.warnings]:
            log.info("알림: %s", note)

        fcpxml_path: Optional[Path] = None
        guide = out / "리졸브_불러오기_방법.txt"
        written.append(guide)
        if media.has_video:
            fcpxml_path = out / f"{name}_timeline.fcpxml"
            written.append(fcpxml_path)
            write_fcpxml(
                fcpxml_path,
                build_fcpxml(
                    media,
                    wav,
                    audio_channels=OUTPUT_CHANNELS,
                    audio_rate=OUTPUT_SAMPLE_RATE,
                    wav_duration=probe(wav).duration,
                ),
            )
            guide.write_text(
                RESOLVE_GUIDE.format(fcpxml=fcpxml_path.name, wav=wav.name, wav_stem=wav.stem),
                encoding="utf-8",
            )
        else:
            guide.write_text(AUDIO_ONLY_GUIDE.format(wav=wav.name), encoding="utf-8")

        report_txt = out / "음량_리포트.txt"
        written.append(report_txt)
        report_txt.write_text(format_report(media, result, notes), encoding="utf-8")

        project = Project(
            source=str(video),
            commands=cmds,
            media=media.to_dict(),
            settings={"audio_tracks": result.audio_tracks},
            outputs={
                "audio_wav": wav.name,
                "fcpxml": fcpxml_path.name if fcpxml_path else None,
                "report": report_txt.name,
            },
            reports={
                "before": result.before.to_dict(),
                "after": result.after.to_dict(),
                "applied_gain_db": result.applied_gain_db,
                "warnings": [*notes, *result.warnings],
            },
        )
        # project.json은 마지막에 쓴다. 이 파일이 있으면 '끝난 결과'로 보고 다음 실행은 새 폴더를 쓴다.
        project_file = project.save(out)
        log.info("완료: %s", out)
        outcome = "done"
        return JobResult(
            output_dir=str(out),
            media=media,
            balance=result,
            fcpxml=str(fcpxml_path) if fcpxml_path else None,
            report_txt=str(report_txt),
            project_file=str(project_file),
            warnings=notes,
        )
    except Cancelled:
        log.info("취소됨")
        outcome = "cancelled"
        raise
    except Exception:
        log.exception("처리 실패")
        raise
    finally:
        log.removeHandler(handler)
        handler.close()
        if outcome != "done":
            _clean_up(out, written, remove_dir=created and outcome == "cancelled")


def _clean_up(out: Path, written: List[Path], *, remove_dir: bool) -> None:
    """실패·취소한 실행이 만든 결과 파일을 지워서, 반쯤 만든 결과가 남지 않게 한다.

    실패한 경우 작업로그.log는 남겨서 원인을 볼 수 있게 한다.
    """
    for path in written:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    if remove_dir:
        shutil.rmtree(out, ignore_errors=True)
