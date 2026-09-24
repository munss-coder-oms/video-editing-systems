# 영상 편집 자동화 시스템

영상을 넣으면 **음량 밸런스를 자동으로 정리**하고, **다빈치 리졸브 무료판**에서 바로 이어서 편집할 수 있는 타임라인 파일로 넘겨주는 윈도우 데스크톱 앱입니다.

현재 버전: **v0.1.0 (초기 버전)** · 기획 문서: [docs/PRD.md](docs/PRD.md) (PR #1)

## 지금 되는 것

| 기능 | 설명 |
| --- | --- |
| 영상 열기 | 끌어다 놓기 또는 [영상 열기] (mp4, mov, mkv 등). 원본은 절대 수정하지 않음 |
| 음량 분석 | 평균 음량(LUFS), 최대치(dBTP), 음량 범위, 튀는 구간·작은 구간 목록 |
| 튀는 소리 낮추기 | 말소리보다 갑자기 커지는 부분(웃음, 박수, 큰 소리)을 눌러줌 |
| 작은 목소리 올리기 | 작게 말한 구간을 끌어올림. 말이 없는 구간의 바닥 소음은 키우지 않음 |
| 유튜브 기준 맞추기 | 평균 -14 LUFS, 최대치 -1 dBTP 이하 |
| 리졸브로 넘기기 | 정리된 오디오(WAV 48kHz 24bit) + 타임라인 파일(FCPXML) + 불러오는 방법 안내 |

정리 강도는 **약하게 / 보통 / 강하게** 세 가지입니다. 목소리가 답답하게 들리면 약하게, 여전히 들쭉날쭉하면 강하게로 다시 돌려보세요.

아직 없는 것 (PRD 순서대로 다음 단계에서): 자막 자동 생성(SRT), 채팅으로 다듬기, 음량 그래프, 구간별 되돌리기, 설치 파일(.exe).

## 설치 (처음 한 번)

1. **Miniconda 설치**: https://www.anaconda.com/download/success 에서 Miniconda(Windows 64-bit)를 받아 기본 설정으로 설치합니다.
2. 이 저장소를 내려받습니다. (GitHub에서 **Code → Download ZIP** 후 압축 풀기, 또는 `git clone`)
   - 폴더 경로에 특수문자가 없는 곳을 권장합니다. 예: `C:\video-editing-systems`
3. 폴더 안의 **`setup_windows.bat`** 을 더블클릭합니다.
   - 파란 **"Windows의 PC 보호"** 창이 뜨면 **[추가 정보] → [실행]** 을 누르세요. 인터넷에서 받은 파일이라 한 번 뜨는 경고입니다. 설치가 끝나면 이 폴더의 파일에는 다시 뜨지 않습니다.
   - Python 3.11, FFmpeg, PySide6가 든 `video-editing` 환경을 만들고
   - 자동 테스트를 돌린 뒤
   - 바탕화면에 **Video Editing** 바로가기를 만들고, 앱을 한 번 띄웁니다.

### 새 버전으로 바꾸기

새 ZIP을 받아 압축을 풀고, 새 폴더의 `setup_windows.bat` 을 다시 실행하면 됩니다. 환경은 새 버전에 맞게 고쳐지고, 바탕화면 아이콘도 새 폴더를 가리키게 바뀝니다. 예전 폴더는 지워도 됩니다.

명령어로 직접 하고 싶다면 (Anaconda Prompt에서):

```bat
cd C:\video-editing-systems
conda env update -n video-editing -f environment.yml --prune
conda activate video-editing
python -m pytest -q
python -m app
```

## 사용법

1. 바탕화면의 **Video Editing** (또는 `run_app.bat`) 실행
2. 영상을 창에 끌어다 놓기
3. 정리 강도 고르고 **[③ 음량 정리하고 리졸브용으로 내보내기]**
4. 끝나면 처리 전/후 음량 비교가 나오고, 영상 옆에 `<영상이름>_resolve` 폴더가 생깁니다.

```
<영상이름>_resolve/
├─ <영상이름>_timeline.fcpxml   ← 리졸브에서 불러올 타임라인
├─ <영상이름>_balanced.wav      ← 음량을 정리한 오디오
├─ 음량_리포트.txt              ← 처리 전/후 비교, 튀던 구간 목록
├─ 리졸브_불러오기_방법.txt
├─ project.json                 ← 작업 기록 (나중에 이어서 편집할 때 사용)
└─ 작업로그.log                 ← 문제가 생기면 이 파일을 보내 주세요
```

### 다빈치 리졸브(무료판)에서 불러오기

1. 리졸브에서 프로젝트를 열고 **파일 → 가져오기 → 타임라인...** (File → Import → Timeline...)
2. `<영상이름>_timeline.fcpxml` 선택
3. "Automatically import source clips into media pool"이 체크된 상태로 OK
4. V1에 원본 영상, A1에 정리된 오디오가 올라온 타임라인이 생깁니다.

원본 오디오가 한 트랙 더 올라오면 그 트랙은 끄거나 지우세요. 타임라인 불러오기가 안 되면 원본 영상과 `_balanced.wav`를 직접 끌어다 0초 위치에 놓아도 결과는 같습니다 (처리해도 소리 위치는 그대로라 싱크가 맞습니다).

### 명령줄로 쓰기

화면 없이도 같은 엔진을 쓸 수 있습니다 (여러 영상을 한꺼번에 처리할 때 편리).

```bat
conda activate video-editing
python -m engine info  "D:\촬영\1화.mp4"
python -m engine balance "D:\촬영\1화.mp4" --strength medium --target -14
```

## 구조

```
engine/        편집 엔진 (화면 코드를 전혀 모름)
  ffmpeg.py      FFmpeg 호출을 한곳에 모음
  probe.py       영상 정보 읽기
  loudness.py    음량 분석 (EBU R128), 튀는/작은 구간 찾기
  commands.py    편집 명령 등록 (gain, tame_peaks, lift_quiet, normalize_loudness)
  balance.py     음량 처리 파이프라인
  fcpxml.py      리졸브용 타임라인 파일
  project.py     프로젝트 파일 (schema_version + 자동 변환)
  job.py         영상 하나를 처음부터 끝까지 처리
app/           PySide6 화면 (engine.job.process_video만 호출)
tests/         자동 테스트 (테스트 음원은 FFmpeg로 즉석에서 만듦)
```

PRD 7.4절 원칙을 따릅니다: 엔진과 화면 분리, 새 기능은 편집 명령 하나 추가(`@register`), 프로젝트 파일에 버전 번호, 음량 결과 자동 테스트.

## 문제 해결

| 증상 | 해결 |
| --- | --- |
| `Miniconda was not found` | Miniconda 설치 후 `setup_windows.bat` 다시 실행 |
| `ffmpeg을(를) 찾을 수 없습니다` | `setup_windows.bat` 다시 실행, 또는 `conda install -n video-editing -c conda-forge ffmpeg` |
| 앱이 안 켜짐 | 검은 창의 안내대로 폴더의 `check_setup.bat` 을 실행하고, 생기는 `setup_check_result.txt` 내용을 보내 주세요 (기록 위치: `%LOCALAPPDATA%\video-editing-systems\app.log`, `launch.log`) |
| "결과 파일을 저장할 수 없습니다" | 리졸브나 재생 프로그램이 이전 결과 WAV를 열고 있습니다. 그 프로그램을 닫거나 결과 폴더를 바꿔서 다시 실행 |
| 처리 중 오류 | 결과 폴더의 `작업로그.log` 확인 |
