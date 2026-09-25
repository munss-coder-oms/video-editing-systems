"""AI 도우미 창 (리졸브 연결 시험판).

리졸브에서 Workspace → Scripts → AI_Helper_Connect를 누르면, 이 창이 리졸브 안의 Lua 스크립트와
engine.resolve_link로 주고받으며 지금 열린 프로젝트·타임라인에 바로 작업한다.

    steps.py   시험 단계 (화면 코드 없음, 작업 스레드에서 돈다)
    report.py  결과 파일 (화면 코드 없음)
    window.py  창 (PySide6)
"""
