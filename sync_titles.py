"""8번 후처리 — 토픽 <title>을 승인된 DITA map의 navtitle로 맞춘다 (@locktitle 정책 D2′).

    python scripts/sync_titles.py --map out/step08/ev200-install-ko.ditamap          # 고쳐 쓰기
    python scripts/sync_titles.py --map out/step08/ev200-install-ko.ditamap --check  # 보고만, 다르면 종료 1

규칙 (사용자 결정 2026-11-04)
- 제목의 출처는 7번에서 승인된 맵의 <navtitle>이다. 맵에 @locktitle을 쓰지 않는다.
- 맵 항목에 locktitle="yes"가 있으면 건드리지 않는다. 목차 라벨 예외는 현재 지원하지 않으므로(보류)
  정상 맵에는 나오지 않으며, 13번이 R13-001.locked(info)로 드러낸다.
- navtitle 안의 요소(예: <keyword keyref="product-name"/>)도 그대로 옮긴다.
- 바꿀 것이 없는 파일은 다시 쓰지 않는다 (바이트·수정 시각 유지). DOCTYPE과 title의 속성은 유지한다.
- 9번 이후 사람이 제목을 바꾸려면 16번 → 6번(수정 모드) → 7번 재승인 → 8번 이 스크립트만 → 11번 재검증
  경로를 탄다. 최대 2회 (사용자 결정 2026-11-04).
  13번 R13-001이 어긋남을 잡는다. 이 스크립트로 되돌리는 것은 의도하지 않은 변경일 때만이다.
"""
from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

from lxml import etree

PARSER = etree.XMLParser(load_dtd=False, no_network=True, resolve_entities=False, remove_blank_text=False)


def signature(el: etree._Element) -> tuple:
    """비교용: 공백을 정리한 글자 + 하위 요소의 태그·속성."""
    text = " ".join("".join(el.itertext()).split())
    kids = tuple((c.tag, tuple(sorted(c.attrib.items()))) for c in el.iter() if c is not el and isinstance(c.tag, str))
    return text, kids


def targets(map_tree: etree._ElementTree):
    for el in map_tree.iter():
        if not isinstance(el.tag, str):
            continue
        href = el.get("href")
        nav = el.find("topicmeta/navtitle")
        if (href and nav is not None and el.get("scope") != "external"
                and el.get("format") in (None, "dita") and ".ditamap" not in href):
            yield el, href.split("#", 1)[0], nav


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="토픽 title ← 맵 navtitle (8번 후처리)")
    ap.add_argument("--map", type=Path, required=True)
    ap.add_argument("--check", action="store_true", help="파일을 고치지 않고 다른 항목만 보고")
    args = ap.parse_args(argv)
    map_path = args.map.resolve()
    tree = etree.parse(str(map_path), PARSER)

    changed, same, locked, missing = [], 0, [], []
    for el, href, nav in targets(tree):
        path = map_path.parent / href
        if el.get("locktitle") == "yes":
            locked.append(href)
            continue
        if not path.is_file():
            missing.append(href)
            continue
        ttree = etree.parse(str(path), PARSER)
        title = ttree.getroot().find("title")
        if title is not None and signature(title) == signature(nav):
            same += 1
            continue
        changed.append(href)
        if args.check:
            continue
        if title is None:
            title = etree.Element("title")
            ttree.getroot().insert(0, title)
        tail = title.tail
        for c in list(title):
            title.remove(c)
        title.text = nav.text
        for c in nav:
            title.append(copy.deepcopy(c))
        title.tail = tail
        ttree.write(str(path), encoding="UTF-8", xml_declaration=True)

    verb = "다름" if args.check else "고침"
    print(f"[sync_titles] {verb} {len(changed)} · 같음 {same} · locktitle 예외 {len(locked)} · 파일 없음 {len(missing)}")
    for h in changed:
        print(f"  {verb}: {h}")
    for h in locked:
        print(f"  예외(locktitle): {h}")
    for h in missing:
        print(f"  파일 없음: {h}")
    return 1 if (args.check and changed) else 0


if __name__ == "__main__":
    sys.exit(main())
