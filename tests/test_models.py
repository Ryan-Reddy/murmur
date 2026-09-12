"""Tests for where a Whisper model is found, and where a new one is put.

    venv\\Scripts\\python.exe -m unittest discover -s tests -v

Two constraints pull in opposite directions.

An MSIX package installs read-only under `Program Files\\WindowsApps`, so a
model that shipped with the app can be read and never written beside -- a
download into the install directory fails on a Store install and nowhere else,
which is the worst place for a bug to only appear.

And a model that was downloaded has to live somewhere writable, out of the
repository, because a cache sitting in `models/` added 1.4 GB to every build
of the package until someone noticed.

So: look where the app shipped, fall back to what has been fetched, and always
fetch into the writable place.
"""

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import listener  # noqa: E402


def make_model(at: Path):
    """The four files faster-whisper needs to load a directory directly."""
    at.mkdir(parents=True, exist_ok=True)
    for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"):
        (at / name).write_text("{}", encoding="utf-8")
    return at


class FindingAModel(unittest.TestCase):

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.here = Path(self.tmp.name) / "app"
        self.cache = Path(self.tmp.name) / "local" / "models"
        self.here.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def find(self, size="base"):
        return listener.find_model(size, shipped=self.here, cache=self.cache)

    def test_a_shipped_model_is_used_where_it_lies(self):
        made = make_model(self.here / "whisper-base")
        self.assertEqual(Path(self.find()), made)

    def test_a_shipped_model_is_preferred_over_a_downloaded_one(self):
        """It is the same weights and it is already on the disk the app was
        installed from; fetching it again would be pure waste."""
        made = make_model(self.here / "whisper-base")
        make_model(self.cache / "models--Systran--faster-whisper-base")
        self.assertEqual(Path(self.find()), made)

    def test_without_one_it_falls_back_to_the_name_to_download(self):
        self.assertEqual(self.find(), "base")

    def test_a_half_written_shipped_model_is_ignored(self):
        """An interrupted copy leaves a directory with no weights in it.
        Loading that fails deep inside CTranslate2 with nothing useful said;
        falling back and fetching is the recoverable answer."""
        (self.here / "whisper-base").mkdir(parents=True)
        (self.here / "whisper-base" / "config.json").write_text("{}", encoding="utf-8")
        self.assertEqual(self.find(), "base")

    def test_each_size_is_looked_for_separately(self):
        make_model(self.here / "whisper-base")
        self.assertEqual(self.find("small"), "small")


class WhereDownloadsGo(unittest.TestCase):

    def test_the_cache_is_outside_the_repository(self):
        """A 464 MB download inside `models/` was quietly added to every
        build of the Store package -- 612 MB to 1823 MB before anybody looked.
        It also cannot be written at all on a Store install."""
        cache = listener.model_cache()
        self.assertNotIn(str(ROOT).lower(), str(cache).lower())

    def test_it_is_under_the_app_name(self):
        self.assertIn("cufflink", str(listener.model_cache()).lower())

    def test_a_transcriber_asked_for_a_shipped_model_does_not_want_a_root(self):
        """faster-whisper takes either a size to fetch or a directory to load.
        Handing it both a directory and a download root is how you get a
        download that lands next to a model already there."""
        with TemporaryDirectory() as tmp:
            here = Path(tmp)
            make_model(here / "whisper-tiny")
            got = listener.WhisperTranscriber("tiny", shipped=here)
            self.assertTrue(Path(got.source).is_dir())
            self.assertTrue(got.cached(), "a shipped model is already here")


if __name__ == "__main__":
    unittest.main()
