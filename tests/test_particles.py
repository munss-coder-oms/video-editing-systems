"""조사 고르기 (strings_ko.fill / josa): 자리표시 뒤의 을(를)·은(는)·이(가)·으로(로)·이에요(예요)·과(와).

- 앞 글자의 받침으로 고른다. 숫자는 읽는 소리로 (0 영, 1 일, 2 이, 3 삼 …), ㄹ 받침 뒤에는 "로".
- 닫는 따옴표·괄호는 건너뛰고, 괄호 풀이는 괄호 앞 낱말에 붙인다.
- strings_ko의 어느 틀도 자리표시 바로 뒤에 조사 하나만 적지 않는다 (값에 따라 틀리므로).
- 두 꼴을 적은 틀은 화면 코드에서 .format만으로 쓰지 않는다 (fill 또는 josa를 거친다).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from app.companion import strings_ko as S

ROOT = Path(__file__).resolve().parent.parent
COMPANION = ROOT / "app" / "companion"

# (값, 을/를, 으로/로, 이/가, 은/는, 이에요/예요, 과/와)
TABLE = [
    ("0", "을", "으로", "이", "은", "이에요", "과"),
    ("1", "을", "로", "이", "은", "이에요", "과"),
    ("2", "를", "로", "가", "는", "예요", "와"),
    ("3", "을", "으로", "이", "은", "이에요", "과"),
    ("4", "를", "로", "가", "는", "예요", "와"),
    ("5", "를", "로", "가", "는", "예요", "와"),
    ("6", "을", "으로", "이", "은", "이에요", "과"),
    ("7", "을", "로", "이", "은", "이에요", "과"),
    ("8", "을", "로", "이", "은", "이에요", "과"),
    ("9", "를", "로", "가", "는", "예요", "와"),
    ("10", "을", "으로", "이", "은", "이에요", "과"),
    ("소리 고르게", "를", "로", "가", "는", "예요", "와"),
    ("쉬는 곳", "을", "으로", "이", "은", "이에요", "과"),
    ("말", "을", "로", "이", "은", "이에요", "과"),
    ("Timeline 1", "을", "로", "이", "은", "이에요", "과"),
    ("3:20.0", "을", "으로", "이", "은", "이에요", "과"),
    ("2:12.8", "을", "로", "이", "은", "이에요", "과"),
    ("01:03:20:00", "을", "으로", "이", "은", "이에요", "과"),
    ("AI 도우미 점검용 101500", "을", "으로", "이", "은", "이에요", "과"),
    ("OBS", "를", "로", "가", "는", "예요", "와"),
    ("MP3", "을", "으로", "이", "은", "이에요", "과"),
    ("트랙(A2)", "을", "으로", "이", "은", "이에요", "과"),
    ("'쉬는 곳 표시'", "를", "로", "가", "는", "예요", "와"),
    ("자동화 3", "을", "으로", "이", "은", "이에요", "과"),
]


@pytest.mark.parametrize("row", TABLE, ids=[r[0] for r in TABLE])
def test_particle_table(row):
    value, eul, euro, i, eun, ieyo, gwa = row
    assert S.fill("{v}을(를)", v=value) == value + eul
    assert S.fill("{v}으로(로)", v=value) == value + euro
    assert S.fill("{v}이(가)", v=value) == value + i
    assert S.fill("{v}은(는)", v=value) == value + eun
    assert S.fill("{v}이에요(예요)", v=value) == value + ieyo
    assert S.fill("{v}과(와)", v=value) == value + gwa


def test_real_templates_read_naturally():
    assert S.fill(S.SWITCHED_BACK, name="Timeline 1") == "원래 타임라인 'Timeline 1'로 돌아왔어요"
    assert S.fill(S.TIP_SLOT_NOT_READY, name="소리 고르게").startswith("'소리 고르게'는 아직")
    assert S.fill(S.CHAT_JUMP_DONE, at="3:20.0", tc="01:03:20:00").startswith("재생 위치를 3:20.0으로 ")
    assert S.fill(S.CHAT_JUMP_DONE, at="2:12.8", tc="01:02:12:48").startswith("재생 위치를 2:12.8로 ")
    assert S.fill(S.LISTENING, n=2) == "소리 2를 트는 중 (3초)"
    assert S.fill(S.LISTENING, n=3) == "소리 3을 트는 중 (3초)"
    assert S.fill(S.VOICE_MIX, n=1).startswith("소리 1은 ")
    assert S.fill(S.VOICE_PICKED, n=2).startswith("목소리는 소리 2로 ")
    assert S.fill(S.SAVE_DONE, n=3) == "자동화 3을 바꿨어요"
    assert S.fill(S.SAVE_DONE, n=2) == "자동화 2를 바꿨어요"
    assert S.fill(S.CHIPS["example:jump_time"], time="3분 20초") == "3분 20초로 가줘"
    assert S.fill(S.CHIPS["example:jump_time"], time="1분") == "1분으로 가줘"
    assert S.fill(S.CHAT_JUMP_UNCONFIRMED, readback="3:20.0", tc="01:03:20:00").startswith(
        "옮기라고 했는데 다시 읽어 보니 재생 위치가 3:20.0이에요. 리졸브에서 01:03:20:00을 ")
    # 표시 없이 쓰는 두 꼴이 없는 글은 그대로
    assert S.josa("그대로예요") == "그대로예요"


def test_unknown_ending_keeps_both_forms():
    """받침을 알 수 없는 끝 (기호뿐): 흔히 쓰는 "을(를)" 두 꼴을 그대로 둔다 (틀린 하나를 고르지 않는다)."""
    assert S.fill("{v}을(를)", v="→") == "→을(를)"
    assert S.final_sound("") is False


ENDINGS = "을|를|은|는|이|가|으로|로|과|와|이에요|예요"
PARTICLE = re.compile(r"\{[a-z_]+\}['\"’”\]]?(" + ENDINGS + r")(?!\()(?=[\s.,!?:·)]|$)")
# 괄호 풀이 뒤의 조사는 괄호 앞 낱말에 붙는다 ("시간({tc})으로"): 그 낱말로 맞는지 본다
PAREN = re.compile(r"(\S+)\([^()]*\{[a-z_]+\}[^()]*\)(" + ENDINGS + r")(?!\()(?=[\s.,!?:·)]|$)")
MARKERS = tuple(m for m, *_ in S.PARTICLES)


def _constants(value, where):
    if isinstance(value, str):
        yield where, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _constants(v, f"{where}[{k!r}]")
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            yield from _constants(v, f"{where}[{i}]")


def test_no_template_puts_a_bare_particle_after_a_placeholder():
    bad = []
    for name in dir(S):
        if name.startswith("_") or name == "PARTICLES":
            continue
        for where, text in _constants(getattr(S, name), name):
            for m in PARTICLE.finditer(text):
                bad.append(f"{where}: ...{text[max(0, m.start() - 10):m.end() + 3]}")
    assert bad == []


def test_particles_after_a_parenthetical_fit_the_word_before_it():
    bad, seen = [], 0
    for name in dir(S):
        if name.startswith("_") or name == "PARTICLES":
            continue
        for where, text in _constants(getattr(S, name), name):
            for m in PAREN.finditer(text):
                seen += 1
                word, ending = m.group(1), m.group(2)
                marker = next(mk for mk, *forms in S.PARTICLES if ending in forms)
                if S.josa(word + marker) != word + ending:
                    bad.append(f"{where}: {m.group(0)}")
    assert seen >= 5 and bad == []


def _has_marker(value) -> bool:
    return any(any(mk in text for mk in MARKERS) for _, text in _constants(value, ""))


def _template_of(node):
    """S.NAME 또는 S.NAME[...]: 그 값."""
    while isinstance(node, ast.Subscript):
        node = node.value
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "S":
        return getattr(S, node.attr, None)
    return None


@pytest.mark.parametrize("path", sorted(COMPANION.glob("*.py")), ids=lambda p: p.name)
def test_marker_templates_are_never_formatted_bare(path):
    """두 꼴 틀을 .format만 하면 "3을(를)"이 그대로 보인다: fill()이나 josa()를 거쳐야 한다."""
    if path.name == "strings_ko.py":
        return
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    bad = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format"):
            continue
        value = _template_of(node.func.value)
        if value is None or not _has_marker(value):
            continue
        parent = parents.get(node)
        wrapped = (isinstance(parent, ast.Call) and isinstance(parent.func, ast.Attribute)
                   and parent.func.attr == "josa")
        if not wrapped:
            bad.append(f"{path.name}:{node.lineno}")
    assert bad == []
