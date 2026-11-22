import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from seedradar import cli, seeds

ROOT = Path(__file__).resolve().parent.parent


def bilingual(text="x"):
    return {"en": text, "ja": text}


def make_seed():
    return {
        "title": bilingual(), "customer": bilingual(), "market": bilingual(),
        "why_now": bilingual(), "moat": bilingual(), "time_to_market": "1-3y",
    }


def make_entry(arxiv_id="2601.00001", version=1, with_seed=True):
    return {
        "arxiv_id": arxiv_id,
        "version": version,
        "technology": bilingual(),
        "breakthrough": bilingual(),
        "business_seeds": [make_seed()] if with_seed else [],
        "scores": {"market_size": 3, "feasibility": 4, "startup_fit": 2},
        "confidence": "medium",
        "caveats": bilingual(),
    }


def make_analysis(*entries):
    return {"analyzed_at": "2026-01-02", "analyzer": "test", "papers": list(entries) or [make_entry()]}


def make_paper(arxiv_id="2601.00001", version=1, published="2026-01-01T09:00:00Z"):
    return {
        "arxiv_id": arxiv_id, "version": version, "title": "T", "summary": "abstract",
        "authors": ["A"], "primary_category": "cs.AI", "categories": ["cs.AI"],
        "published": published, "updated": published,
        "abs_url": f"https://arxiv.org/abs/{arxiv_id}", "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
        "fields": ["ai"],
    }


class ValidateTest(unittest.TestCase):
    def test_valid_analysis(self):
        self.assertEqual(seeds.validate_analysis(make_analysis()), [])

    def test_paper_without_seed_is_valid(self):
        self.assertEqual(seeds.validate_analysis(make_analysis(make_entry(with_seed=False))), [])

    def assertInvalid(self, mutate, expected):
        entry = make_entry()
        mutate(entry)
        errors = seeds.validate_analysis(make_analysis(entry))
        self.assertTrue(any(expected in e for e in errors), errors)

    def test_missing_japanese(self):
        self.assertInvalid(lambda e: e["technology"].pop("ja"), "technology.ja")

    def test_empty_text(self):
        self.assertInvalid(lambda e: e["caveats"].update(en="  "), "caveats.en")

    def test_score_out_of_range(self):
        self.assertInvalid(lambda e: e["scores"].update(feasibility=6), "scores.feasibility")

    def test_score_must_be_integer(self):
        self.assertInvalid(lambda e: e["scores"].update(market_size=True), "scores.market_size")

    def test_bad_time_to_market(self):
        self.assertInvalid(lambda e: e["business_seeds"][0].update(time_to_market="soon"), "time_to_market")

    def test_bad_confidence(self):
        self.assertInvalid(lambda e: e.update(confidence="sure"), "confidence")

    def test_too_many_seeds(self):
        self.assertInvalid(lambda e: e.update(business_seeds=[make_seed()] * 4), "at most")

    def test_seed_missing_field(self):
        self.assertInvalid(lambda e: e["business_seeds"][0].pop("moat"), "moat")

    def test_duplicate_ids(self):
        errors = seeds.validate_analysis(make_analysis(make_entry(), make_entry()))
        self.assertTrue(any("duplicate" in e for e in errors), errors)

    def test_empty_papers(self):
        doc = make_analysis()
        doc["papers"] = []
        self.assertTrue(seeds.validate_analysis(doc))


class MergeTest(unittest.TestCase):
    def test_merge_builds_public_entries_without_abstract(self):
        papers = {"papers": [make_paper()]}
        result = seeds.merge(papers, make_analysis())
        self.assertEqual(result["count"], 1)
        entry = result["papers"][0]
        self.assertNotIn("summary", entry)
        self.assertEqual(entry["title"], "T")
        self.assertEqual(entry["scores"], {"market_size": 3, "feasibility": 4, "startup_fit": 2})
        self.assertEqual(entry["analyzed_at"], "2026-01-02")
        self.assertIn("ai", result["fields"])

    def test_merge_keeps_old_papers_and_sorts_newest_first(self):
        old = seeds.merge({"papers": [make_paper("2601.00001")]}, make_analysis(make_entry("2601.00001")))
        papers = {"papers": [make_paper("2601.00002", published="2026-01-05T00:00:00Z")]}
        result = seeds.merge(papers, make_analysis(make_entry("2601.00002")), old)
        self.assertEqual([p["arxiv_id"] for p in result["papers"]], ["2601.00002", "2601.00001"])

    def test_new_analysis_replaces_old_one(self):
        papers = {"papers": [make_paper()]}
        old = seeds.merge(papers, make_analysis())
        updated = make_entry()
        updated["confidence"] = "high"
        result = seeds.merge(papers, make_analysis(updated), old)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["papers"][0]["confidence"], "high")

    def test_unknown_paper_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "not in papers.json"):
            seeds.merge({"papers": []}, make_analysis())

    def test_version_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "v1 but papers.json has v2"):
            seeds.merge({"papers": [make_paper(version=2)]}, make_analysis())

    def test_invalid_analysis_is_rejected(self):
        entry = make_entry()
        entry["scores"]["feasibility"] = 0
        with self.assertRaisesRegex(ValueError, "invalid analysis"):
            seeds.merge({"papers": [make_paper()]}, make_analysis(entry))


