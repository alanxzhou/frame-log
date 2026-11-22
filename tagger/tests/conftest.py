"""Keep every test away from the real config folder.

Tests point FRAMELOG_TAGGER_CONFIG at their own tmp_path with monkeypatch, which puts the old value back at the end of
the test. A background thread that outlives its test (the Lightroom library read saves faces.json when it finishes)
then resolved the config path with no override, and wrote into the real %APPDATA%\\framelog-tagger. Setting a scratch
folder for the whole run means "the old value" is that folder, never the real one.
"""

import os
import shutil
import tempfile

_scratch = tempfile.mkdtemp(prefix="framelog-tagger-tests-")


def pytest_configure(config):
    os.environ["FRAMELOG_TAGGER_CONFIG"] = os.path.join(_scratch, "config.json")


def pytest_unconfigure(config):
    shutil.rmtree(_scratch, ignore_errors=True)
