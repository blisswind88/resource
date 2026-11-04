"""T19 규칙 목록(rules.yaml) → ISO Schematron(rules.sch) 생성.

사람은 rules.yaml만 고친다. .sch는 이 스크립트가 만든다 (v3 '파일 포맷 기준').

    python scripts/build_schematron.py            # rules/rules.yaml → rules/rules.sch
    python scripts/build_schematron.py --check    # .sch가 원본과 어긋나면 종료 코드 1

생성한 .sch는 lxml이 내장한 ISO Schematron RELAX NG 스키마로 바로 검사한다.
ID 규칙: 패턴 id는 'pat-<규칙 id>', 검사(assert·report) id는 rules.yaml의 checks[].id를 그대로 쓴다.
같은 문서 안에서 ID가 겹치면 스키마가 무효가 된다 (2026-11-04 실제로 겪음 — libxml2 오류 메시지는
겹친 ID가 아니라 엉뚱한 요소를 가리키므로, 여기서 먼저 중복을 잡는다).

의존: ruamel.yaml (YAML 1.2), lxml
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from lxml import etree, isoschematron
from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parent.parent
SCH_NS = "http://purl.oclc.org/dsdl/schematron"
S = f"{{{SCH_NS}}}"
SEVERITIES = ("error", "warning", "info")
PLACEHOLDER = re.compile(r"\{\{(.+?)\}\}")


def load_rules(path: Path) -> dict:
    data = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    problems: list[str] = []
    seen: set[str] = set()

    def claim(i: str) -> None:
        if i in seen:
            problems.append(f"ID 중복: {i}")
        seen.add(i)

    for r in data.get("rules", []):
        for key in ("id", "title", "context", "checks"):
            if key not in r:
                problems.append(f"{r.get('id', '?')}: '{key}' 없음")
        claim(f"pat-{r.get('id')}")
        for c in r.get("checks", []):
            claim(c.get("id"))
            for part in ("expected", "actual"):
                if c.get(part):
                    claim(diag_id(c["id"], part))
            if c.get("kind") not in ("assert", "report"):
                problems.append(f"{c.get('id')}: kind는 assert 또는 report")
            if c.get("severity") not in SEVERITIES:
                problems.append(f"{c.get('id')}: severity는 {SEVERITIES} 중 하나")
    if problems:
        raise ValueError("rules.yaml 오류:\n  " + "\n  ".join(problems))
    return data


def diag_id(check_id: str, part: str) -> str:
    return f"{check_id}.{part}"


def add_message(parent: etree._Element, text: str) -> None:
    """'{{XPath}}' 자리를 <sch:value-of select="XPath"/>로 바꾼다."""
    pos, last = 0, None
    for m in PLACEHOLDER.finditer(text):
        chunk = text[pos:m.start()]
        if last is None:
            parent.text = (parent.text or "") + chunk
        else:
            last.tail = (last.tail or "") + chunk
        last = etree.SubElement(parent, S + "value-of", select=m.group(1).strip())
        pos = m.end()
    rest = text[pos:]
    if last is None:
        parent.text = (parent.text or "") + rest
    else:
        last.tail = (last.tail or "") + rest


def build(data: dict, source_name: str) -> bytes:
    schema = etree.Element(S + "schema", nsmap={"sch": SCH_NS}, queryBinding=data.get("query_binding", "xslt"))
    schema.append(etree.Comment(f" 생성 파일 — {source_name}에서 build_schematron.py가 만들었습니다. 직접 고치지 마십시오. "))
    etree.SubElement(schema, S + "title").text = "T19 규칙 목록 (13번 DITA 규칙 검증)"
    diags: list[tuple[str, str]] = []
    for r in data["rules"]:
        pattern = etree.SubElement(schema, S + "pattern", id=f"pat-{r['id']}")
        etree.SubElement(pattern, S + "title").text = f"{r['id']} {r['title']}"
        rule = etree.SubElement(pattern, S + "rule", context=r["context"])
        for name, expr in (r.get("lets") or {}).items():
            etree.SubElement(rule, S + "let", name=name, value=expr)
        for c in r["checks"]:
            refs = []
            for part in ("expected", "actual"):
                if c.get(part):
                    refs.append(diag_id(c["id"], part))
                    diags.append((diag_id(c["id"], part), c[part]))
            attrs = {"test": c["test"], "id": c["id"], "role": c["severity"]}
            if refs:
                attrs["diagnostics"] = " ".join(refs)
            add_message(etree.SubElement(rule, S + c["kind"], **attrs), c["message"])
    if diags:
        dg = etree.SubElement(schema, S + "diagnostics")
        for did, expr in diags:
            etree.SubElement(etree.SubElement(dg, S + "diagnostic", id=did), S + "value-of", select=expr)
    return etree.tostring(schema, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="rules.yaml → rules.sch")
    ap.add_argument("--rules", type=Path, default=ROOT / "rules" / "rules.yaml")
    ap.add_argument("--out", type=Path, default=ROOT / "rules" / "rules.sch")
    ap.add_argument("--check", action="store_true", help="생성 결과가 지금 .sch와 같은지만 확인")
    args = ap.parse_args(argv)
    try:
        data = load_rules(args.rules)
        out = build(data, args.rules.name)
        isoschematron.Schematron(etree.fromstring(out))  # ISO Schematron 스키마 검사 + XSLT 컴파일
    except (ValueError, etree.SchematronParseError) as exc:
        print(f"[오류] {exc}", file=sys.stderr)
        return 2
    n = sum(len(r["checks"]) for r in data["rules"])
    if args.check:
        same = args.out.is_file() and args.out.read_bytes() == out
        print(f"[확인] {args.out.name} {'원본과 일치' if same else '원본과 다름 — 다시 생성하십시오'}")
        return 0 if same else 1
    args.out.write_bytes(out)
    print(f"[생성] {args.out.name} — 규칙 {len(data['rules'])}개, 검사 {n}개 (ISO Schematron 스키마 검사 통과)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
