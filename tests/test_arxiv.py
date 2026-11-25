import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

from seedradar import arxiv, cli

FIXTURE = (Path(__file__).parent / "fixtures" / "feed.xml").read_bytes()


def _entry(arxiv_id, summary):
    return f"""<entry>
    <id>http://arxiv.org/abs/{arxiv_id}v1</id>
    <title>Paper {arxiv_id}</title>
    <summary>{summary}</summary>
    <published>2026-01-01T00:00:00Z</published>
    <updated>2026-01-01T00:00:00Z</updated>
    <category term="astro-ph.IM" scheme="http://arxiv.org/schemas/atom"/>
    <author><name>A</name></author>
  </entry>"""


def _feed(*entries):
    return ('<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" '
            'xmlns:arxiv="http://arxiv.org/schemas/atom">' + "".join(entries) + "</feed>").encode()


# Made-up results: only the second paper really mentions a space keyword.
SPACE_FEED = _feed(
    _entry("2601.00020", "Exoplanet atmospheres observed from the ground."),
    _entry("2601.00021", "A pose estimator for a spacecraft approaching a target."),
)
SPACE_FEED_TWO_HITS = _feed(
    _entry("2601.00022", "Remote sensing of crops."),
    _entry("2601.00023", "A cubesat radio."),
)

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


class BuildSearchQueryTest(unittest.TestCase):
    def test_categories_only(self):
        self.assertEqual(arxiv.build_search_query(("quant-ph",)), "cat:quant-ph")

    def test_keywords_are_required_in_the_abstract(self):
        query = arxiv.build_search_query(("cs.RO", "eess.SP"), ("spacecraft", "remote sensing"))
        self.assertEqual(query, '(cat:cs.RO OR cat:eess.SP) AND (abs:spacecraft OR abs:"remote sensing")')

    def test_hyphenated_keywords_are_quoted(self):
        query = arxiv.build_search_query(("cs.RO",), ("on-orbit", "cubesat"))
        self.assertEqual(query, '(cat:cs.RO) AND (abs:"on-orbit" OR abs:cubesat)')

    def test_keyword_with_quote_is_rejected(self):
        with self.assertRaises(ValueError):
            arxiv.build_search_query(("cs.RO",), ('bad"word',))

    def test_space_field_is_keyword_filtered(self):
        space = arxiv.FIELDS["space"]
        self.assertTrue(space.keywords)
        self.assertNotIn("astro-ph.EP", space.categories)
        url = arxiv.build_query_url(space.categories, 5, space.keywords)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["search_query"][0]
        self.assertIn(") AND (", query)
        self.assertIn("abs:spacecraft", query)

    def test_api_results_are_checked_against_keywords(self):
        # arXiv's search can return papers that do not mention any keyword; they are dropped.
        urls = []
        papers = arxiv.fetch_field("space", 5, get=lambda url: urls.append(url) or SPACE_FEED, source="api")
        self.assertEqual([p.arxiv_id for p in papers], ["2601.00021"])
        query = urllib.parse.parse_qs(urllib.parse.urlparse(urls[0]).query)
        self.assertEqual(query["max_results"], [str(5 * arxiv.KEYWORD_OVERFETCH)])

    def test_api_keyword_results_are_capped(self):
        papers = arxiv.fetch_field("space", 1, get=lambda url: SPACE_FEED_TWO_HITS, source="api")
        self.assertEqual(len(papers), 1)

    def test_settlement_field_keeps_only_settlement_papers(self):
        feed = _feed(
            _entry("2601.00030", "A cubesat radio for remote sensing."),
            _entry("2601.00031", "Closed-loop life support for a lunar base."),
        )
        urls = []
        papers = arxiv.fetch_field("settlement", 5, get=lambda url: urls.append(url) or feed, source="api")
        self.assertEqual([p.arxiv_id for p in papers], ["2601.00031"])
        self.assertIn("astro-ph.EP", urllib.parse.unquote(urls[0]))
        self.assertIn('abs:"life support"', urllib.parse.unquote_plus(urls[0]))

    def test_ai_papers_about_space_or_quantum_come_first(self):
        feed = _feed(
            _entry("2601.00040", "A better optimizer for language models in latent space."),
            _entry("2601.00041", "Reinforcement learning for satellite scheduling."),
            _entry("2601.00042", "A transformer that decodes quantum error syndromes."),
        )
        urls = []
        papers = arxiv.fetch_field("ai", 2, get=lambda url: urls.append(url) or feed, source="api")
        # "latent space" does not count as space; the other two are listed first.
        self.assertEqual([p.arxiv_id for p in papers], ["2601.00041", "2601.00042"])
        query = urllib.parse.parse_qs(urllib.parse.urlparse(urls[0]).query)
        self.assertEqual(query["search_query"], ["cat:cs.AI OR cat:cs.LG OR cat:cs.CL"])
        self.assertEqual(query["max_results"], [str(2 * arxiv.KEYWORD_OVERFETCH)])

    def test_boost_does_not_drop_papers(self):
        feed = _feed(_entry("2601.00050", "Nothing about space here."))
        self.assertEqual(len(arxiv.fetch_field("ai", 5, get=lambda url: feed, source="api")), 1)

    def test_fields_without_keywords_are_not_filtered(self):
        papers = arxiv.fetch_field("quantum", 2, get=lambda url: FIXTURE, source="api")
        self.assertEqual(len(papers), 2)

    def test_fetch_field_sends_keywords(self):
        urls = []
        arxiv.fetch_field("space", 5, get=lambda url: urls.append(url) or FIXTURE)
        self.assertIn("abs%3Aspacecraft", urls[0])
        urls.clear()
        arxiv.fetch_field("quantum", 5, get=lambda url: urls.append(url) or FIXTURE)
        self.assertNotIn("abs%3A", urls[0])


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


