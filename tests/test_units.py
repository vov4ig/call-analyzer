"""Unit tests that need no GPU, no models and no network.

    python -m unittest discover -s tests -v
"""
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# keep tests away from the real data dir
os.environ["CALL_ANALYZER_HOME"] = tempfile.mkdtemp(prefix="ca-tests-")

import analyze_call          # noqa: E402
import cc_paths              # noqa: E402
import webapp                # noqa: E402


class TestFmtSize(unittest.TestCase):
    def test_bytes(self):
        self.assertEqual(webapp.fmt_size(0), "0 Б")
        self.assertEqual(webapp.fmt_size(512), "512 Б")

    def test_units(self):
        self.assertEqual(webapp.fmt_size(1024), "1.0 КБ")
        self.assertEqual(webapp.fmt_size(1024 ** 2), "1.0 МБ")
        self.assertEqual(webapp.fmt_size(3 * 1024 ** 3), "3.0 ГБ")

    def test_terabytes_do_not_crash(self):
        self.assertEqual(webapp.fmt_size(5 * 1024 ** 4), "5120.0 ГБ")


class TestSafeName(unittest.TestCase):
    def test_strips_directories(self):
        self.assertEqual(webapp.safe_name(r"C:\calls\day1\call.mp3"), "call.mp3")
        self.assertEqual(webapp.safe_name("../../etc/passwd"), "passwd")

    def test_replaces_forbidden_characters(self):
        self.assertEqual(webapp.safe_name('my "call"?.mp3'), "my _call__.mp3")

    def test_empty_falls_back(self):
        self.assertEqual(webapp.safe_name(""), "audio")
        self.assertEqual(webapp.safe_name(None), "audio")
        self.assertEqual(webapp.safe_name(".."), "audio")

    def test_length_cap(self):
        self.assertEqual(len(webapp.safe_name("a" * 500)), 180)


class TestMarkdown(unittest.TestCase):
    def test_headings_and_paragraphs(self):
        html = webapp.md_to_html("# Title\n\nsome text")
        self.assertEqual(html, "<h1>Title</h1>\n<p>some text</p>")

    def test_bullet_list(self):
        html = webapp.md_to_html("- one\n- two\n\nafter")
        self.assertEqual(html,
                         "<ul>\n<li>one</li>\n<li>two</li>\n</ul>\n<p>after</p>")

    def test_inline_formatting(self):
        self.assertIn("<strong>bold</strong>", webapp.md_to_html("**bold**"))
        self.assertIn("<code>x = 1</code>", webapp.md_to_html("`x = 1`"))

    def test_html_is_escaped(self):
        html = webapp.md_to_html("<script>alert(1)</script>")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_empty(self):
        self.assertEqual(webapp.md_to_html(""), "")


class TestDisposition(unittest.TestCase):
    def test_name_and_filename(self):
        got = webapp.parse_disposition('form-data; name="file"; filename="a b.mp3"')
        self.assertEqual(got, ("file", "a b.mp3"))

    def test_name_only(self):
        self.assertEqual(webapp.parse_disposition('form-data; name="lang"'), ("lang", None))


class TestBodyReader(unittest.TestCase):
    def test_read_line_and_exact(self):
        r = webapp.BodyReader(io.BytesIO(b"a: b\r\n\r\npayload"), 15)
        self.assertEqual(r.read_line(), b"a: b")
        self.assertEqual(r.read_line(), b"")
        self.assertEqual(r.read_exact(7), b"payload")

    def test_truncated_body_raises(self):
        r = webapp.BodyReader(io.BytesIO(b"short"), 100)
        with self.assertRaises(webapp.MultipartError):
            r.read_line()

    def test_read_part_splits_on_boundary(self):
        delim = b"\r\n--X"
        r = webapp.BodyReader(io.BytesIO(b"data" + delim + b"tail"), 13)
        sink = io.BytesIO()
        r.read_part(sink, delim)
        self.assertEqual(sink.getvalue(), b"data")


class TestBuildTranscript(unittest.TestCase):
    def test_groups_consecutive_segments_of_one_speaker(self):
        data = {"segments": [
            {"speaker": "SPEAKER_00", "start": 0.0, "text": "Здравствуйте."},
            {"speaker": "SPEAKER_00", "start": 1.5, "text": "Чем помочь?"},
            {"speaker": "SPEAKER_01", "start": 65.0, "text": "Не проходит платёж."},
        ]}
        out = analyze_call.build_transcript(data)
        self.assertEqual(out.splitlines(), [
            "[00:00] SPEAKER_00: Здравствуйте. Чем помочь?",
            "[01:05] SPEAKER_01: Не проходит платёж.",
        ])

    def test_limit_is_applied(self):
        data = {"segments": [{"speaker": "S", "start": 0, "text": "x" * 100}]}
        self.assertEqual(len(analyze_call.build_transcript(data, limit=10)), 10)

    def test_no_segments(self):
        self.assertEqual(analyze_call.build_transcript({"segments": []}), "")


class TestPaths(unittest.TestCase):
    def test_home_follows_env(self):
        self.assertEqual(str(cc_paths.HOME), os.environ["CALL_ANALYZER_HOME"])

    def test_ollama_url_normalisation(self):
        old = os.environ.pop("OLLAMA_HOST", None)
        try:
            self.assertEqual(cc_paths.ollama_url(), "http://127.0.0.1:11434")
            os.environ["OLLAMA_HOST"] = "192.168.1.5:11434"
            self.assertEqual(cc_paths.ollama_url(), "http://192.168.1.5:11434")
            os.environ["OLLAMA_HOST"] = "http://box:11434/"
            self.assertEqual(cc_paths.ollama_url(), "http://box:11434")
        finally:
            os.environ.pop("OLLAMA_HOST", None)
            if old is not None:
                os.environ["OLLAMA_HOST"] = old

    def test_offline_flags_are_set(self):
        cc_paths.configure_offline_env()
        self.assertEqual(os.environ["HF_HUB_OFFLINE"], "1")
        self.assertEqual(os.environ["TRANSFORMERS_OFFLINE"], "1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
