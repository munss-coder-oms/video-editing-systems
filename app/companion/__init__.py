"""AI 편집 도우미 창 (새 도우미 창 1차: 자동화 버튼 + 대화 칸의 바탕).

리졸브에서 Workspace → Scripts → AI_Helper_Connect를 누르면, 이 창이 리졸브 안의 Lua 스크립트와
engine.resolve_link로 주고받으며 지금 열린 프로젝트·타임라인에 바로 작업한다.

    steps.py        ⋯ > 연결 점검의 시험 단계 (화면 코드 없음, 작업 스레드에서 돈다)
    report.py       결과 파일 (화면 코드 없음)
    connection.py   연결 상태와 언제 리졸브에 물을지 (한가할 때는 묻지 않음)
    tasks.py        작업 스레드 (리졸브 줄 하나, 짧은 일)
    window.py       창 (PySide6): header_view, automation_view, chat_view, undo_view, check_page를 붙인다
    strings_ko.py   화면에 보이는 한국어 전부, theme.py 색과 글꼴
"""
