"""The language evaluation set runs in CI on the mock: detection and routing per language."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chatfront.core.languages import live_pass_rates, load_registry
from chatfront.eval.languages import evaluate, load_cases, main, write_results


def test_every_tier_one_language_has_ten_text_conversations_and_voice_notes() -> None:
    cases = load_cases()
    tier_one = [language.tag for language in load_registry().by_tier(1)]
    assert sorted(cases) == sorted(tier_one)
    for tag, conversations in cases.items():
        text = [case for case in conversations if not case.is_voice]
        voice = [case for case in conversations if case.is_voice]
        assert len(text) >= 10, tag
        assert len(voice) >= 2, tag
        assert len({case.id for case in conversations}) == len(conversations)


async def test_mock_run_passes_the_mark_in_every_language() -> None:
    results = await evaluate(live=False)
    mark = load_registry().pass_mark
    for tag, summary in results.items():
        assert summary.total >= 10, tag
        assert summary.pass_rate >= mark, (tag, summary.failures)
        assert summary.pending == 2  # recordings not made yet


async def test_results_are_written_but_mock_runs_never_mark_a_language_supported(
    tmp_path: Path,
) -> None:
    target = tmp_path / "eval-results.json"
    target.write_text(json.dumps({"runs": {}}), encoding="utf-8")
    results = await evaluate(live=False, only=["ps"])
    write_results(results, live=False, path=target)
    stored = json.loads(target.read_text(encoding="utf-8"))
    assert stored["runs"]["ps"]["mock"]["total"] == 10
    assert live_pass_rates(stored) == {}


def test_live_run_needs_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BASETEN_API_KEY", "")
    monkeypatch.setenv("FIREWORKS_API_KEY", "")
    with pytest.raises(SystemExit, match="needs"):
        main(["--live", "--lang", "ps"])


def test_command_line_report(capsys: pytest.CaptureFixture[str]) -> None:
    main(["--lang", "sd"])
    report = capsys.readouterr().out
    assert report.splitlines()[1].startswith("sd")
    assert "10/10" in report
