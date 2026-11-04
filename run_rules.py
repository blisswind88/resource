"""13번 DITA 규칙 검증 — T19 rules.sch를 DITA map에 적용하고 T32 리포트를 쓴다.

    python scripts/run_rules.py --map out/step09/ev200-install-ko.ditamap --out out/step13 \
        --run-id <id> [--iteration 0]

- 규칙은 rules/rules.sch(생성본)를 쓰고, suggested_action은 rules/rules.yaml(원본)에서 읽는다.
- 맵과 토픽은 DTD를 불러오지 않고 읽는다 (네트워크 접근 없음). 유효성은 12번이 맡는다.
- lxml은 XSLT document()로 없는 파일을 읽으면 빈 결과 대신 예외를 내서 검사 전체가 멈춘다
  (2026-11-04 확인). 그래서 규칙을 적용하기 전에 토픽 파일이 있는지 먼저 보고, 없는 참조는
  메모리에 읽은 맵에서만 href를 떼어 비교 대상에서 뺀 뒤 R13-001.missing(info)으로 기록한다.
  맵 파일은 고치지 않는다. rules.sch의 같은 report는 빈 결과를 돌려주는 처리기(Oxygen 등)용이다.
- 이 단계는 LLM을 쓰지 않는다 (보안 설계: 13번 결과는 외부로 보내지 않는다).
- 종료 코드: 0 = pass, 1 = fail (허용되지 않은 error 1건 이상), 3 = 검사 자체 실패
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

from lxml import etree, isoschematron
from ruamel.yaml import YAML

sys.path.insert(0, str(Path(__file__).resolve().parent))
from report_lib import Report, new_run_id  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SVRL = "http://purl.oclc.org/dsdl/svrl"
NS = {"svrl": SVRL}
PARSER = etree.XMLParser(load_dtd=False, no_network=True, resolve_entities=False)


def topic_files(map_tree: etree._ElementTree, map_path: Path) -> list[Path]:
    """맵이 가리키는 .dita 중 실제로 있는 파일 (리포트 target.files와 base_revision용)."""
    out = []
    for el in map_tree.iter():
        href = el.get("href") if isinstance(el.tag, str) else None
        if not href or el.get("scope") == "external" or ".ditamap" in href:
            continue
        p = (map_path.parent / href.split("#", 1)[0]).resolve()
        if p.suffix == ".dita" and p.is_file() and p not in out:
            out.append(p)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="13번 DITA 규칙 검증 (T19 → T32)")
    ap.add_argument("--map", type=Path, required=True)
    ap.add_argument("--rules", type=Path, default=ROOT / "rules" / "rules.sch")
    ap.add_argument("--rules-source", type=Path, default=ROOT / "rules" / "rules.yaml")
    ap.add_argument("--out", type=Path, default=Path("out/step13"))
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--iteration", type=int, default=0)
    args = ap.parse_args(argv)

    sch_hash = hashlib.sha256(args.rules.read_bytes()).hexdigest()[:12] if args.rules.is_file() else "none"
    lxml_ver = ".".join(map(str, etree.LXML_VERSION[:3]))
    checker = {"kind": "script", "name": "run_rules.py (lxml isoschematron, T19 rules.sch)",
               "version": f"lxml {lxml_ver}; rules.sch {sch_hash}"}
    map_path = args.map.resolve()

    try:
        tree = etree.parse(str(map_path), PARSER)
    except (OSError, etree.XMLSyntaxError) as exc:
        rep = Report(13, "DITA 규칙 검증", args.run_id or new_run_id(), args.iteration,
                     checker=checker, target_kind="ditamap", files=[])
        rep.tool_error(f"맵을 읽을 수 없습니다: {exc}")
        print(f"[오류] {rep.save(args.out)}")
        return 3

    files = [map_path] + topic_files(tree, map_path)
    rep = Report(13, "DITA 규칙 검증", args.run_id or new_run_id(), args.iteration,
                 checker=checker, target_kind="ditamap", files=files)
    try:
        actions = {}
        if args.rules_source.is_file():
            src = YAML(typ="safe").load(args.rules_source.read_text(encoding="utf-8"))
            for r in src.get("rules", []):
                for c in r.get("checks", []):
                    if c.get("suggested_action"):
                        actions[c["id"]] = c["suggested_action"]
        missing_msg = "토픽 파일을 읽을 수 없어 비교하지 않았습니다 — 14번 링크 검사 대상 ({{@href}})"
        if args.rules_source.is_file():
            for r in src.get("rules", []):
                for c in r.get("checks", []):
                    if c.get("id") == "R13-001.missing":
                        missing_msg = c["message"]
        missing = []
        for el in tree.iter():
            href = el.get("href") if isinstance(el.tag, str) else None
            if (not href or el.get("scope") == "external" or el.get("format") not in (None, "dita")
                    or ".ditamap" in href):
                continue
            if not (map_path.parent / href.split("#", 1)[0]).is_file():
                missing.append((el, href))
                del el.attrib["href"]  # 메모리 사본에서만 비교 대상 제외
        sch = isoschematron.Schematron(etree.parse(str(args.rules)), store_report=True)
        sch.validate(tree)
        svrl = sch.validation_report
    except Exception as exc:  # 규칙 컴파일·적용 실패는 통과로 세지 않는다
        rep.tool_error(f"규칙을 적용할 수 없습니다: {type(exc).__name__}: {exc}")
        print(f"[오류] {rep.save(args.out)}")
        return 3

    rep.checked = int(svrl.xpath("count(//svrl:fired-rule)", namespaces=NS))
    for el, href in missing:
        rep.add("info", "R13-001.missing", "schematron",
                missing_msg.replace("{{@href}}", href),
                {"file": str(map_path).replace("\\", "/"), "line": el.sourceline, "element": el.tag,
                 "xpath": tree.getpath(el)})
    for node in svrl.xpath("//svrl:failed-assert | //svrl:successful-report", namespaces=NS):
        loc = node.get("location")
        target = tree.xpath(loc) if loc else []
        el = target[0] if target else None
        diags = {d.get("diagnostic", "").rsplit(".", 1)[-1]: " ".join("".join(d.itertext()).split())
                 for d in node.xpath("svrl:diagnostic-reference", namespaces=NS)}
        rid = node.get("id") or "R13-?"
        rep.add(
            node.get("role") or "error",
            rid,
            "schematron",
            " ".join("".join(node.xpath("svrl:text", namespaces=NS)[0].itertext()).split()),
            {"file": str(map_path).replace("\\", "/"), "line": el.sourceline if el is not None else None,
             "element": el.tag if el is not None else None, "xpath": loc},
            evidence={"expected": diags.get("expected"), "actual": diags.get("actual")} if diags else None,
            suggested_action=actions.get(rid),
        )
    path = rep.save(args.out)
    s = rep.summary
    print(f"[{rep.status}] 검사 {s['checked']}건 · error {s['error']} · warning {s['warning']} · info {s['info']} → {path}")
    return 0 if rep.status == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
