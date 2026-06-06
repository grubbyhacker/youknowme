from __future__ import annotations

from pathlib import Path

from ykm.build import build_index
from ykm.cli import main
from ykm.eval import EvalCase, EvalSuite, load_eval_suite, run_eval
from ykm.embeddings import FakeEmbeddingProvider
from ykm.index import YkmIndex


def built_index(tmp_path: Path) -> YkmIndex:
    out = tmp_path / "index"
    provider = FakeEmbeddingProvider()
    build_index(Path("fixtures/corpus"), out, provider)
    return YkmIndex(out, provider)


def test_synthetic_eval_cases_pass(tmp_path: Path) -> None:
    index = built_index(tmp_path)
    suite = load_eval_suite(Path("fixtures/eval/synthetic.json"))

    summary = run_eval(index, suite)

    assert summary.passed is True
    assert summary.passed_count == summary.case_count
    assert summary.hit_at["top_5"] == summary.case_count


def test_eval_reports_missing_expected_source(tmp_path: Path) -> None:
    index = built_index(tmp_path)
    suite = EvalSuite(
        cases=[
            EvalCase(
                name="missing",
                query="weekly spa maintenance",
                expected_sources=["not-a-source"],
            )
        ]
    )

    summary = run_eval(index, suite)

    assert summary.passed is False
    assert summary.cases[0].expected_ranks["source:not-a-source"] is None


def test_eval_reports_absent_source_violation(tmp_path: Path) -> None:
    index = built_index(tmp_path)
    suite = EvalSuite(
        cases=[
            EvalCase(
                name="absent",
                query="weekly spa maintenance",
                tags=["spa"],
                expected_sources=["spa-home"],
                absent_sources=["spa-cabin"],
                limit=5,
            )
        ]
    )

    summary = run_eval(index, suite)

    assert summary.passed is False
    assert summary.cases[0].absent_ok is False


def test_eval_case_can_require_top_one(tmp_path: Path) -> None:
    index = built_index(tmp_path)
    suite = EvalSuite(
        cases=[
            EvalCase(
                name="top-one",
                query="weekly spa maintenance",
                expected_sources=["spa-home"],
                pass_at=1,
            )
        ]
    )

    summary = run_eval(index, suite)

    assert summary.passed is False
    assert summary.cases[0].hit_at["top_3"] is True
    assert summary.cases[0].expected_ranks["source:spa-home"] == 2


def test_eval_cli_writes_summary_outfile(tmp_path: Path, monkeypatch, capsys) -> None:
    index_dir = tmp_path / "index"
    out_path = tmp_path / "results" / "eval.json"
    build_index(Path("fixtures/corpus"), index_dir, FakeEmbeddingProvider())
    monkeypatch.setattr(
        "sys.argv",
        [
            "ykm",
            "eval",
            "--index",
            str(index_dir),
            "--cases",
            "fixtures/eval/synthetic.json",
            "--out",
            str(out_path),
        ],
    )

    main()

    assert out_path.exists()
    summary = out_path.read_text(encoding="utf-8")
    assert '"passed_count": 6' in summary
    assert '"case_count": 6' in summary
    assert capsys.readouterr().out == summary
