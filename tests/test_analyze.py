import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from seedradar import analyze, cli, seeds

from tests.test_seeds import make_entry, make_paper


def _body(entry):
    return {k: v for k, v in entry.items() if k not in ("arxiv_id", "version")}


def _response(text, stop_reason="end_turn"):
    return SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)])


class FakeClient:
    """Stands in for anthropic.Anthropic: returns queued responses and records each request."""

    def __init__(self, *responses):
        self.queue = list(responses)
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **params):
        self.calls.append(params)
        return self.queue.pop(0)


def paper(arxiv_id="2601.00001"):
    p = make_paper(arxiv_id)
    p["title"] = f"Paper {arxiv_id}"
    return p


class AnalyzePaperTest(unittest.TestCase):
    def test_valid_answer(self):
        client = FakeClient(_response(json.dumps(_body(make_entry()))))
        entry = analyze.analyze_paper(client, paper())
        self.assertEqual(entry["arxiv_id"], "2601.00001")
        self.assertEqual(entry["version"], 1)
        self.assertEqual(seeds.validate_entry(entry), [])

    def test_request_shape(self):
        client = FakeClient(_response(json.dumps(_body(make_entry()))))
        analyze.analyze_paper(client, paper(), model="claude-opus-5-5", effort="medium")
        call = client.calls[0]
        self.assertEqual(call["model"], "claude-opus-5-5")
        self.assertEqual(call["fallbacks"], "default")
        self.assertEqual(call["betas"], [analyze.FALLBACK_BETA])
        self.assertEqual(call["output_config"]["effort"], "medium")
        self.assertEqual(call["output_config"]["format"]["type"], "json_schema")
        self.assertIn("Paper 2601.00001", call["messages"][0]["content"])
        self.assertIn("無理に種を作らない", call["system"])
        self.assertNotIn("thinking", call)

    def test_without_fallbacks(self):
        client = FakeClient(_response(json.dumps(_body(make_entry()))))
        analyze.analyze_paper(client, paper(), use_fallbacks=False)
        self.assertNotIn("fallbacks", client.calls[0])

    def test_invalid_answer_is_retried_once(self):
        bad = _body(make_entry())
        bad["business_seeds"] = [bad["business_seeds"][0]] * 4
        client = FakeClient(_response(json.dumps(bad)), _response(json.dumps(_body(make_entry()))))
        analyze.analyze_paper(client, paper())
        self.assertEqual(len(client.calls), 2)

    def test_gives_up_after_two_bad_answers(self):
        client = FakeClient(_response("not json"), _response("", stop_reason="max_tokens"))
        with self.assertRaisesRegex(analyze.AnalysisError, "max_tokens"):
            analyze.analyze_paper(client, paper())

    def test_refusal_is_not_retried(self):
        client = FakeClient(_response("", stop_reason="refusal"))
        with self.assertRaisesRegex(analyze.AnalysisError, "declined"):
            analyze.analyze_paper(client, paper())
        self.assertEqual(len(client.calls), 1)

    def test_no_seed_means_all_scores_one(self):
        body = _body(make_entry(with_seed=False))
        client = FakeClient(_response(json.dumps(body)))
        entry = analyze.analyze_paper(client, paper())
        self.assertEqual(set(entry["scores"].values()), {1})

    def test_schema_matches_validator(self):
        schema = analyze.OUTPUT_SCHEMA
        self.assertEqual(set(schema["required"]) | {"arxiv_id", "version"},
                         set(make_entry().keys()))
        seed_schema = schema["properties"]["business_seeds"]["items"]
        self.assertEqual(set(seed_schema["required"]), set(seeds.SEED_TEXT_KEYS) | {"time_to_market"})
        self.assertEqual(seed_schema["properties"]["time_to_market"]["enum"], list(seeds.TIME_TO_MARKET))
        self.assertNotIn("minimum", json.dumps(schema))


class AnalyzePapersTest(unittest.TestCase):
    def test_failed_papers_are_left_out(self):
        client = FakeClient(
            _response(json.dumps(_body(make_entry()))),
            _response("", stop_reason="refusal"),
        )
        doc, failures = analyze.analyze_papers([paper("a"), paper("b")], "2026-01-02",
                                               client=client, log=lambda *a: None)
        self.assertEqual([e["arxiv_id"] for e in doc["papers"]], ["a"])
        self.assertEqual(len(failures), 1)
        self.assertTrue(failures[0].startswith("b:"))
        self.assertEqual(doc["analyzer"], analyze.ANALYZER_NAME)


