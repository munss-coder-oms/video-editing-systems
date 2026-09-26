"""도우미 창 미리보기 (python -m tools.preview --out DIR): 설치 없이 보는 2.1 시험 순서의 실제 창 화면.

진짜 HelperWindow를 화면 없이(offscreen) 띄우고, 시험용 가짜 리졸브(tests/fakes.py)에 이어 사용자처럼
단추를 눌러 가며 찍는다. 리졸브 안의 결과는 가짜 리졸브의 표시 목록을 그린 흉내 그림이다.

- world.py: 가짜 리졸브 한 벌 (테스트 영상, 7분 타임라인, 직접 찍은 표시, 지난번 시험 흔적)
- media.py: OBS 녹화 흉내 (소리 4개, 쉬는 곳과 튀는 곳)
- director.py: 누르기, 기다리기, 찍기 (메뉴와 묻는 창 포함)
- scenes.py: 단계와 설명 (시험 안내 순서)
- timeline_view.py: 리졸브 타임라인 흉내 그림
- build.py, page.py: steps.json과 index.html
"""