RSS_FIXTURE = (Path(__file__).parent / "fixtures" / "rss.xml").read_bytes()
# A real response from rss.arxiv.org on a day when the feed had no items.
RSS_EMPTY = (Path(__file__).parent / "fixtures" / "rss_empty.xml").read_bytes()


def unavailable(url):
    raise arxiv.ArxivUnavailable("HTTP Error 429")


class RssTest(unittest.TestCase):
    def test_rss_url_joins_categories(self):
        self.assertEqual(arxiv.build_rss_url(("cs.RO", "eess.SP")), "https://rss.arxiv.org/rss/cs.RO+eess.SP")

    def test_parses_items_and_skips_replacements(self):
        papers = arxiv.parse_rss(RSS_FIXTURE)
        self.assertEqual([p.arxiv_id for p in papers], ["2601.00011", "2601.00012", "2601.00014"])
        first = papers[0]
        self.assertEqual(first.version, 1)
        self.assertEqual(first.title, "Onboard Planning for a Made-up Spacecraft")
        self.assertEqual(first.summary, "We study a made-up spacecraft that plans its own manoeuvres.")
        self.assertEqual(first.authors, ["Alice Example", "Bob Example"])
        self.assertEqual(first.categories, ["cs.RO", "eess.SY"])
        self.assertEqual(first.primary_category, "cs.RO")
        self.assertEqual(first.published, "2026-01-02T05:00:00Z")
        self.assertEqual(first.abs_url, "https://arxiv.org/abs/2601.00011")
        self.assertEqual(first.pdf_url, "https://arxiv.org/pdf/2601.00011v1")
        self.assertEqual(first.source, "rss")
        self.assertEqual(papers[2].version, 2)

    def test_empty_feed(self):
        self.assertEqual(arxiv.parse_rss(RSS_EMPTY), [])

    def test_invalid_rss_raises(self):
        with self.assertRaises(arxiv.ArxivError):
            arxiv.parse_rss(b"<rss>")

    def test_keyword_match_is_whole_word_and_case_insensitive(self):
        self.assertTrue(arxiv.matches_keywords("A new Spacecraft design", ("spacecraft",)))
        self.assertTrue(arxiv.matches_keywords("uses Remote  sensing".replace("  ", " "), ("remote sensing",)))
        self.assertFalse(arxiv.matches_keywords("spacecrafts-like", ("cubesat",)))
        self.assertFalse(arxiv.matches_keywords("microcubesats", ("cubesat",)))
        self.assertTrue(arxiv.matches_keywords("anything", ()))

    def test_rss_fetch_applies_field_keywords(self):
        papers = arxiv.fetch_field("space", 5, get=lambda url: RSS_FIXTURE, source="rss")
        # The cell-biology paper mentions "satellite" but none of the space keywords.
        self.assertEqual([p.arxiv_id for p in papers], ["2601.00011", "2601.00014"])
        self.assertEqual(len(arxiv.fetch_field("space", 1, get=lambda url: RSS_FIXTURE, source="rss")), 1)


