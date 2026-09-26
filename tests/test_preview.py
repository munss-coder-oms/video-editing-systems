"""화면 미리보기 만들기 (tools/preview): 앞 몇 단계만 임시 폴더에 만들어 본다 (녹화 흉내가 필요 없는 단계라 빠르다).

- 단계마다 그림(PNG)이 생기고 비어 있지 않다.
- steps.json에 판, 앱 판, 스크립트 판, 만든 때, 단계마다 제목·할 일·보이는 것·안내 번호·그림이 있다.
- index.html은 쪽 조각이다 (<html>/<head>/<body> 없이 <title>로 시작), 밖의 주소는 구글 글꼴뿐이다.
- 단계 목록이 2.1 시험 안내의 1~23번을 모두 덮는다.
- 찍고 어느 단계에도 넣지 않은 그림이 있으면 만들기가 멈춘다 (올려도 쪽에서 보이지 않는 파일).
"""

from __future__ import annotations

import json
import os
import re

import pytest

pytest.importorskip("PySide6")

GENERATED_AT = "2026-09-26 12:00"
LIMIT = 4
FONT_HOSTS = {"fonts.googleapis.com", "fonts.gstatic.com"}


@pytest.fixture(scope="module")
def preview(tmp_path_factory):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from tools.preview import build

    out = tmp_path_factory.mktemp("preview")
    data = build.build(out, generated_at=GENERATED_AT, limit=LIMIT, log=lambda _line: None)
    return out, data


def test_images_exist(preview):
    out, data = preview
    from PySide6.QtGui import QImage

    srcs = [img["src"] for s in data["steps"] for img in s["images"]]
    assert srcs and len(srcs) == len(set(srcs))
    for src in srcs:
        path = out / src
        assert path.is_file(), src
        assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", src
        assert path.stat().st_size > 5000, src  # 빈 그림이 아니다
        image = QImage(str(path))
        assert not image.isNull() and image.width() >= 300, src
    assert sorted(p.name for p in (out / "img").glob("*.png")) == sorted(src.split("/")[-1] for src in srcs)


def test_unreferenced_images_stop_the_build(tmp_path):
    """앞 몇 단계만 만드는 위 시험으로는 뒤쪽 단계에서 찍고 넣지 않은 그림을 못 본다: 찾는 함수를 따로 본다."""
    from tools.preview import build

    for name in ("s1-window.png", "s42-menu.png", "s42-dialog.png"):
        (tmp_path / name).write_bytes(b"\x89PNG")
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    steps = [{"images": [{"src": "img/s1-window.png"}]}, {"images": [{"src": "img/s42-dialog.png"}]}]
    assert build.unreferenced_images(tmp_path, steps) == ["s42-menu.png"]
    steps[1]["images"].insert(0, {"src": "img/s42-menu.png"})
    assert build.unreferenced_images(tmp_path, steps) == []


def test_steps_json(preview):
    import engine
    from engine.resolve_link import SCRIPT_VERSION

    out, data = preview
    saved = json.loads((out / "steps.json").read_text(encoding="utf-8"))
    assert saved == data
    assert data["version"] == 1
    assert data["build"] == "2.1"
    assert data["title"] == "AI 도우미 2.1 미리보기"
    assert data["app_version"] == engine.__version__
    assert data["script_version"] == SCRIPT_VERSION
    assert data["generated_at"] == GENERATED_AT
    steps = data["steps"]
    assert [s["id"] for s in steps] == [f"s{n}" for n in range(1, LIMIT + 1)]
    assert [s["guide_step"] for s in steps] == [None, 1, 2, 2]
    for s in steps:
        for key in ("title", "do", "see", "section", "section_title", "key"):
            assert isinstance(s[key], str) and s[key].strip(), (s["id"], key)
        assert isinstance(s["note"], str) and isinstance(s["issues"], list)
        assert s["images"], s["id"]
        for img in s["images"]:
            assert img["src"].startswith("img/") and img["alt"]
            assert img["kind"] in {"window", "dialog", "menu", "card", "timeline"}
            assert img["w"] > 0 and img["h"] > 0
    assert {sec["id"] for sec in data["sections"]} == {s["section"] for s in steps}


def test_page_is_fragment(preview):
    out, data = preview
    page = (out / "index.html").read_text(encoding="utf-8")
    assert page.startswith("<title>AI 도우미 2.1 미리보기</title>\n<style>")
    for tag in ("!doctype", "html", "head", "body"):  # <header>는 괜찮다
        assert not re.search(rf"</?{tag}[\s>]", page, re.IGNORECASE), tag
    hosts = set(re.findall(r"https?://([^/\"'\s)]+)", page))
    assert hosts and hosts <= FONT_HOSTS, hosts
    for call in ("alert(", "confirm(", "prompt("):
        assert call not in page, call
    assert "설치 없이 보는 실제 도우미 창 화면이에요. 리졸브 안의 결과는 흉내 그림이에요." in page
    assert GENERATED_AT in page
    for s in data["steps"]:
        assert f'id="{s["id"]}"' in page
        for img in s["images"]:
            assert f'src="{img["src"]}"' in page
    assert not re.search("[\U0001F300-\U0001FAFF]", page)  # 그림 글자(이모지) 없음


def test_scenes_cover_guide():
    from tools.preview.scenes import SCENES, SECTIONS

    keys = [sc.key for sc in SCENES]
    assert len(keys) == len(set(keys))
    assert {sc.guide for sc in SCENES if sc.guide is not None} == set(range(1, 24))
    guides = [sc.guide for sc in SCENES if sc.guide is not None]
    assert guides == sorted(guides)  # 안내 순서대로
    sections = {k for k, _ in SECTIONS}
    assert {sc.section for sc in SCENES} == sections
    assert not any(sc.media for sc in SCENES[:LIMIT])  # 위의 짧은 미리보기는 녹화 흉내 없이 된다


def test_caption_helpers():
    from tools.preview.scenes import particle_slip, q

    assert q("가나") == "'가나'"
    assert q("'이름' 설정") == "\"'이름' 설정\""
    assert q("첫 줄\n둘째 줄") == "'첫 줄 / 둘째 줄'"
    assert particle_slip("소리 2을 트는 중", "소리 2")
    assert particle_slip("소리 3을 트는 중", "소리 3") is None
    assert particle_slip("'소리 고르게'을 되돌렸어요", "'소리 고르게'")
    assert particle_slip("'쉬는 곳 표시'를 되돌렸어요", "'쉬는 곳 표시'") is None
