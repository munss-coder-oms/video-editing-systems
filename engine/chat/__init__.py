"""대화 칸의 두뇌 (설계 B4, B5.1). Qt 없음.

- brain.py: 두뇌의 모양(ChatBrain)과 두뇌가 돌려주는 것 (Reply, Question, ProposalDraft, NotUnderstood).
- timeparse.py: 한국어 시간 말 ("3분 20초", "1:02:03", "여기", "5분~6분") → 타임라인 프레임.
- intents_ko.py: 한국어 낱말표 (색, 동작, 대상, 양)와 찾기 도구.
- rules.py: RuleBrain "기본 도우미 (AI 아님)". 정해진 말만 알아듣고, 못 알아들은 부분은 숨기지 않는다.
- context.py: 두뇌가 보는 것 (AssistContext).
- session.py: 대화 기록 (최근 몇 줄, 마지막 카드, chat.jsonl).

2.1에는 RuleBrain 하나뿐이다. AI 두뇌(2.2 이후)는 같은 ChatBrain 모양으로 붙는다.
"""