class FallbackTest(unittest.TestCase):
    def test_auto_falls_back_to_rss_when_api_is_unavailable(self):
        urls = []

        def get(url):
            urls.append(url)
            if url.startswith(arxiv.API_URL):
                raise arxiv.ArxivUnavailable("HTTP Error 429")
            return RSS_FIXTURE

        papers = arxiv.fetch_field("space", 5, get=get)
        self.assertEqual(len(urls), 2)
        self.assertTrue(urls[1].startswith(arxiv.RSS_URL))
        self.assertTrue(all(p.source == "rss" for p in papers))

    def test_api_only_does_not_fall_back(self):
        with self.assertRaises(arxiv.ArxivUnavailable):
            arxiv.fetch_field("space", 5, get=unavailable, source="api")

    def test_bad_query_error_does_not_fall_back(self):
        def get(url):
            raise arxiv.ArxivError("HTTP 400 from export.arxiv.org")

        with self.assertRaisesRegex(arxiv.ArxivError, "HTTP 400"):
            arxiv.fetch_field("space", 5, get=get)

    def test_empty_rss_after_api_failure_is_an_error(self):
        def get(url):
            if url.startswith(arxiv.API_URL):
                raise arxiv.ArxivUnavailable("HTTP Error 429")
            return RSS_EMPTY

        with self.assertRaisesRegex(arxiv.ArxivUnavailable, "no matching papers in today's feed"):
            arxiv.fetch_field("space", 5, get=get)

    def test_after_one_failure_the_run_stops_calling_the_api(self):
        urls = []

        def get(url):
            urls.append(url)
            if url.startswith(arxiv.API_URL):
                raise arxiv.ArxivUnavailable("HTTP Error 429")
            return RSS_FIXTURE

        arxiv.fetch_all(["space", "quantum", "ai"], max_results=5, get=get, sleep=lambda s: None)
        self.assertEqual(sum(u.startswith(arxiv.API_URL) for u in urls), 1)
        self.assertEqual(sum(u.startswith(arxiv.RSS_URL) for u in urls), 3)

    def test_unknown_source(self):
        with self.assertRaises(ValueError):
            arxiv.fetch_field("space", 5, get=lambda url: FIXTURE, source="mirror")


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
        with mock.patch.object(arxiv.urllib.request, "urlopen", side_effect=error), \
                mock.patch.object(arxiv.time, "sleep"):
            with self.assertRaises(arxiv.ArxivUnavailable):
                arxiv.http_get("https://example.invalid/")
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

    def test_fetch_with_no_papers_keeps_the_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "papers.json"
            out.write_text("{}", encoding="utf-8")
            with mock.patch.object(cli, "fetch_all", return_value=[]), mock.patch("builtins.print"):
                self.assertEqual(cli.main(["fetch", "--source", "rss", "--out", str(out)]), 1)
            self.assertEqual(out.read_text(encoding="utf-8"), "{}")

    def test_fetch_passes_source(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(cli, "fetch_all", return_value=[]) as fetch_all, mock.patch("builtins.print"):
            cli.main(["fetch", "--source", "rss", "--out", str(Path(tmp) / "p.json")])
        self.assertEqual(fetch_all.call_args.kwargs["source"], "rss")

    def test_fetch_reports_api_failure(self):
        with mock.patch.object(cli, "fetch_all", side_effect=arxiv.ArxivError("down")), \
                mock.patch("builtins.print"):
            self.assertEqual(cli.main(["fetch"]), 1)


if __name__ == "__main__":
    unittest.main()
