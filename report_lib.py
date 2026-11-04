"""검증 리포트 공통 기록 모듈 (T32).

확정 워크플로우 v3의 검증 단계(4, 10, 11, 12, 13, 14, 15)가 모두 이 모듈로 리포트를
만든다. 단계마다 리포트 작성 코드를 따로 두면 형식이 갈라지고 16번에서 한 번에 검토할
수 없으므로, 형식 결정과 스키마 검증을 여기에 모은다.

사용 예 (11번 XML 정형성 검증):

    from report_lib import Report
    rep = Report(step=11, step_label="XML 정형성 검증", run_id=run_id, iteration=0,
                 checker={"kind": "script", "name": "lxml", "version": "5.2.1"},
                 target_kind="topicset", files=topic_files)
    for f in topic_files:
        try:
            etree.parse(f)
        except etree.XMLSyntaxError as e:
            rep.add("error", "XML-WF", "xml", str(e.msg),
                    {"file": f, "line": e.lineno, "column": e.offset})
    rep.save(out_dir)

의존: jsonschema >= 4 (JSON Schema 2020-12). 표준 라이브러리만으로는 검증하지 않는다.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = "1"
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "report.schema.json"

# 외부 LLM으로 넘기는 사본에서 반드시 지우는 필드 (보안 설계: 원고·로그 원문 전송 금지)
MASK_DROP_FIELDS = ("evidence.quote",)


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_run_id() -> str:
    """실행 식별자. T39 실행 기록과 같은 값을 쓴다."""
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


def revision_of(paths: Iterable[str | os.PathLike]) -> str:
    """대상 파일 집합의 sha256 앞 12자리. 경로 순서가 달라도 같은 값이 나오도록 정렬한다."""
    h = hashlib.sha256()
    for p in sorted(str(x).replace("\\", "/") for x in paths):
        h.update(p.encode("utf-8"))
        h.update(b"\x00")
        data = Path(p)
        h.update(data.read_bytes() if data.is_file() else b"")
        h.update(b"\x00")
    return h.hexdigest()[:12]


def sha256_of(path: str | os.PathLike) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Report:
    """T32 형식 리포트를 만든다."""

    def __init__(
        self,
        step: int,
        step_label: str,
        run_id: str,
        iteration: int = 0,
        *,
        checker: dict[str, Any],
        target_kind: str,
        files: Iterable[str | os.PathLike] = (),
        base_revision: str | None = None,
        direct_fix: bool = False,
    ) -> None:
        self.step = int(step)
        self.step_label = step_label
        self.run_id = run_id
        self.iteration = int(iteration)
        self.direct_fix = direct_fix
        self.checker = dict(checker)
        self.started_at = utc_now()
        self.files = [str(f).replace("\\", "/") for f in files]
        self.target = {
            "kind": target_kind,
            "files": self.files,
            "base_revision": base_revision or revision_of(self.files),
        }
        self.findings: list[dict[str, Any]] = []
        self.skipped_items: list[dict[str, Any]] = []
        self.checked = 0
        self.notes: str | None = None
        self._tool_error = False

    # ---------------- 기록 ----------------

    def add(
        self,
        severity: str,
        rule_id: str,
        rule_source: str,
        message: str,
        location: dict[str, Any],
        *,
        evidence: dict[str, Any] | None = None,
        suggested_action: str | None = None,
        auto_fixable: bool = False,
    ) -> dict[str, Any]:
        if severity not in ("error", "warning", "info"):
            raise ValueError(f"severity 값이 잘못되었습니다: {severity}")
        location = {k: v for k, v in location.items() if v is not None}
        if not location:
            raise ValueError("location은 최소 한 항목이 있어야 합니다 (T32 스키마 minProperties 1)")
        item: dict[str, Any] = {
            "finding_id": f"f{len(self.findings) + 1:04d}",
            "severity": severity,
            "rule_id": rule_id,
            "rule_source": rule_source,
            "message": message,
            "location": location,
            "auto_fixable": bool(auto_fixable),
            "accepted": False,
            "fix_status": "open",
        }
        if evidence:
            item["evidence"] = {k: v for k, v in evidence.items() if v is not None}
        if suggested_action:
            item["suggested_action"] = suggested_action
        self.findings.append(item)
        return item

    def add_skipped(self, item: str, reason: str, triggered_by_step: int, scope: str | None = None) -> None:
        rec = {"item": item, "reason": reason, "triggered_by_step": int(triggered_by_step)}
        if scope:
            rec["scope"] = scope
        self.skipped_items.append(rec)

    def tool_error(self, message: str) -> None:
        """검사 자체가 실패한 경우 (status = error). 통과로 집계되지 않게 한다."""
        self._tool_error = True
        self.add("error", "TOOL", "project", message, {"step": self.step})

    # ---------------- 집계·저장 ----------------

    @property
    def summary(self) -> dict[str, int]:
        err = sum(1 for f in self.findings if f["severity"] == "error" and not f.get("accepted"))
        warn = sum(1 for f in self.findings if f["severity"] == "warning" and not f.get("accepted"))
        info = sum(1 for f in self.findings if f["severity"] == "info")
        accepted = sum(1 for f in self.findings if f.get("accepted"))
        return {
            "error": err,
            "warning": warn,
            "info": info,
            "accepted_warning": accepted,
            "checked": self.checked,
            "skipped": len(self.skipped_items),
        }

    @property
    def status(self) -> str:
        if self._tool_error:
            return "error"
        return "fail" if self.summary["error"] else "pass"

    def to_dict(self) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "report_id": f"report-{self.step:02d}-i{self.iteration}",
            "run_id": self.run_id,
            "step": self.step,
            "step_label": self.step_label,
            "iteration": self.iteration,
            "direct_fix": self.direct_fix,
            "status": self.status,
            "checker": self.checker,
            "started_at": self.started_at,
            "finished_at": utc_now(),
            "target": self.target,
            "summary": self.summary,
            "findings": self.findings,
        }
        if self.skipped_items:
            doc["skipped_items"] = self.skipped_items
        if self.notes:
            doc["notes"] = self.notes
        return doc

    def save(self, out_dir: str | os.PathLike, validate: bool = True) -> Path:
        doc = self.to_dict()
        if validate:
            validate_report(doc)
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{doc['report_id']}.json"
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path


def validate_report(doc: dict[str, Any], schema_path: str | os.PathLike = SCHEMA_PATH) -> None:
    """T32 스키마로 검증한다. 받는 쪽에서도 다시 검증하는 것이 원칙이므로 공개 함수로 둔다."""
    import jsonschema  # 지연 import: 리포트를 읽기만 하는 코드에 의존을 강제하지 않는다

    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    validator_cls(schema, format_checker=validator_cls.FORMAT_CHECKER).validate(doc)


def mask_for_llm(doc: dict[str, Any], map_path: str | os.PathLike, blocked_terms: Iterable[str] = ()) -> dict[str, Any]:
    """LLM에 넘길 사본을 만든다.

    - 파일 경로를 가명(F001, F002 …)으로 바꾸고 대응표는 map_path에 로컬 저장한다.
    - evidence.quote를 지운다 (원고 본문 외부 전송 금지).
    - 차단어가 남아 있으면 blocked_term_hit을 true로 두고 전송을 막는다 (fail-closed).

    반환한 사본에 masking.blocked_term_hit이 true면 호출한 쪽은 전송하지 않아야 한다.
    """
    import copy
    import re

    out = copy.deepcopy(doc)
    names: dict[str, str] = {}

    def alias(path: str) -> str:
        if path not in names:
            names[path] = f"F{len(names) + 1:03d}"
        return names[path]

    out["target"]["files"] = [alias(f) for f in out["target"].get("files", [])]
    for f in out.get("findings", []):
        if "file" in f["location"]:
            f["location"]["file"] = alias(f["location"]["file"])
        ev = f.get("evidence")
        if ev:
            ev.pop("quote", None)
            if not ev:
                f.pop("evidence")
    for s in out.get("skipped_items", []):
        s["item"] = alias(s["item"])

    Path(map_path).write_text(
        json.dumps({v: k for k, v in names.items()}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    blob = json.dumps(out, ensure_ascii=False)
    hit = any(re.search(re.escape(t), blob, re.IGNORECASE) for t in blocked_terms if t)
    out["masking"] = {
        "applied": True,
        "blocked_term_hit": bool(hit),
        "fields_removed": list(MASK_DROP_FIELDS),
    }
    return out
