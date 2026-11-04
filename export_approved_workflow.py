#!/usr/bin/env python3
"""T28 확정 워크플로우(approved-workflow.yaml)를 온톨로지에서 생성한다.

단계 정보의 원본은 dita-workflow.ttl(T03) 하나다 (사용자 결정 2026-10-30).
엑셀(v3 워크플로우 표)은 설계 기획안이다. 엑셀의 워크플로우가 모두 구현되면 온톨로지에
한 번에 반영한다 (사용자 결정 2026-11-04). 그 전까지 이 스크립트와 4번 검증은 엑셀에만 있는
변경(예: 16 → 6·7 목차 변경 복귀)을 알지 못한다.
이 스크립트는 온톨로지를 SPARQL로 읽어 기계가 읽는 YAML로 내보내고,
사람이 채우는 항목만 보존·병합한다.

사람이 채우는 항목
  approved.approved_by, approved.approved_on
  approved.approved_ontology_sha256       (--approve가 기록한다. 손으로 적지 않는다)
  project.*                               (--project 로 T01에서 가져오거나 직접 편집)
  steps[].included, steps[].exclusion_reason

사용 예
  python export_approved_workflow.py \
      --ontology ../ontology/dita-workflow.ttl \
      --project  ../../ev200kw-test/ev200kw-map/profiles/project-definition.yaml \
      --out      ../out/approved-workflow.yaml

  # 두 번째 실행부터는 사람이 편집한 항목을 보존한다
  python export_approved_workflow.py --ontology ... --out ../out/approved-workflow.yaml --merge

  # 3번 승인 기록 — 승인자·승인일과 함께 지금 온톨로지 해시를 남긴다 (사용자 결정 2026-11-04)
  # 이후 정의가 바뀌어 --merge로 다시 만들면 이 해시는 옛 값으로 남고, 4번 P6이 재승인을 요구한다.
  python export_approved_workflow.py --ontology ... --out ../out/approved-workflow.yaml --merge --approve '[user1]'

종료 코드: 0 = 생성·스키마 검증 통과, 2 = 스키마 검증 실패, 3 = 입력 오류
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Any

from rdflib import Graph, Namespace, RDF, RDFS, URIRef
from ruamel.yaml import YAML

sys.path.insert(0, str(Path(__file__).resolve().parent))
from report_lib import sha256_of, utc_now  # noqa: E402

SCRIPT_VERSION = "1"
WF = Namespace("https://example.org/dita-wf#")
PPLAN = Namespace("http://purl.org/net/p-plan#")
SCHEMA = Path(__file__).resolve().parent.parent / "schemas" / "approved-workflow.schema.json"

TBD_APPROVER = "[TBD: 출처 필요 — 3번 승인자]"
TBD = "[TBD: 출처 필요]"

PERFORMER_KIND = {WF.Human: "human", WF.LLMAgent: "llm", WF.Script: "script"}

yaml = YAML()
yaml.preserve_quotes = True
yaml.width = 4096
yaml.indent(mapping=2, sequence=4, offset=2)


# --------------------------------------------------------------------------- 읽기

def local(node: Any) -> str:
    return str(node).split("#")[-1]


def label_of(g: Graph, node: URIRef) -> str:
    for o in g.objects(node, RDFS.label):
        return str(o)
    return local(node)


def step_nodes(g: Graph) -> list[tuple[int, URIRef]]:
    """계획에 속한 실행 단계만 고른다. wf:OriginalStep(원안 기록)은 제외된다."""
    out = []
    for s in g.subjects(PPLAN.isStepOfPlan, WF.DitaDraftWorkflow):
        n = g.value(s, WF.stepNumber)
        if n is not None:
            out.append((int(n), s))
    return sorted(out)


def step_to_dict(g: Graph, number: int, node: URIRef) -> dict[str, Any]:
    types = [t for t in g.objects(node, RDF.type) if t != WF.Step]
    performers = []
    for p in g.objects(node, WF.performedBy):
        kinds = [PERFORMER_KIND[t] for t in g.objects(p, RDF.type) if t in PERFORMER_KIND]
        performers.append({"kind": kinds[0] if kinds else "script", "name": label_of(g, p)})
    performers.sort(key=lambda x: (x["kind"], x["name"]))

    resources = []
    for r in g.objects(node, WF.usesResource):
        item: dict[str, Any] = {"label": label_of(g, r)}
        tid = g.value(r, WF.templateId)
        if tid:
            item["template_id"] = str(tid)
        av = g.value(r, WF.availability)
        if av is not None:
            item["availability"] = label_of(g, av)
        resources.append(item)
    resources.sort(key=lambda x: x["label"])

    transmissions = []
    for x in g.objects(node, WF.hasExternalTransmission):
        data = [label_of(g, d) for d in g.objects(x, WF.transmits)]
        cond = [label_of(g, c) for c in g.objects(x, WF.conditionalOn)]
        for d in sorted(data):
            transmissions.append({"data": d, "condition": cond[0] if cond else "항상"})
    transmissions.sort(key=lambda x: (x["data"], x["condition"]))

    sends = g.value(node, WF.sendsToExternalLLM)
    rer = g.value(node, WF.requiresExclusionReason)

    doc: dict[str, Any] = {
        "number": number,
        "id": local(node),
        "label": label_of(g, node),
        "type": local(types[0]) if types else "Step",
        "performed_by": performers,
        "task": str(g.value(node, WF.task) or TBD),
        "inputs": sorted(label_of(g, v) for v in g.objects(node, PPLAN.hasInputVar)),
        "outputs": sorted(label_of(g, v) for v in g.objects(node, PPLAN.hasOutputVar)),
        "sends_to_external_llm": bool(sends.toPython()) if sends is not None else False,
        "included": True,
        "requires_exclusion_reason": bool(rer.toPython()) if rer is not None else False,
        "exclusion_reason": None,
    }
    if resources:
        doc["resources"] = resources
    if transmissions:
        doc["external_transmissions"] = transmissions
    constraints = sorted(label_of(g, c) for c in g.objects(node, WF.hasConstraint))
    if constraints:
        doc["constraints"] = constraints
    return doc


def transitions_to_list(g: Graph) -> list[dict[str, Any]]:
    rows = []
    for t in g.subjects(RDF.type, WF.Transition):
        frm, to = g.value(t, WF.fromStep), g.value(t, WF.toStep)
        cond = g.value(t, WF.onCondition)
        cond_types = [local(c) for c in g.objects(cond, RDF.type)] if cond else []
        cti = g.value(t, WF.countsTowardIteration)
        gov = g.value(t, WF.governedBy)
        note = g.value(t, RDFS.comment)
        rows.append({
            "id": local(t),
            "from": local(frm) if frm else TBD,
            "to": local(to) if to else TBD,
            "condition": label_of(g, cond) if cond else "무조건",
            "condition_type": cond_types[0] if cond_types else "Condition",
            "counts_toward_iteration": bool(cti.toPython()) if cti is not None else None,
            "governed_by": local(gov) if gov else None,
            "note": str(note) if note else None,
        })
    return sorted(rows, key=lambda r: r["id"])


def loop_to_dict(g: Graph) -> dict[str, Any]:
    node = WF.FixReverifyLoop
    body = sorted(int(g.value(s, WF.stepNumber)) for s in g.objects(node, WF.loopBody))
    exit_to = g.value(node, WF.exitOnPassTo)
    err_to = g.value(node, WF.onErrorsRemainTo)
    rule = g.value(node, WF.errorCountingRule)
    passr = g.value(node, WF.passRule)
    return {
        "id": local(node),
        "label": label_of(g, node),
        "body": body,
        "max_iterations": int(g.value(node, WF.maxIterations)),
        "exit_on_pass_to": int(g.value(exit_to, WF.stepNumber)) if exit_to else None,
        "on_errors_remain_to": int(g.value(err_to, WF.stepNumber)) if err_to else None,
        "on_limit_reached": str(g.value(node, WF.onLimitReached) or TBD),
        "error_counting_rule": str(rule) if rule is not None else None,
        "pass_rule": str(passr) if passr is not None else None,
    }


def skip_rules_to_list(g: Graph) -> list[dict[str, Any]]:
    rows = []
    for r in g.subjects(RDF.type, WF.SkipRule):
        rows.append({
            "id": local(r),
            "label": label_of(g, r),
            "triggered_by_failure_of": sorted(int(g.value(s, WF.stepNumber)) for s in g.objects(r, WF.triggeredByFailureOf)),
            "skips": sorted(int(g.value(s, WF.stepNumber)) for s in g.objects(r, WF.skips)),
            "scope": str(g.value(r, WF.scope) or TBD),
            "reason": str(g.value(r, WF.reason)) if g.value(r, WF.reason) else None,
        })
    return sorted(rows, key=lambda x: x["id"])


# ------------------------------------------------------------------- 프로젝트 블록

def project_from_t01(path: Path) -> dict[str, Any]:
    """T01 프로젝트 정의서에서 프로젝트 블록을 만든다. 없는 값은 [TBD]로 둔다."""
    data = yaml.load(path.read_text(encoding="utf-8")) or {}
    doc = data.get("document") or {}
    sec = data.get("security") or {}
    transfer = (sec.get("llm_transfer") or {}).get("spec")
    mt = doc.get("manual_type")
    manual_types = [mt] if isinstance(mt, str) else list(doc.get("manual_types") or [])
    return {
        "project_id": str(data.get("doc_id") or TBD),
        "manual_types": manual_types or [TBD],
        "security_level": str(sec.get("level") or TBD),
        "spec_transmission_allowed": transfer == "allowed",
    }


def blank_project() -> dict[str, Any]:
    return {
        "project_id": TBD,
        "manual_types": [TBD],
        "security_level": TBD,
        "spec_transmission_allowed": False,
    }


# ----------------------------------------------------------------------- 병합·저장

HUMAN_STEP_FIELDS = ("included", "exclusion_reason")


def merge_human_fields(new: dict[str, Any], old: dict[str, Any]) -> list[str]:
    """사람이 채운 항목을 이전 판에서 가져온다. 가져온 항목 목록을 돌려준다."""
    carried = []
    for key in ("approved_by", "approved_on", "approved_ontology_sha256"):
        val = (old.get("approved") or {}).get(key)
        if val not in (None, "", TBD_APPROVER):
            new["approved"][key] = val
            carried.append(f"approved.{key}")
    old_project = old.get("project")
    if old_project and TBD not in json.dumps(old_project, ensure_ascii=False):
        new["project"] = old_project
        carried.append("project")
    old_steps = {s.get("id"): s for s in (old.get("steps") or [])}
    for step in new["steps"]:
        prev = old_steps.get(step["id"])
        if not prev:
            continue
        for field in HUMAN_STEP_FIELDS:
            if field in prev and prev[field] != step.get(field):
                step[field] = prev[field]
                carried.append(f"{step['id']}.{field}")
    return carried


HEADER = """\
# 확정 워크플로우 (T28) — 3번 단계 산출물, 4번 단계 입력
#
# 이 파일은 export_approved_workflow.py가 온톨로지 {src}에서 생성합니다.
# 단계·전이·반복·생략 규칙을 손으로 고치지 마십시오. 다시 생성하면 덮어씁니다.
# 고칠 항목은 온톨로지(T03)입니다.
#
# 사람이 채우는 항목 (다시 생성해도 --merge로 보존됩니다)
#   approved.approved_by, approved.approved_on
#   approved.approved_ontology_sha256 (--approve가 기록. 정의가 바뀌면 4번 P6이 3번 재승인을 요구)
#   project 블록
#   steps[].included, steps[].exclusion_reason
#
# 생성: {at} / 스크립트 판 {ver}
"""


def build(ontology: Path, project_file: Path | None) -> dict[str, Any]:
    g = Graph()
    g.parse(str(ontology), format="turtle")
    doc = {
        "schema_version": "1",
        "approved": {
            "workflow_id": "dita-draft-v3",
            "workflow_label": label_of(g, WF.DitaDraftWorkflow),
            "baseline": "v3",
            "ontology_source": {"file": ontology.name, "sha256": sha256_of(ontology)},
            "generated_by": {
                "script": Path(__file__).name,
                "version": SCRIPT_VERSION,
                "generated_at": utc_now(),
            },
            "approved_by": TBD_APPROVER,
            "approved_on": None,
            "approved_ontology_sha256": None,
        },
        "project": project_from_t01(project_file) if project_file else blank_project(),
        "steps": [step_to_dict(g, n, s) for n, s in step_nodes(g)],
        "transitions": transitions_to_list(g),
        "loop": loop_to_dict(g),
        "skip_rules": skip_rules_to_list(g),
    }
    return doc


def validate_doc(doc: dict[str, Any]) -> list[str]:
    import jsonschema

    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    cls = jsonschema.validators.validator_for(schema)
    cls.check_schema(schema)
    v = cls(schema, format_checker=cls.FORMAT_CHECKER)
    return [f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}" for e in v.iter_errors(doc)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="온톨로지(T03)에서 확정 워크플로우 YAML(T28)을 생성합니다.")
    ap.add_argument("--ontology", required=True, type=Path)
    ap.add_argument("--project", type=Path, help="T01 프로젝트 정의서 (project-definition.yaml)")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--merge", action="store_true", help="--out에 이미 있는 파일에서 사람이 채운 항목을 가져옵니다")
    ap.add_argument("--approve", metavar="승인자", help="3번 승인 기록: 승인자·승인일·지금 온톨로지 해시를 함께 적습니다")
    ap.add_argument("--approved-on", metavar="YYYY-MM-DD", help="--approve와 함께. 생략하면 오늘 날짜")
    args = ap.parse_args(argv)

    if not args.ontology.is_file():
        print(f"[오류] 온톨로지 파일이 없습니다: {args.ontology}", file=sys.stderr)
        return 3
    if args.project and not args.project.is_file():
        print(f"[오류] 프로젝트 정의서가 없습니다: {args.project}", file=sys.stderr)
        return 3

    doc = build(args.ontology, args.project)

    carried: list[str] = []
    if args.merge and args.out.is_file():
        old = yaml.load(args.out.read_text(encoding="utf-8")) or {}
        carried = merge_human_fields(doc, old)

    if args.approved_on and not args.approve:
        print("[오류] --approved-on은 --approve와 함께 써야 합니다", file=sys.stderr)
        return 3
    if args.approve is not None:
        name = args.approve.strip()
        if not name or name.startswith("[TBD"):
            print("[오류] --approve에 승인자를 적어야 합니다", file=sys.stderr)
            return 3
        on = args.approved_on or _dt.date.today().isoformat()
        try:
            _dt.date.fromisoformat(on)
        except ValueError:
            print(f"[오류] 승인일 형식이 YYYY-MM-DD가 아닙니다: {on}", file=sys.stderr)
            return 3
        doc["approved"]["approved_by"] = name
        doc["approved"]["approved_on"] = on
        doc["approved"]["approved_ontology_sha256"] = doc["approved"]["ontology_source"]["sha256"]
        print(f"[승인 기록] {name} / {on} / 온톨로지 {doc['approved']['ontology_source']['sha256'][:12]}")

    errors = validate_doc(doc)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    text = HEADER.format(src=args.ontology.name, at=doc["approved"]["generated_by"]["generated_at"], ver=SCRIPT_VERSION)
    import io

    buf = io.StringIO()
    yaml.dump(doc, buf)
    args.out.write_text(text + buf.getvalue(), encoding="utf-8")

    print(f"[생성] {args.out}")
    print(f"  단계 {len(doc['steps'])}개 / 전이 {len(doc['transitions'])}개 / 생략 규칙 {len(doc['skip_rules'])}개 / 반복 상한 {doc['loop']['max_iterations']}회")
    if carried:
        print(f"  보존한 사람 입력 {len(carried)}건: {', '.join(carried[:8])}{' …' if len(carried) > 8 else ''}")
    # 사람이 채우는 항목이 비어 있는 것은 구조 오류가 아니라 '입력 대기'로 구분한다.
    pending = [e for e in errors if e.startswith("project") or e.startswith("approved/approved_")]
    broken = [e for e in errors if e not in pending]
    if pending:
        print(f"[사람 입력 대기] {len(pending)}건 — 3번 승인 전에 채워야 합니다")
        for e in pending:
            print(f"  - {e}")
    if broken:
        print(f"[스키마 검증 실패] {len(broken)}건", file=sys.stderr)
        for e in broken[:20]:
            print(f"  - {e}", file=sys.stderr)
        return 2
    print("[스키마 검증] 구조 통과 (approved-workflow.schema.json)" + (" · 사람 입력 미완료" if pending else " · 사람 입력 완료"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
