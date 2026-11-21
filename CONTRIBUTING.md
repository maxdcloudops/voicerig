# Contributing

Bug reports with a `rig.yaml` that reproduces them are the most useful thing you can send.

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q        # no Docker, no network, no API keys
```

The rule this project follows, and asks of changes: **a check has to be able to fail.** If you add a step kind or
change how a verdict is reached, add a test where the check is expected to fail, not only one where it passes. The
engine imports no LiveKit and is tested with fakes (`tests/test_engine.py`) - keep it that way.

- Keep `engine.py` free of Docker and LiveKit imports; I/O belongs in `client.py`, `stub.py`, `compose.py`, `cli.py`.
- Secrets never reach disk or the rig's own containers (`tests/test_compose.py` guards this).
- Changing the README's claims? Re-run the example and update the numbers; do not leave stale ones.
- Pull requests run the unit tests on Python 3.10-3.13. The Docker end-to-end run (`e2e.yml`) needs provider keys
  and is run by a maintainer.

By contributing you agree your work is licensed under Apache-2.0.