class DailyCommandTest(unittest.TestCase):
    def run_daily(self, tmp, fetched, analysis_result, extra=()):
        tmp = Path(tmp)
        out = tmp / "github_output"
        env = {"GITHUB_OUTPUT": str(out)}
        args = ["daily", "--papers", str(tmp / "papers.json"), "--seeds", str(tmp / "seeds.json"),
                "--analysis-dir", str(tmp / "analysis"), "--summary", str(tmp / "summary.md"), *extra]
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(cli, "fetch_all", return_value=fetched), \
                mock.patch.object(analyze, "analyze_papers", return_value=analysis_result) as run, \
                mock.patch("builtins.print"):
            code = cli.main(args)
        return code, out.read_text(encoding="utf-8"), run

    def fetched(self):
        from seedradar.arxiv import Paper
        return [Paper(arxiv_id="2601.00001", version=1, title="Paper", summary="abstract", authors=["A"],
                      primary_category="cs.AI", categories=["cs.AI"], published="2026-01-01T00:00:00Z",
                      updated="2026-01-01T00:00:00Z", abs_url="https://arxiv.org/abs/2601.00001",
                      pdf_url="https://arxiv.org/pdf/2601.00001", fields=["ai"])]

    def test_new_paper_is_analyzed_and_merged(self):
        analysis = {"analyzed_at": "2026-01-02", "analyzer": "test", "papers": [make_entry()]}
        with tempfile.TemporaryDirectory() as tmp:
            code, output, run = self.run_daily(tmp, self.fetched(), (analysis, []))
            self.assertEqual(code, 0)
            self.assertIn("changed=true", output)
            merged = json.loads((Path(tmp) / "seeds.json").read_text(encoding="utf-8"))
            self.assertEqual(merged["count"], 1)
            self.assertEqual(len(list((Path(tmp) / "analysis").glob("*.json"))), 1)
            summary = (Path(tmp) / "summary.md").read_text(encoding="utf-8")
            self.assertIn("Analyzed: 1", summary)

    def test_nothing_new_changes_nothing(self):
        analysis = {"analyzed_at": "2026-01-02", "analyzer": "test", "papers": [make_entry()]}
        with tempfile.TemporaryDirectory() as tmp:
            self.run_daily(tmp, self.fetched(), (analysis, []))
            before = (Path(tmp) / "seeds.json").read_text(encoding="utf-8")
            code, output, run = self.run_daily(tmp, self.fetched(), (analysis, []))
            self.assertEqual(code, 0)
            self.assertTrue(output.strip().endswith("changed=false"))
            run.assert_not_called()
            self.assertEqual((Path(tmp) / "seeds.json").read_text(encoding="utf-8"), before)

    def test_all_failures_return_an_error(self):
        empty = {"analyzed_at": "2026-01-02", "analyzer": "test", "papers": []}
        with tempfile.TemporaryDirectory() as tmp:
            code, output, run = self.run_daily(tmp, self.fetched(), (empty, ["2601.00001: refused"]))
            self.assertEqual(code, 1)
            self.assertNotIn("changed=true", output)
            self.assertFalse((Path(tmp) / "seeds.json").exists())

    def test_fetch_failure_returns_an_error(self):
        from seedradar.arxiv import ArxivUnavailable
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(Path(tmp) / "o")}), \
                mock.patch.object(cli, "fetch_all", side_effect=ArxivUnavailable("429")), \
                mock.patch("builtins.print"):
            self.assertEqual(cli.main(["daily", "--papers", str(Path(tmp) / "p.json")]), 1)

    def test_second_run_on_the_same_day_gets_its_own_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "2026-01-02.json").write_text("{}")
            self.assertEqual(cli._analysis_path(d, "2026-01-02").name, "2026-01-02-2.json")


class WorkflowFileTest(unittest.TestCase):
    """Cheap checks on the workflow, since it cannot run in tests."""

    def setUp(self):
        root = Path(__file__).resolve().parent.parent
        self.text = (root / ".github" / "workflows" / "daily-radar.yml").read_text(encoding="utf-8")

    def test_uses_secret_and_runs_tests(self):
        self.assertIn("secrets.ANTHROPIC_API_KEY", self.text)
        self.assertIn("python -m seedradar daily", self.text)
        self.assertGreaterEqual(self.text.count("python -m unittest"), 2)

    def test_runs_on_weekday_schedule_and_by_hand(self):
        self.assertIn("schedule:", self.text)
        self.assertIn("workflow_dispatch:", self.text)
        self.assertIn("1-5", self.text)

    def test_only_data_is_committed(self):
        self.assertIn("git add data/", self.text)
        self.assertNotIn("git add -A", self.text)
        self.assertNotIn("git add .", self.text)


if __name__ == "__main__":
    unittest.main()
