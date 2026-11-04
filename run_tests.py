#!/usr/bin/env python3
"""T28·T30·T32 자체 시험.

정상 입력이 통과하는지만 보면 검사가 실제로 동작하는지 알 수 없으므로, 규칙마다
일부러 틀린 입력(오류 fixture)을 만들어 그 규칙이 잡아내는지 확인한다. fixture는
실행할 때 임시 폴더에 만들고 끝나면 지운다 (파일 수를 늘리지 않기 위해).

실행
  python tests/run_tests.py --ontology ontology/dita-workflow.ttl --shapes ontology/dita-workflow-shapes.ttl
"""

from __future__ import annotations

import argparse
import copy
import io
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

yaml = YAML()
yaml.width = 4096
yaml.indent(mapping=2, sequence=4, offset=2)

PASS, FAIL = "통과", "실패"
results: list[tuple[str, str, str, str]] = []  # (번호, 항목, 기대, 결과)


def record(no: str, item: str, expected: str, ok: bool, detail: str = "") -> None:
    results.append((no, item, expected, f"{PASS if ok else FAIL}{' — ' + detail if detail else ''}"))


def dump_yaml(doc: dict, path: Path) -> None:
    buf = io.StringIO()
    yaml.dump(doc, buf)
    path.write_text(buf.getvalue(), encoding="utf-8")


def run_validate(approved: Path, ontology: Path, shapes: Path, out_dir: Path) -> tuple[int, dict]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "validate_workflow.py"),
         "--approved", str(approved), "--ontology", str(ontology),
         "--shapes", str(shapes), "--out-dir", str(out_dir)],
        capture_output=True, text=True,
    )
    reports = sorted(out_dir.glob("report-04-i*.json"))
    doc = json.loads(reports[-1].read_text(encoding="utf-8")) if reports else {}
    return proc.returncode, doc


