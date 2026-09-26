"""다빈치 리졸브(무료판)와 바로 주고받는 연결 (리졸브 안에서 도는 Lua 스크립트와의 우체통).

무료판은 Workspace → Scripts 메뉴로 실행한 Lua 스크립트만 리졸브를 다룰 수 있다.
그래서 앱은 우체통 폴더에 request.lua(요청)를 쓰고, 리졸브 안의 AI_Helper_Connect.lua가
그것을 읽어 처리한 뒤 답을 Fusion.prefs에 적는다. 앱은 그 파일을 읽어 답을 받는다.

화면(Qt) 코드를 모르는 순수 모듈이다. 화면은 app/companion이 맡는다.
"""

# 설치하는 Lua 스크립트의 버전. 답(sv)에 실려 오므로 예전 스크립트가 도는지 알 수 있다.
SCRIPT_VERSION = "1.1.0"

# 리졸브 Scripts 메뉴에 보이는 이름 (파일 이름에서 .lua를 뺀 것)
SCRIPT_NAME = "AI_Helper_Connect"
SCRIPT_FILENAME = SCRIPT_NAME + ".lua"
