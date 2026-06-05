from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ykm.contracts import QueryRequest
from ykm.index import YkmIndex


DEFAULT_TOP_KS = [1, 3, 5]


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    query: str
    type: str | None = None
    tags: list[str] = Field(default_factory=list)
    tags_any: list[str] = Field(default_factory=list)
    source: str | None = None
    expected_sources: list[str] = Field(default_factory=list)
    expected_paths: list[str] = Field(default_factory=list)
    expected_sections: list[str] = Field(default_factory=list)
    match: Literal["any", "all"] = "any"
    absent_sources: list[str] = Field(default_factory=list)
    absent_paths: list[str] = Field(default_factory=list)
    limit: int = Field(default=5, ge=1, le=10)


class EvalSuite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cases: list[EvalCase]


class EvalCaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    query: str
    passed: bool
    hit_at: dict[str, bool]
    absent_ok: bool
    expected_ranks: dict[str, int | None]
    result_sources: list[str]
    result_paths: list[str]


class EvalSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_count: int
    passed_count: int
    hit_at: dict[str, int]
    cases: list[EvalCaseResult]

    @property
    def passed(self) -> bool:
        return self.passed_count == self.case_count


def load_eval_suite(path: Path) -> EvalSuite:
    return EvalSuite.model_validate(json.loads(path.read_text(encoding="utf-8")))


def run_eval(index: YkmIndex, suite: EvalSuite, top_ks: list[int] | None = None) -> EvalSummary:
    top_ks = top_ks or DEFAULT_TOP_KS
    case_results = [run_eval_case(index, case, top_ks) for case in suite.cases]
    return EvalSummary(
        case_count=len(case_results),
        passed_count=sum(1 for result in case_results if result.passed),
        hit_at={
            f"top_{top_k}": sum(1 for result in case_results if result.hit_at[f"top_{top_k}"])
            for top_k in top_ks
        },
        cases=case_results,
    )


def run_eval_case(index: YkmIndex, case: EvalCase, top_ks: list[int]) -> EvalCaseResult:
    response = index.query(
        QueryRequest(
            query=case.query,
            type=case.type,
            tags=case.tags,
            tags_any=case.tags_any,
            source=case.source,
            limit=max(case.limit, max(top_ks)),
        )
    )
    results = response.results
    result_sources = [result.source_id for result in results]
    result_paths = [result.source_path for result in results]
    result_sections = [result.section_id for result in results]

    expectations = {
        **{f"source:{source_id}": _rank(source_id, result_sources) for source_id in case.expected_sources},
        **{f"path:{path}": _rank(path, result_paths) for path in case.expected_paths},
        **{
            f"section:{section_id}": _rank(section_id, result_sections)
            for section_id in case.expected_sections
        },
    }
    hit_at = {
        f"top_{top_k}": _matches(expectations, top_k, case.match) for top_k in top_ks
    }
    absent_ok = not set(case.absent_sources).intersection(result_sources) and not set(
        case.absent_paths
    ).intersection(result_paths)
    passed = hit_at[f"top_{max(top_ks)}"] and absent_ok
    return EvalCaseResult(
        name=case.name,
        query=case.query,
        passed=passed,
        hit_at=hit_at,
        absent_ok=absent_ok,
        expected_ranks=expectations,
        result_sources=result_sources,
        result_paths=result_paths,
    )


def _rank(expected: str, results: list[str]) -> int | None:
    try:
        return results.index(expected) + 1
    except ValueError:
        return None


def _matches(expectations: dict[str, int | None], top_k: int, match: str) -> bool:
    if not expectations:
        return True
    hits = [rank is not None and rank <= top_k for rank in expectations.values()]
    if match == "all":
        return all(hits)
    return any(hits)
