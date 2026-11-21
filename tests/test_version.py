import pathlib
import re

import voicerig

ROOT = pathlib.Path(__file__).parents[1]


def test_citation_file_carries_the_package_version():
    # CITATION.cff is what a paper cites; a stale version there cites a release that does not match the code.
    cff = (ROOT / "CITATION.cff").read_text()
    assert re.search(rf"^version: {re.escape(voicerig.__version__)}$", cff, re.M)


def test_version_is_single_sourced():
    toml = (ROOT / "pyproject.toml").read_text()
    assert 'dynamic = ["version"]' in toml and not re.search(r'^version\s*=\s*"', toml, re.M)
