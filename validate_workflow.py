#!/usr/bin/env python3
"""4번 단계: 워크플로우 검증 (스크립트).

입력
  approved-workflow.yaml   확정 워크플로우 (T28, 3번 산출물)
  dita-workflow.ttl        워크플로우 온톨로지 (T03)
  dita-workflow-shapes.ttl 검증 규칙 (T29, SHACL)

출력
  project-workflow.ttl     프로젝트 워크플로우 정의 (T31) — 6번 이후 단계가 읽는다
  report-04-i<n>.json      워크플로우 검증 리포트 (T30, 형식은 T32 공통 스키마)

검사 두 층
  정의 층 (SHACL, R1~R8)  — 워크플로우 판 자체가 성립하는지. 규칙은 T29에 있다.
  프로젝트 층 (이 스크립트, P1~P5) — 이 프로젝트의 단계 선택이 성립하는지.
    P1 제외한 단계의 산출물을 포함된 단계가 입력으로 쓴다 (연결 끊김)
    P2 제외 사유가 필요한 단계를 사유 없이 제외했다
    P3 T28이 참조하는 온톨로지 해시가 지금 온톨로지와 다르다
    P4 T28의 단계 집합이 온톨로지의 단계 집합과 다르다
    P5 보안 등급이 사양서 전송을 허용하지 않아 막히는 전송이 있다 (정보)
    P6 3번 승인 기록이 없거나, 승인 당시 온톨로지 해시가 지금 T28의 해시와 다르다
       (--merge가 정의 변경 뒤 옛 승인을 옮겨 오는 빈틈을 막는다. 사용자 결정 2026-11-04)

SHACL 실행 설정 (중요)
  inference="none"으로 실행한다. SHACL은 targetClass를 rdfs:subClassOf*까지 넓혀 맞추므로
  wf:ValidationStep 등 하위 분류 인스턴스도 wf:Step 대상 규칙에 걸린다. 반면 inference="rdfs"로
  실행하면 wf:stepNumber의 rdfs:domain(wf:Step) 때문에 원안 기록(wf:orig01~16)까지 단계로
  추론되어 R1 위반 48건이 거짓으로 보고된다. 2026-10-30 확인.

종료 코드: 0 = 통과(위반 0건), 1 = 위반 있음, 2 = 리포트 스키마 검증 실패, 3 = 입력 오류
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from rdflib import BNode, Graph, Literal, Namespace, RDF, RDFS, URIRef, XSD
from rdflib.namespace import SH
from pyshacl import validate as shacl_validate
from ruamel.yaml import YAML

sys.path.insert(0, str(Path(__file__).resolve().parent))
from report_lib import Report, new_run_id, revision_of, sha256_of  # noqa: E402

WF = Namespace("https://example.org/dita-wf#")
PW = Namespace("https://example.org/dita-wf/project#")
PPLAN = Namespace("http://purl.org/net/p-plan#")

STEP_LABEL = "워크플로우 검증"
SEVERITY = {
    str(SH.Violation): "error",
    str(SH.Warning): "warning",
    str(SH.Info): "info",
}
SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"

yaml = YAML()
yaml.width = 4096
yaml.indent(mapping=2, sequence=4, offset=2)


def local(node: Any) -> str:
    return str(node).split("#")[-1]


def label_of(g: Graph, node: Any) -> str:
    for o in g.objects(node, RDFS.label):
        return str(o)
    return local(node)


# --------------------------------------------------------------- T28 스키마 검증

def validate_t28(doc: dict[str, Any]) -> list[str]:
    import jsonschema

    schema = json.loads((SCHEMA_DIR / "approved-workflow.schema.json").read_text(encoding="utf-8"))
    cls = jsonschema.validators.validator_for(schema)
    v = cls(schema, format_checker=cls.FORMAT_CHECKER)
    return [f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}" for e in v.iter_errors(doc)]


# ------------------------------------------------------------- T31 프로젝트 그래프

def build_project_graph(doc: dict[str, Any], ontology: Path) -> Graph:
    """T31 프로젝트 워크플로우 정의를 만든다.

    온톨로지(T03)는 '워크플로우 판'을, 이 그래프는 '이 프로젝트가 그중 무엇을 실행하는지'를
    담는다. 단계 속성은 복사하지 않고 온톨로지 노드를 가리킨다 (사실을 한곳에만 둔다).
    """
    g = Graph()
    g.bind("wf", WF)
    g.bind("pw", PW)
    g.bind("p-plan", PPLAN)

    pid = doc["project"]["project_id"]
    proj = PW[pid]
    g.add((proj, RDF.type, PW.ProjectWorkflow))
    g.add((proj, RDFS.label, Literal(pid)))
    g.add((proj, PW.basedOn, WF.DitaDraftWorkflow))
    g.add((proj, PW.baseline, Literal(doc["approved"]["baseline"])))
    g.add((proj, PW.ontologySha256, Literal(doc["approved"]["ontology_source"]["sha256"])))
    g.add((proj, PW.approvedBy, Literal(doc["approved"]["approved_by"])))
    if doc["approved"].get("approved_on"):
        g.add((proj, PW.approvedOn, Literal(str(doc["approved"]["approved_on"]), datatype=XSD.date)))
    g.add((proj, PW.securityLevel, Literal(doc["project"]["security_level"])))
    g.add((proj, PW.specTransmissionAllowed,
           Literal(bool(doc["project"]["spec_transmission_allowed"]), datatype=XSD.boolean)))
    for mt in doc["project"]["manual_types"]:
        g.add((proj, PW.manualType, WF[f"mt_{mt}"]))

    for step in doc["steps"]:
        node = WF[step["id"]]
        sel = BNode()
        g.add((proj, PW.hasStepSelection, sel))
        g.add((sel, RDF.type, PW.StepSelection))
        g.add((sel, PW.step, node))
        g.add((sel, WF.stepNumber, Literal(int(step["number"]), datatype=XSD.integer)))
        g.add((sel, PW.included, Literal(bool(step["included"]), datatype=XSD.boolean)))
        if not step["included"] and step.get("exclusion_reason"):
            g.add((sel, PW.exclusionReason, Literal(step["exclusion_reason"])))
    return g


# ------------------------------------------------------------- 프로젝트 층 검사

def check_project_layer(rep: Report, doc: dict[str, Any], onto: Graph, ontology: Path) -> None:
    steps = {s["number"]: s for s in doc["steps"]}
    included = {n for n, s in steps.items() if s["included"]}
    excluded = {n for n, s in steps.items() if not s["included"]}

    # P3. 온톨로지 해시 일치
    actual = sha256_of(ontology)
    recorded = doc["approved"]["ontology_source"]["sha256"]
    if actual != recorded:
        rep.add(
            "error", "P3", "project",
            "확정 워크플로우가 참조하는 온톨로지와 지금 온톨로지가 다릅니다. "
            "export_approved_workflow.py --merge로 다시 생성한 뒤 3번 승인을 다시 받아야 합니다.",
            {"step": 4, "file": ontology.name},
            evidence={"expected": recorded, "actual": actual},
        )

    # P6. 승인 기록과 승인 당시 정의 해시
    apv = doc["approved"]
    approver = str(apv.get("approved_by") or "").strip()
    approved_hash = apv.get("approved_ontology_sha256")
    fix = ("3번 승인을 받은 뒤 export_approved_workflow.py --merge --approve '<승인자>'로 기록하십시오.")
    if not approver or approver.startswith("[TBD") or not apv.get("approved_on"):
        rep.add("error", "P6", "project", "3번 승인 기록(승인자·승인일)이 없습니다.",
                {"step": 3, "file": "approved-workflow.yaml"}, suggested_action=fix)
    elif not approved_hash:
        rep.add("error", "P6", "project",
                "승인 당시 온톨로지 해시가 없습니다. 무엇을 승인했는지 확인할 수 없습니다 "
                "(해시 기록 도입 전 승인이거나 승인자를 손으로 적은 경우).",
                {"step": 3, "file": "approved-workflow.yaml"}, suggested_action=fix)
    elif approved_hash != recorded:
        rep.add("error", "P6", "project",
                f"승인 후 워크플로우 정의가 바뀌었습니다. {approver}의 승인({apv.get('approved_on')})은 이전 정의에 대한 것입니다.",
                {"step": 3, "file": "approved-workflow.yaml"},
                evidence={"expected": recorded, "actual": approved_hash}, suggested_action=fix)

    # P4. 단계 집합 일치
    onto_numbers = {
        int(onto.value(s, WF.stepNumber))
        for s in onto.subjects(PPLAN.isStepOfPlan, WF.DitaDraftWorkflow)
        if onto.value(s, WF.stepNumber) is not None
    }
    missing, extra = sorted(onto_numbers - set(steps)), sorted(set(steps) - onto_numbers)
    if missing or extra:
        rep.add(
            "error", "P4", "project",
            "확정 워크플로우의 단계 집합이 온톨로지와 다릅니다.",
            {"step": 4},
            evidence={"expected": f"온톨로지 {sorted(onto_numbers)}", "actual": f"T28 {sorted(steps)}"},
        )

    # P2. 제외 사유 누락
    for n in sorted(excluded):
        s = steps[n]
        if s["requires_exclusion_reason"] and not (s.get("exclusion_reason") or "").strip():
            rep.add(
                "error", "P2", "project",
                f"{n}번 '{s['label']}'은 제외 시 사유가 필요한 단계입니다 (wf:requiresExclusionReason).",
                {"step": n, "node": f"wf:{s['id']}"},
                suggested_action="approved-workflow.yaml의 해당 단계 exclusion_reason을 채우고 3번 승인을 다시 받으십시오.",
            )

    # P1. 제외한 단계의 산출물을 포함된 단계가 입력으로 쓴다
    q = """
        PREFIX wf: <https://example.org/dita-wf#>
        PREFIX p-plan: <http://purl.org/net/p-plan#>
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        SELECT DISTINCT ?from ?to ?label WHERE {
            ?p p-plan:hasOutputVar ?v ; wf:stepNumber ?from .
            ?v rdfs:label ?label .
            { ?c p-plan:hasInputVar ?v } UNION { ?c p-plan:hasInputVar ?a . ?a wf:aliasOf ?v }
            ?c wf:stepNumber ?to .
            FILTER (?from != ?to)
        }
    """
    produced_elsewhere: dict[str, set[int]] = {}
    edges = []
    for row in onto.query(q):
        frm, to, lab = int(row["from"]), int(row["to"]), str(row["label"])
        edges.append((frm, to, lab))
        produced_elsewhere.setdefault(lab, set()).add(frm)
    for frm, to, lab in sorted(edges):
        if frm in excluded and to in included:
            # 같은 자료를 포함된 다른 단계도 만든다면 끊기지 않는다
            if produced_elsewhere[lab] & included:
                continue
            rep.add(
                "error", "P1", "project",
                f"{to}번의 입력 '{lab}'을 만드는 단계가 {frm}번뿐인데 {frm}번이 제외되었습니다.",
                {"step": to, "node": f"wf:step{frm:02d}"},
                evidence={"expected": f"{frm}번 포함 또는 '{lab}' 대체 입력", "actual": f"{frm}번 제외"},
                suggested_action=f"{frm}번을 포함하거나, {to}번의 입력을 사람이 채우는 자료로 바꾸십시오.",
            )

    # P5. 보안 등급으로 막히는 전송 (정보)
    if not doc["project"]["spec_transmission_allowed"]:
        for n in sorted(included):
            for tr in steps[n].get("external_transmissions") or []:
                if tr["condition"] == "보안 등급 허용 시":
                    rep.add(
                        "info", "P5", "project",
                        f"{n}번의 '{tr['data']}' 외부 전송이 보안 등급(level={doc['project']['security_level']})으로 막힙니다. "
                        "해당 단계는 사양서 없이 실행되므로 수치는 [TBD: 출처 필요]로 남습니다.",
                        {"step": n},
                    )

    rep.checked += len(steps)


# ------------------------------------------------------------------ SHACL 층 검사

def check_definition_layer(rep: Report, onto: Graph, shapes_path: Path) -> bool:
    shapes = Graph()
    shapes.parse(str(shapes_path), format="turtle")
    conforms, results, _ = shacl_validate(
        onto, shacl_graph=shapes, inference="none", advanced=True, allow_warnings=True
    )
    rows = []
    for r in set(results.subjects(SH.resultSeverity, None)):
        sev = SEVERITY.get(str(results.value(r, SH.resultSeverity)), "info")
        msg = str(results.value(r, SH.resultMessage) or "")
        focus = results.value(r, SH.focusNode)
        path = results.value(r, SH.resultPath)
        rule = msg.split(":", 1)[0].strip() if msg[:1] == "R" else "R?"
        rows.append((rule, sev, focus, path, msg))
    for rule, sev, focus, path, msg in sorted(rows, key=lambda x: (x[0], str(x[2]))):
        loc: dict[str, Any] = {"node": f"wf:{local(focus)}" if focus is not None else None}
        num = onto.value(focus, WF.stepNumber) if focus is not None else None
        if num is not None:
            loc["step"] = int(num)
        if path is not None:
            loc["element"] = local(path)
        rep.add(
            sev, rule, "shacl",
            msg[len(rule) + 1:].strip() if msg.startswith(rule) else msg,
            loc,
        )
    rep.checked += 8  # R1~R8
    return conforms


# ------------------------------------------------------------------------- 실행

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="4번 단계 워크플로우 검증. T31과 T30 리포트를 만듭니다.")
    ap.add_argument("--approved", required=True, type=Path, help="확정 워크플로우 YAML (T28)")
    ap.add_argument("--ontology", required=True, type=Path)
    ap.add_argument("--shapes", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path, help="리포트와 T31을 저장할 폴더")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--iteration", type=int, default=0)
    args = ap.parse_args(argv)

    for p in (args.approved, args.ontology, args.shapes):
        if not p.is_file():
            print(f"[오류] 파일이 없습니다: {p}", file=sys.stderr)
            return 3

    doc = yaml.load(args.approved.read_text(encoding="utf-8"))
    run_id = args.run_id or new_run_id()
    rep = Report(
        step=4, step_label=STEP_LABEL, run_id=run_id, iteration=args.iteration,
        checker={"kind": "script", "name": "validate_workflow.py + pyshacl", "version": _tool_version()},
        target_kind="workflow",
        files=[str(args.approved), str(args.ontology), str(args.shapes)],
    )

    t28_errors = validate_t28(doc)
    for e in t28_errors:
        rep.add("error", "P0", "project", f"확정 워크플로우 형식 오류 — {e}", {"step": 4, "file": args.approved.name})

    if not t28_errors:
        onto = Graph()
        onto.parse(str(args.ontology), format="turtle")
        check_definition_layer(rep, onto, args.shapes)
        check_project_layer(rep, doc, onto, args.ontology)

        t31 = build_project_graph(doc, args.ontology)
        args.out_dir.mkdir(parents=True, exist_ok=True)
        t31_path = args.out_dir / "project-workflow.ttl"
        t31.serialize(destination=str(t31_path), format="turtle")
        print(f"[생성] {t31_path} (트리플 {len(t31)}개)")

    try:
        path = rep.save(args.out_dir)
    except Exception as exc:  # 리포트가 스키마를 어기면 이후 단계가 읽을 수 없으므로 즉시 멈춘다
        print(f"[리포트 스키마 검증 실패] {exc}", file=sys.stderr)
        return 2

    s = rep.summary
    print(f"[생성] {path}")
    print(f"  판정 {rep.status} · 오류 {s['error']} · 경고 {s['warning']} · 정보 {s['info']} · 검사 {s['checked']}")
    for f in rep.findings:
        if f["severity"] != "info":
            print(f"  [{f['severity']}] {f['rule_id']} {f['message'][:100]}")
    print(f"  다음 단계: {'5번 (상품군 용어집 추출)' if rep.status == 'pass' else '3번 (워크플로우 승인 재검토)'}")
    return 0 if rep.status == "pass" else 1


def _tool_version() -> str:
    import pyshacl
    import rdflib

    return f"pyshacl {pyshacl.__version__} / rdflib {rdflib.__version__}"


if __name__ == "__main__":
    raise SystemExit(main())