class RemoveTest(unittest.TestCase):
    def setUp(self):
        papers = {"papers": [make_paper("a"), make_paper("b", published="2026-01-03T00:00:00Z")]}
        self.doc = seeds.merge(papers, make_analysis(make_entry("a"), make_entry("b")))

    def test_removes_papers(self):
        result = seeds.remove(self.doc, ["a"])
        self.assertEqual([p["arxiv_id"] for p in result["papers"]], ["b"])
        self.assertEqual(result["count"], 1)

    def test_unknown_id_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "not in seeds.json: zzz"):
            seeds.remove(self.doc, ["a", "zzz"])

    def test_cli_remove(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "seeds.json"
            path.write_text(json.dumps(self.doc), encoding="utf-8")
            with mock.patch("builtins.print"):
                self.assertEqual(cli.main(["remove", "b", "--seeds", str(path)]), 0)
                self.assertEqual(cli.main(["remove", "nope", "--seeds", str(path)]), 1)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["count"], 1)


class PendingTest(unittest.TestCase):
    def test_lists_unanalyzed_and_new_versions(self):
        papers = {"papers": [make_paper("a", 1), make_paper("b", 2), make_paper("c", 1)]}
        done = {"papers": [{"arxiv_id": "a", "version": 1}, {"arxiv_id": "b", "version": 1}]}
        self.assertEqual([p["arxiv_id"] for p in seeds.pending_papers(papers, done)], ["b", "c"])
        self.assertEqual(len(seeds.pending_papers(papers, None)), 3)


class CliSeedsTest(unittest.TestCase):
    def test_validate_and_merge_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "papers.json").write_text(json.dumps({"papers": [make_paper()]}), encoding="utf-8")
            (tmp / "analysis.json").write_text(json.dumps(make_analysis()), encoding="utf-8")
            with mock.patch("builtins.print"):
                self.assertEqual(cli.main(["validate", str(tmp / "analysis.json")]), 0)
                code = cli.main(["merge", str(tmp / "analysis.json"),
                                 "--papers", str(tmp / "papers.json"), "--seeds", str(tmp / "seeds.json")])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads((tmp / "seeds.json").read_text(encoding="utf-8"))["count"], 1)

    def test_validate_reports_errors(self):
        bad = make_analysis()
        bad["papers"][0]["confidence"] = "sure"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "analysis.json"
            path.write_text(json.dumps(bad), encoding="utf-8")
            with mock.patch("builtins.print"):
                self.assertEqual(cli.main(["validate", str(path)]), 1)


class PublishedDataTest(unittest.TestCase):
    """The data files the public page reads must stay valid."""

    def test_every_committed_analysis_is_valid(self):
        files = sorted((ROOT / "data" / "analysis").glob("*.json"))
        self.assertTrue(files)
        for path in files:
            with self.subTest(path=path.name):
                self.assertEqual(seeds.validate_analysis(json.loads(path.read_text(encoding="utf-8"))), [])

    def test_public_seeds_file_matches_schema(self):
        doc = json.loads((ROOT / "data" / "seeds.json").read_text(encoding="utf-8"))
        self.assertEqual(doc["count"], len(doc["papers"]))
        ids = [p["arxiv_id"] for p in doc["papers"]]
        self.assertEqual(len(ids), len(set(ids)))
        for paper in doc["papers"]:
            with self.subTest(arxiv_id=paper["arxiv_id"]):
                self.assertEqual(seeds.validate_entry(copy.deepcopy(paper)), [])
                self.assertTrue(set(paper["fields"]) <= set(doc["fields"]))
                self.assertNotIn("summary", paper)
                if not paper["business_seeds"]:
                    self.assertEqual(set(paper["scores"].values()), {1})


    def test_removed_space_papers_stay_out(self):
        # Basic-astronomy papers that the old space query picked up; removed on 2026-11-22.
        doc = json.loads((ROOT / "data" / "seeds.json").read_text(encoding="utf-8"))
        ids = {p["arxiv_id"] for p in doc["papers"]}
        self.assertFalse(ids & {"2611.20754", "2611.20654", "2611.20636", "2611.20617"})


class PageTest(unittest.TestCase):
    """The Daily Alert sign-up must be the first thing on the page."""

    def test_daily_alert_is_above_the_header(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        body = html[html.index("<body>"):]
        self.assertLess(body.index('class="alert-bar"'), body.index("<header>"))
        self.assertGreaterEqual(body.count("cta-link"), 2)
        self.assertIn("WAITLIST_ISSUE_URL", html)


if __name__ == "__main__":
    unittest.main()
