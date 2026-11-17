import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

from seedradar import arxiv, cli

FIXTURE = (Path(__file__).parent / "fixtures" / "feed.xml").read_bytes()

ERROR_FEED = b"""<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/api/errors#bad_query</id>
    <title>Error</title>
    <summary>incorrect id format</summary>
  </entry>
</feed>"""


class BuildQueryUrlTest(unittest.TestCase):
    def test_joins_categories_and_sorts_by_newest(self):
        url = arxiv.build_query_url(("cs.AI", "cs.LG"), 5)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.assertEqual(query["search_query"], ["cat:cs.AI OR cat:cs.LG"])
        self.assertEqual(query["sortBy"], ["submittedDate"])
        self.assertEqual(query["sortOrder"], ["descending"])
        self.assertEqual(query["max_results"], ["5"])

    def test_rejects_bad_arguments(self):
        with self.assertRaises(ValueError):
            arxiv.build_query_url((), 5)
        with self.assertRaises(ValueError):
            arxiv.build_query_url(("cs.AI",), 0)
        with self.assertRaises(ValueError):
            arxiv.build_query_url(("cs.AI",), arxiv.MAX_RESULTS_LIMIT + 1)


class ParseFeedTest(unittest.TestCase):
    def test_parses_entries(self):
        papers = arxiv.parse_feed(FIXTURE)
        self.assertEqual(len(papers), 2)
        first = papers[0]
        self.assertEqual(first.arxiv_id, "2601.00001")
        self.assertEqual(first.version, 2)
        self.assertEqual(first.title, "A Test Paper on Error-Corrected Qubits")
        self.assertEqual(first.summary, "This is a made-up abstract used only for tests.")
        self.assertEqual(first.authors, ["Alice Example", "Bob Example"])
        self.assertEqual(first.primary_category, "quant-ph")
        self.assertEqual(first.categories, ["quant-ph", "cs.AI"])
        self.assertEqual(first.published, "2026-01-01T09:00:00Z")
        self.assertEqual(first.abs_url, "https://arxiv.org/abs/2601.00001")
        self.assertEqual(first.pdf_url, "https://arxiv.org/pdf/2601.00001v2")

    def test_pdf_url_falls_back_when_link_is_missing(self):
        second = arxiv.parse_feed(FIXTURE)[1]
        self.assertEqual(second.pdf_url, "https://arxiv.org/pdf/2601.00002")

    def test_old_style_id(self):
        self.assertEqual(arxiv._split_id("http://arxiv.org/abs/hep-th/9901001v3"), ("hep-th/9901001", 3))

    def test_error_entry_raises(self):
        with self.assertRaisesRegex(arxiv.ArxivError, "incorrect id format"):
            arxiv.parse_feed(ERROR_FEED)

    def test_invalid_xml_raises(self):
        with self.assertRaises(arxiv.ArxivError):
            arxiv.parse_feed(b"<html>not a feed")


class FetchAllTest(unittest.TestCase):
    def test_merges_papers_found_in_several_fields(self):
        urls, sleeps = [], []

        def fake_get(url):
            urls.append(url)
            return FIXTURE

        papers = arxiv.fetch_all(["quantum", "ai"], max_results=2, get=fake_get, sleep=sleeps.append)
        self.assertEqual(len(urls), 2)
        self.assertEqual(sleeps, [arxiv.REQUEST_INTERVAL_SEC])
        self.assertEqual([p.arxiv_id for p in papers], ["2601.00001", "2601.00002"])
        self.assertEqual(papers[0].fields, ["quantum", "ai"])

    def test_unknown_field(self):
        with self.assertRaises(ValueError):
            arxiv.fetch_field("biology", get=lambda url: FIXTURE)


class HttpGetTest(unittest.TestCase):
    def test_retries_then_fails(self):
        error = arxiv.urllib.error.URLError("down")
        with mock.patch.object(arxiv.urllib.request, "urlopen", side_effect=error) as urlopen, \
                mock.patch.object(arxiv.time, "sleep") as sleep:
            with self.assertRaises(arxiv.ArxivError):
                arxiv.http_get("https://example.invalid/")
        self.assertEqual(urlopen.call_count, arxiv.MAX_ATTEMPTS)
        self.assertEqual(sleep.call_count, arxiv.MAX_ATTEMPTS - 1)

    def test_client_error_is_not_retried(self):
        error = arxiv.urllib.error.HTTPError("u", 400, "Bad Request", {}, None)
        with mock.patch.object(arxiv.urllib.request, "urlopen", side_effect=error) as urlopen:
            with self.assertRaisesRegex(arxiv.ArxivError, "HTTP 400"):
                arxiv.http_get("https://example.invalid/")
        self.assertEqual(urlopen.call_count, 1)


class CliTest(unittest.TestCase):
    def test_fetch_writes_json(self):
        papers = arxiv.parse_feed(FIXTURE)
        for p in papers:
            p.fields.append("ai")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "nested" / "papers.json"
            with mock.patch.object(cli, "fetch_all", return_value=papers), \
                    mock.patch("builtins.print"):
                code = cli.main(["fetch", "--fields", "ai", "--out", str(out)])
            self.assertEqual(code, 0)
            payload = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["fields"]["ai"]["label_ja"], "AI")
        self.assertEqual(payload["papers"][0]["arxiv_id"], "2601.00001")

    def test_fetch_reports_api_failure(self):
        with mock.patch.object(cli, "fetch_all", side_effect=arxiv.ArxivError("down")), \
                mock.patch("builtins.print"):
            self.assertEqual(cli.main(["fetch"]), 1)


if __name__ == "__main__":
    unittest.main()