def rules_in(report: dict, severity: str | None = None) -> set[str]:
    return {f["rule_id"] for f in report.get("findings", []) if severity is None or f["severity"] == severity}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ontology", type=Path, default=ROOT / "ontology" / "dita-workflow.ttl")
    ap.add_argument("--shapes", type=Path, default=ROOT / "ontology" / "dita-workflow-shapes.ttl")
    ap.add_argument("--project", type=Path, default=None)
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="wf-test-"))
    try:
        # ---------- 기준 T28 생성 ----------
        base = tmp / "approved-workflow.yaml"
        cmd = [sys.executable, str(SCRIPTS / "export_approved_workflow.py"),
               "--ontology", str(args.ontology), "--out", str(base)]
        if args.project:
            cmd += ["--project", str(args.project)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        doc = yaml.load(base.read_text(encoding="utf-8"))
        record("C01", "T28 생성 — 단계 19개", "19", len(doc["steps"]) == 19, f"{len(doc['steps'])}개")
        record("C02", "T28 생성 — 전이 27개", "27", len(doc["transitions"]) == 27, f"{len(doc['transitions'])}개")
        record("C03", "T28 생성 — 반복 상한 3회", "3", doc["loop"]["max_iterations"] == 3, str(doc["loop"]["max_iterations"]))
        record("C04", "T28 생성 — 생략 규칙 2건", "2", len(doc["skip_rules"]) == 2, f"{len(doc['skip_rules'])}건")
        record("C05", "T28 생성 — 원안 기록(orig*) 제외", "0건", all(s["id"].startswith("step") for s in doc["steps"]))
        llm_steps = sorted(s["number"] for s in doc["steps"] if s["sends_to_external_llm"])
        # v3 기준 외부 전송 단계: 1(prompt-translator), 2(워크플로우 추출), 6(목차), 8(토픽),
        # 13(판단형 규칙에 LLM 사용 시), 15(내용 검증), 17(수정). 5번은 사람의 용어집 추출이라 전송 없음.
        record("C06", "T28 생성 — 외부 전송 단계", "1·2·6·8·13·15·17번",
               llm_steps == [1, 2, 6, 8, 13, 15, 17], str(llm_steps))

        # 프로젝트 블록이 비어 있으면 아래 시험이 스키마에서 먼저 막히므로 채운다
        if "[TBD" in json.dumps(doc["project"], ensure_ascii=False):
            doc["project"] = {"project_id": "test-fixture", "manual_types": ["installation"],
                              "security_level": "public", "spec_transmission_allowed": True}
            dump_yaml(doc, base)

        # 3번 승인 기록 (P6) — 기준 T28은 승인된 상태로 둔다
        export = [sys.executable, str(SCRIPTS / "export_approved_workflow.py")]
        subprocess.run(export + ["--ontology", str(args.ontology), "--out", str(base), "--merge",
                                 "--approve", "시험 승인자", "--approved-on", "2026-11-04"],
                       capture_output=True, text=True, check=True)
        doc = yaml.load(base.read_text(encoding="utf-8"))

        # ---------- C07. 정상 입력 통과 ----------
        code, rep = run_validate(base, args.ontology, args.shapes, tmp / "ok")
        record("C07", "정상 입력 — 판정 pass", "pass / 종료 0",
               code == 0 and rep.get("status") == "pass", f"{rep.get('status')} / {code}")
        record("C08", "정상 입력 — SHACL 위반 0건", "오류 0",
               rep["summary"]["error"] == 0, f"오류 {rep['summary']['error']}")
        record("C09", "정상 입력 — R8 정보 9건 (작성 필요 리소스)", "9",
               rep["summary"]["info"] == 9, f"{rep['summary']['info']}건")
        record("C10", "리포트가 T32 스키마를 통과", "통과", bool(rep) and rep["schema_version"] == "1")
        record("C11", "T31 생성", "파일 있음", (tmp / "ok" / "project-workflow.ttl").is_file())

        # ---------- C12. P2 제외 사유 누락 ----------
        d = copy.deepcopy(doc)
        for s in d["steps"]:
            if s["number"] == 13:  # requires_exclusion_reason = true
                s["included"] = False
                s["exclusion_reason"] = None
        p = tmp / "bad-p2.yaml"; dump_yaml(d, p)
        code, rep = run_validate(p, args.ontology, args.shapes, tmp / "p2")
        # 스키마(T28)가 먼저 잡거나 P2가 잡는다. 둘 중 하나면 통과.
        caught = "P2" in rules_in(rep, "error") or "P0" in rules_in(rep, "error")
        record("C12", "P2 제외 사유 누락 검출 (T28 스키마가 먼저 잡음)", "오류 보고",
               caught and code == 1, f"규칙 {sorted(rules_in(rep,'error'))}")

        # ---------- C12b. 공백만 적은 사유 — 스키마는 통과하므로 P2가 잡아야 한다 ----------
        d = copy.deepcopy(doc)
        for s in d["steps"]:
            if s["number"] == 13:
                s["included"] = False
                s["exclusion_reason"] = "      "  # 길이는 충족, 내용은 없음
        p = tmp / "bad-p2b.yaml"; dump_yaml(d, p)
        code, rep = run_validate(p, args.ontology, args.shapes, tmp / "p2b")
        record("C12b", "P2 공백 사유 검출", "오류 보고 (P2)",
               "P2" in rules_in(rep, "error") and code == 1, f"규칙 {sorted(rules_in(rep,'error'))}")

        # ---------- C13. P1 연결 끊김 (10번 제외) ----------
        d = copy.deepcopy(doc)
        for s in d["steps"]:
            if s["number"] == 10:
                s["included"] = False
                s["exclusion_reason"] = "시험용 — 용어 검사를 빼고 16번 입력이 끊기는지 확인"
        p = tmp / "bad-p1.yaml"; dump_yaml(d, p)
        code, rep = run_validate(p, args.ontology, args.shapes, tmp / "p1")
        p1 = [f for f in rep["findings"] if f["rule_id"] == "P1"]
        record("C13", "P1 연결 끊김 검출 (10번 제외 → 16번 입력)", "오류 1건",
               len(p1) == 1 and code == 1, f"{len(p1)}건 / {p1[0]['message'][:48] if p1 else ''}")

        # ---------- C14. P3 온톨로지 해시 불일치 ----------
        d = copy.deepcopy(doc)
        d["approved"]["ontology_source"]["sha256"] = "0" * 64
        p = tmp / "bad-p3.yaml"; dump_yaml(d, p)
        code, rep = run_validate(p, args.ontology, args.shapes, tmp / "p3")
        record("C14", "P3 온톨로지 해시 불일치 검출", "오류 보고",
               "P3" in rules_in(rep, "error") and code == 1, f"규칙 {sorted(rules_in(rep,'error'))}")

        # ---------- C15. P4 단계 집합 불일치 ----------
        d = copy.deepcopy(doc)
        d["steps"] = [s for s in d["steps"] if s["number"] != 12]
        p = tmp / "bad-p4.yaml"; dump_yaml(d, p)
        code, rep = run_validate(p, args.ontology, args.shapes, tmp / "p4")
        record("C15", "P4 단계 집합 불일치 검출", "오류 보고",
               "P4" in rules_in(rep, "error") and code == 1, f"규칙 {sorted(rules_in(rep,'error'))}")

        # ---------- C16. P5 보안 등급으로 막히는 전송 ----------
        d = copy.deepcopy(doc)
        d["project"]["security_level"] = "confidential"
        d["project"]["spec_transmission_allowed"] = False
        p = tmp / "sec.yaml"; dump_yaml(d, p)
        code, rep = run_validate(p, args.ontology, args.shapes, tmp / "p5")
        p5 = [f for f in rep["findings"] if f["rule_id"] == "P5"]
        steps5 = sorted(f["location"]["step"] for f in p5)
        record("C16", "P5 사양서 전송 차단 보고 (8·15번)", "정보 2건 / 8·15번",
               steps5 == [8, 15], f"{len(p5)}건 {steps5}")

        # ---------- C17. SHACL R1 검출 (온톨로지 훼손) ----------
        # 문자열 치환은 Turtle 구분자(; 와 .)를 깨뜨리므로 그래프에서 트리플을 지운다
        bad_onto = tmp / "bad-ontology.ttl"
        from rdflib import Graph as _G, Namespace as _NS, Literal as _L, XSD as _XSD
        _WF = _NS("https://example.org/dita-wf#")
        bg = _G(); bg.parse(str(args.ontology), format="turtle")
        before = len(bg)
        bg.remove((_WF.step11, _WF.sendsToExternalLLM, _L(False, datatype=_XSD.boolean)))
        assert len(bg) == before - 1, "fixture 생성 실패: 지울 트리플을 찾지 못했습니다"
        bg.serialize(destination=str(bad_onto), format="turtle")
        d = copy.deepcopy(doc)
        import hashlib
        d["approved"]["ontology_source"]["sha256"] = hashlib.sha256(bad_onto.read_bytes()).hexdigest()
        p = tmp / "bad-shacl.yaml"; dump_yaml(d, p)
        code, rep = run_validate(p, bad_onto, args.shapes, tmp / "r1")
        r1 = [f for f in rep["findings"] if f["rule_id"] == "R1"]
        record("C17", "SHACL R1 검출 (11번 전송 여부 누락)", "오류 1건 이상",
               len(r1) >= 1 and code == 1, f"{len(r1)}건")

        # ---------- C18. rdfs 추론을 켜면 거짓 위반이 생기는지 확인 ----------
        import warnings
        warnings.filterwarnings("ignore")
        from rdflib import Graph
        from rdflib.namespace import SH
        from pyshacl import validate as sv
        og = Graph(); og.parse(str(args.ontology), format="turtle")
        sg = Graph(); sg.parse(str(args.shapes), format="turtle")
        counts = {}
        for inf in ("none", "rdfs"):
            _, rg, _ = sv(og, shacl_graph=sg, inference=inf, advanced=True, allow_warnings=True)
            viol = sum(1 for r in set(rg.subjects(SH.resultSeverity, None))
                       if str(rg.value(r, SH.resultSeverity)) == str(SH.Violation))
            counts[inf] = viol
        record("C18", "inference 설정 영향 (none=0, rdfs=거짓 위반)", "none 0 / rdfs >0",
               counts["none"] == 0 and counts["rdfs"] > 0, f"none {counts['none']} / rdfs {counts['rdfs']}")

        # ---------- C19. T32 스키마가 잘못된 리포트를 거부 ----------
        from report_lib import validate_report
        good = json.loads((tmp / "ok" / "report-04-i0.json").read_text(encoding="utf-8"))
        cases = [
            ("허용 표시에 승인자 없음", lambda r: r["findings"][0].update({"accepted": True})),
            ("심각도 값이 규격 밖", lambda r: r["findings"][0].update({"severity": "critical"})),
            ("정의되지 않은 필드", lambda r: r.update({"unknown_field": 1})),
            ("base_revision 형식 위반", lambda r: r["target"].update({"base_revision": "xyz"})),
            ("단계 번호 범위 밖", lambda r: r.update({"step": 42})),
            ("날짜·시각 형식 위반", lambda r: r.update({"started_at": "2026-10-30 12:00"})),
        ]
        rejected = 0
        for name, mutate in cases:
            bad = copy.deepcopy(good)
            mutate(bad)
            try:
                validate_report(bad)
            except Exception:
                rejected += 1
            else:
                record(f"C19-{name}", f"T32 스키마 거부 — {name}", "거부", False, "통과시킴")
        record("C19", f"T32 스키마가 잘못된 리포트 {len(cases)}종을 거부", f"{len(cases)}건 거부",
               rejected == len(cases), f"{rejected}/{len(cases)}건")

        # ---------- C20. 사람 입력 보존 (--merge) ----------
        merged = tmp / "merge.yaml"
        shutil.copy(base, merged)
        d = yaml.load(merged.read_text(encoding="utf-8"))
        d["approved"]["approved_by"] = "홍길동"
        d["approved"]["approved_on"] = "2026-10-30"
        for s in d["steps"]:
            if s["number"] == 13:
                s["included"] = False
                s["exclusion_reason"] = "시험용 사유"
        dump_yaml(d, merged)
        subprocess.run([sys.executable, str(SCRIPTS / "export_approved_workflow.py"),
                        "--ontology", str(args.ontology), "--out", str(merged), "--merge"],
                       capture_output=True, text=True)
        d2 = yaml.load(merged.read_text(encoding="utf-8"))
        s13 = next(s for s in d2["steps"] if s["number"] == 13)
        ok = (d2["approved"]["approved_by"] == "홍길동"
              and str(d2["approved"]["approved_on"]) == "2026-10-30"
              and s13["included"] is False
              and s13["exclusion_reason"] == "시험용 사유"
              and d2["approved"]["approved_ontology_sha256"] == doc["approved"]["approved_ontology_sha256"])
        record("C20", "--merge 사람 입력 보존", "승인자·승인일·승인 해시·제외 설정 유지", ok)

        # ---------- C26~C29. P6 승인 기록과 승인 당시 정의 해시 (사용자 결정 2026-11-04) ----------
        d = copy.deepcopy(doc)
        d["approved"].update({"approved_by": "[TBD: 출처 필요 — 3번 승인자]", "approved_on": None,
                              "approved_ontology_sha256": None})
        p = tmp / "p6-none.yaml"; dump_yaml(d, p)
        code, rep = run_validate(p, args.ontology, args.shapes, tmp / "p6a")
        record("C26", "P6 승인 기록 없음 검출", "오류 (P6)",
               "P6" in rules_in(rep, "error") and code == 1, f"규칙 {sorted(rules_in(rep,'error'))}")

        d = copy.deepcopy(doc)
        d["approved"].update({"approved_by": "홍길동", "approved_on": "2026-11-04", "approved_ontology_sha256": None})
        p = tmp / "p6-nohash.yaml"; dump_yaml(d, p)
        code, rep = run_validate(p, args.ontology, args.shapes, tmp / "p6b")
        record("C27", "P6 손으로 적은 승인(해시 없음) 검출", "오류 (P6)",
               "P6" in rules_in(rep, "error") and code == 1, f"규칙 {sorted(rules_in(rep,'error'))}")

        # 정의 변경 → --merge 재생성: P3은 통과하지만(해시가 새 값) 승인 해시는 옛 값 → P6
        from rdflib import RDFS as _RDFS
        chg_onto = tmp / "changed-ontology.ttl"
        cg = _G(); cg.parse(str(args.ontology), format="turtle")
        cg.add((_WF.step03, _RDFS.comment, _L("시험용 정의 변경")))
        cg.serialize(destination=str(chg_onto), format="turtle")
        gap = tmp / "gap.yaml"; shutil.copy(base, gap)
        subprocess.run(export + ["--ontology", str(chg_onto), "--out", str(gap), "--merge"],
                       capture_output=True, text=True, check=True)
        code, rep = run_validate(gap, chg_onto, args.shapes, tmp / "p6c")
        errs = rules_in(rep, "error")
        record("C28", "정의 변경 후 --merge — 옛 승인 통과 차단", "P3 없음 / P6 오류",
               "P3" not in errs and "P6" in errs and code == 1, f"규칙 {sorted(errs)}")

        subprocess.run(export + ["--ontology", str(chg_onto), "--out", str(gap), "--merge",
                                 "--approve", "[user1]", "--approved-on", "2026-11-04"],
                       capture_output=True, text=True, check=True)
        code, rep = run_validate(gap, chg_onto, args.shapes, tmp / "p6d")
        g2 = yaml.load(gap.read_text(encoding="utf-8"))
        import hashlib as _h
        ok = (code == 0 and rep.get("status") == "pass"
              and g2["approved"]["approved_by"] == "[user1]"
              and g2["approved"]["approved_ontology_sha256"] == _h.sha256(chg_onto.read_bytes()).hexdigest())
        record("C29", "--approve 재승인 후 통과 (승인자 '[user1]' 문자열 유지)", "pass / 해시 = 새 정의",
               ok, f"{rep.get('status')} / {code}")

        # ---------- C21~C25. T36 에이전트 정의 ----------
        def run_agents(agents_dir: Path, ontology: Path, mode: str = "--check"):
            pr = subprocess.run(
                [sys.executable, str(SCRIPTS / "export_agent_definitions.py"),
                 "--agents-dir", str(agents_dir), "--ontology", str(ontology), mode],
                capture_output=True, text=True)
            return pr.returncode, pr.stdout + pr.stderr

        src = ROOT / "agents"
        code, out = run_agents(src, args.ontology)
        record("C21", "T36 정의 5개가 온톨로지와 일치", "종료 0 / 5개 일치",
               code == 0 and out.count("일치") >= 5, f"종료 {code}")

        adir = tmp / "agents"; shutil.copytree(src, adir)
        code, out = run_agents(adir, bad_onto)
        record("C22", "온톨로지가 바뀌면 불일치로 잡음", "종료 1",
               code == 1 and ("불일치" in out), f"종료 {code}")

        adir2 = tmp / "agents2"; shutil.copytree(src, adir2)
        f = adir2 / "fixer.md"
        f.write_text(f.read_text(encoding="utf-8").replace("name: fixer", "name: wrong-name"), encoding="utf-8")
        code, out = run_agents(adir2, args.ontology)
        record("C23", "name과 wf_agent 불일치 검출", "종료 1",
               code == 1 and "wf_agent" in out, f"종료 {code}")

        adir3 = tmp / "agents3"; shutil.copytree(src, adir3)
        f = adir3 / "fixer.md"
        lines = [ln for ln in f.read_text(encoding="utf-8").splitlines() if not ln.startswith("description:")]
        f.write_text("\n".join(lines), encoding="utf-8")
        code, out = run_agents(adir3, args.ontology)
        record("C24", "description 누락 검출 (Claude Code가 건너뛰는 조건)", "종료 1",
               code == 1 and "description" in out, f"종료 {code}")

        adir4 = tmp / "agents4"; shutil.copytree(src, adir4)
        f = adir4 / "fixer.md"
        f.write_text(f.read_text(encoding="utf-8").replace("{{iteration}}", "{{iteration_no}}", 1), encoding="utf-8")
        code, out = run_agents(adir4, args.ontology)
        record("C25", "치환 표식 불일치 검출", "종료 1",
               code == 1 and "치환 표식" in out, f"종료 {code}")

        # ---------- 결과 표 ----------
        width = max(len(r[1]) for r in results) + 2
        print(f"\n| 번호 | 항목 | 기대 | 결과 |")
        print(f"|---|---|---|---|")
        for no, item, exp, res in results:
            print(f"| {no} | {item} | {exp} | {res} |")
        failed = [r for r in results if r[3].startswith(FAIL)]
        print(f"\n{len(results) - len(failed)}/{len(results)} 통과")
        return 1 if failed else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
