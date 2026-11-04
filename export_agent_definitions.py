#!/usr/bin/env python3
"""에이전트 정의 파일(T36)의 고정부 생성·검사·배포.

에이전트 정의에는 단계의 입력·출력·금지·외부 전송이 적혀야 한다. 그런데 그 사실은 이미
온톨로지(T03)에 있다. 두 곳에 적으면 갈라지므로, 정의 파일에 표식으로 구간을 두고
이 스크립트가 온톨로지에서 그 구간을 생성한다. 사람은 표식 밖의 작업 지침만 쓴다.

    <!-- wf:generated:start -->   ← 이 사이는 스크립트가 씀
    <!-- wf:generated:end -->

사용
  python scripts/export_agent_definitions.py --write    # 생성 구간 갱신 (+ frontmatter 해시 갱신)
  python scripts/export_agent_definitions.py --check    # 온톨로지와 어긋나는지 검사 (종료 코드 1)
  python scripts/export_agent_definitions.py --deploy ../.claude/agents   # 실행용 사본 배치

종료 코드: 0 = 통과, 1 = 불일치·검증 실패, 3 = 입력 오류
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from rdflib import BNode, Graph, Namespace, RDF, RDFS
from ruamel.yaml import YAML

sys.path.insert(0, str(Path(__file__).resolve().parent))
from report_lib import sha256_of  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WF = Namespace("https://example.org/dita-wf#")
PPLAN = Namespace("http://purl.org/net/p-plan#")

START = "<!-- wf:generated:start — 이 구간은 export_agent_definitions.py가 온톨로지에서 생성합니다. 손으로 고치지 마십시오. -->"
END = "<!-- wf:generated:end -->"

PERFORMER_KIND = {WF.Human: "사람", WF.LLMAgent: "LLM", WF.Script: "스크립트"}

yaml = YAML()
yaml.preserve_quotes = True
yaml.width = 4096
yaml.indent(mapping=2, sequence=4, offset=2)


def local(n: Any) -> str:
    return str(n).split("#")[-1]


def label_of(g: Graph, n: Any) -> str:
    for o in g.objects(n, RDFS.label):
        return str(o)
    return local(n)


def agent_name(node_local: str) -> str:
    """agentMapGenerator → map-generator (Claude Code의 name 규칙: 소문자·하이픈)"""
    body = node_local[len("agent"):] if node_local.startswith("agent") else node_local
    return re.sub(r"(?<!^)(?=[A-Z])", "-", body).lower()


# ------------------------------------------------------------------ 고정부 생성

def step_node(g: Graph, number: int):
    for s in g.subjects(PPLAN.isStepOfPlan, WF.DitaDraftWorkflow):
        v = g.value(s, WF.stepNumber)
        if v is not None and int(v) == number:
            return s
    raise KeyError(f"온톨로지에 {number}번 단계가 없습니다")


def generated_block(g: Graph, number: int) -> str:
    s = step_node(g, number)
    lines: list[str] = []
    add = lines.append

    add(f"### 단계 {number}. {label_of(g, s)}")
    add("")
    add("> 이 구간은 워크플로우 온톨로지에서 생성한 것입니다. 바꿔야 할 내용이 있으면")
    add("> `ontology/dita-workflow.ttl`을 고치고 `--write`로 다시 생성하십시오.")
    add("")

    performers = []
    for p in g.objects(s, WF.performedBy):
        kinds = [PERFORMER_KIND[t] for t in g.objects(p, RDF.type) if t in PERFORMER_KIND]
        kind = kinds[0] if kinds else "스크립트"
        # 라벨에 이미 "(LLM)"·"(스크립트)"가 붙어 있으면 떼고 한 번만 표시한다
        name = re.sub(r"\s*\((LLM|스크립트|사람)\)\s*$", "", label_of(g, p))
        performers.append(f"{name} ({kind})")
    add(f"- **수행 주체**: {', '.join(sorted(performers))}")

    prev = [int(g.value(x, WF.stepNumber)) for x in g.objects(s, PPLAN.isPrecededBy)]
    nxt = sorted({int(g.value(t_to, WF.stepNumber))
                  for t in g.subjects(WF.fromStep, s)
                  for t_to in g.objects(t, WF.toStep)
                  if g.value(t_to, WF.stepNumber) is not None})
    add(f"- **앞 단계**: {', '.join(f'{n}번' for n in sorted(prev)) or '없음'}"
        f" · **다음 단계**: {', '.join(f'{n}번' for n in nxt) or '없음'}")
    add(f"- **작업 내용**: {g.value(s, WF.task)}")
    add("")

    add("| 구분 | 자료 |")
    add("|---|---|")
    add(f"| 입력 | {', '.join(sorted(label_of(g, v) for v in g.objects(s, PPLAN.hasInputVar))) or '없음'} |")
    add(f"| 출력 | {', '.join(sorted(label_of(g, v) for v in g.objects(s, PPLAN.hasOutputVar))) or '없음'} |")
    res = []
    for r in g.objects(s, WF.usesResource):
        tid = g.value(r, WF.templateId)
        av = g.value(r, WF.availability)
        res.append(f"{label_of(g, r)}"
                   + (f" ({tid}" if tid else "")
                   + (f", {label_of(g, av)}" if tid and av is not None else (f" ({label_of(g, av)}" if av is not None else ""))
                   + (")" if tid or av is not None else ""))
    add(f"| 사용 리소스 | {', '.join(sorted(res)) or '없음'} |")
    add("")

    cons = []
    for c in g.objects(s, WF.hasConstraint):
        item = f"- **{label_of(g, c)}**"
        proh = g.value(c, WF.prohibits)
        mark = g.value(c, WF.placeholderMarker)
        if proh:
            item += f"\n  - 만들지 않는 것: {proh}"
        if mark:
            item += f"\n  - 대신 넣는 표시: `{mark}`"
        cons.append(item)
    # 독립 지시문 제약이 이미 붙어 있으면 같은 말을 두 번 쓰지 않는다
    has_independence = any(
        (c, RDF.type, WF.IndependenceConstraint) in g for c in g.objects(s, WF.hasConstraint)
    )
    diff_prompt = g.value(s, WF.mustUseDifferentPromptThan)
    if diff_prompt is not None and not has_independence:
        cons.append(f"- **{int(g.value(diff_prompt, WF.stepNumber))}번과 다른 지시문으로 실행** "
                    f"(같은 지시문으로 생성과 검증을 하면 같은 실수를 되풀이함)")
    oos = g.value(s, WF.outOfScopeDelegatedTo)
    if oos is not None:
        cons.append(f"- **범위 밖 판단은 {int(g.value(oos, WF.stepNumber))}번 사람에게 넘긴다** (스스로 판정하지 않음)")
    add("**제약·금지**")
    add("")
    lines.extend(cons or ["- 온톨로지에 적힌 제약 없음"])
    add("")

    sends = g.value(s, WF.sendsToExternalLLM)
    opt = g.value(s, WF.optionalLLMUse)
    add("**외부 LLM API 전송**")
    add("")
    add(f"- 전송 여부: {'있음' if sends is not None and bool(sends.toPython()) else '없음'}"
        + (" (판단형 규칙에만 선택적으로 사용)" if opt is not None and bool(opt.toPython()) else ""))
    tx = []
    for x in g.objects(s, WF.hasExternalTransmission):
        if not isinstance(x, (BNode,)) and x is None:
            continue
        for d in g.objects(x, WF.transmits):
            conds = [label_of(g, c) for c in g.objects(x, WF.conditionalOn)]
            tx.append(f"- {label_of(g, d)} — 조건: {conds[0] if conds else '항상'}")
    lines.extend(sorted(set(tx)) or ["- 전송하는 자료 없음"])
    return "\n".join(lines)


# ------------------------------------------------------------- 파일 읽기·쓰기

FM_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


def split_file(path: Path) -> tuple[dict[str, Any], str, str]:
    text = path.read_text(encoding="utf-8")
    m = FM_RE.match(text)
    if not m:
        raise ValueError(f"{path.name}: frontmatter(--- 로 시작하는 블록)가 없습니다")
    fm = yaml.load(m.group(1)) or {}
    return fm, m.group(0), text[m.end():]


def replace_block(body: str, block: str) -> str:
    if START in body and END in body:
        head, rest = body.split(START, 1)
        _, tail = rest.split(END, 1)
        return f"{head}{START}\n\n{block}\n\n{END}{tail}"
    return f"{body.rstrip()}\n\n{START}\n\n{block}\n\n{END}\n"


def current_block(body: str) -> str | None:
    if START not in body or END not in body:
        return None
    return body.split(START, 1)[1].split(END, 1)[0].strip()


def validate_frontmatter(fm: dict[str, Any]) -> list[str]:
    import jsonschema

    schema = json.loads((ROOT / "schemas" / "agent-definition.schema.json").read_text(encoding="utf-8"))
    cls = jsonschema.validators.validator_for(schema)
    v = cls(schema, format_checker=cls.FORMAT_CHECKER)
    return [f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}" for e in v.iter_errors(fm)]


def check_placeholders(fm: dict[str, Any], body: str) -> list[str]:
    declared = set(fm.get("wf_placeholders") or [])
    used = set(re.findall(r"\{\{[a-z_]+\}\}", body))
    problems = []
    for p in sorted(used - declared):
        problems.append(f"본문에 쓴 치환 표식 {p}이 wf_placeholders에 없습니다")
    for p in sorted(declared - used):
        problems.append(f"wf_placeholders의 {p}을 본문에서 쓰지 않습니다")
    return problems


# ------------------------------------------------------------------------ 실행

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="에이전트 정의 파일의 고정부를 온톨로지에서 생성·검사합니다.")
    ap.add_argument("--agents-dir", type=Path, default=ROOT / "agents")
    ap.add_argument("--ontology", type=Path, default=ROOT / "ontology" / "dita-workflow.ttl")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="생성 구간과 해시를 갱신합니다")
    mode.add_argument("--check", action="store_true", help="온톨로지와 어긋나는지 검사만 합니다")
    mode.add_argument("--deploy", type=Path, help="실행용 사본을 이 폴더에 배치합니다 (.claude/agents)")
    args = ap.parse_args(argv)

    if not args.ontology.is_file():
        print(f"[오류] 온톨로지가 없습니다: {args.ontology}", file=sys.stderr)
        return 3
    files = sorted(args.agents_dir.glob("*.md"))
    if not files:
        print(f"[오류] 에이전트 정의 파일이 없습니다: {args.agents_dir}", file=sys.stderr)
        return 3

    g = Graph()
    g.parse(str(args.ontology), format="turtle")
    onto_hash = sha256_of(args.ontology)

    if args.deploy:
        args.deploy.mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copy2(f, args.deploy / f.name)
        print(f"[배치] {len(files)}개 → {args.deploy}")
        print("  배치본은 사본입니다. 고칠 때는 agents/의 원본을 고치고 다시 배치하십시오.")
        return 0

    problems: list[str] = []
    rows: list[tuple[str, int, str]] = []

    for f in files:
        try:
            fm, fm_text, body = split_file(f)
        except ValueError as exc:
            problems.append(str(exc))
            continue

        # Claude Code가 조용히 건너뛰는 조건을 먼저 본다
        name = fm.get("name", "")
        if not name or str(name).startswith("-") or ":" in str(name):
            problems.append(f"{f.name}: name이 없거나 '-'로 시작하거나 ':'를 포함합니다 → Claude Code가 파일을 건너뜁니다")
        if not fm.get("description"):
            problems.append(f"{f.name}: description이 없습니다 → Claude Code가 파일을 건너뜁니다")

        step = fm.get("wf_step")
        node = fm.get("wf_agent", "")
        if isinstance(step, int) and node:
            expected_name = agent_name(node)
            if name != expected_name:
                problems.append(f"{f.name}: name '{name}'이 wf_agent '{node}'에서 나온 이름 '{expected_name}'과 다릅니다")
            try:
                block = generated_block(g, step)
            except KeyError as exc:
                problems.append(f"{f.name}: {exc}")
                continue
        else:
            problems.append(f"{f.name}: wf_step 또는 wf_agent가 없습니다")
            continue

        for e in validate_frontmatter(fm):
            problems.append(f"{f.name}: frontmatter — {e}")
        for e in check_placeholders(fm, body):
            problems.append(f"{f.name}: {e}")

        now = current_block(body)
        stale_hash = fm.get("wf_ontology_sha256") != onto_hash
        if args.write:
            fm["wf_ontology_sha256"] = onto_hash
            import io
            buf = io.StringIO()
            yaml.dump(fm, buf)
            f.write_text("---\n" + buf.getvalue() + "---\n" + replace_block(body, block), encoding="utf-8")
            state = "갱신" if (now != block or stale_hash) else "변경 없음"
        else:
            if now is None:
                problems.append(f"{f.name}: 생성 구간 표식이 없습니다 (--write로 넣으십시오)")
                state = "표식 없음"
            elif now != block:
                d = "\n".join(difflib.unified_diff(now.splitlines(), block.splitlines(),
                                                   fromfile=f"{f.name} (현재)", tofile="온톨로지 (기대)", lineterm=""))
                problems.append(f"{f.name}: 생성 구간이 온톨로지와 다릅니다\n{d}")
                state = "불일치"
            elif stale_hash:
                problems.append(f"{f.name}: wf_ontology_sha256이 지금 온톨로지와 다릅니다 (--write로 갱신)")
                state = "해시 불일치"
            else:
                state = "일치"
        rows.append((name, step, state))

    print("| 에이전트 | 단계 | 상태 |")
    print("|---|---|---|")
    for name, step, state in sorted(rows, key=lambda r: r[1]):
        print(f"| {name} | {step}번 | {state} |")

    if problems:
        print(f"\n[문제 {len(problems)}건]", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print(f"\n{len(rows)}개 정의 파일 확인 — 문제 없음 (온톨로지 {onto_hash[:12]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
