"""QA-only pytest plugin that prevents completed legacy GUI tests from hanging process shutdown.

Some legacy scientific/GUI imports can leave non-daemon third-party threads alive
on Linux after pytest has already emitted its terminal summary. The release QA
runner invokes this plugin only in its isolated child processes. After the
terminal summary is written, a short daemon timer exits with pytest's real exit
status so completed groups cannot hang indefinitely during interpreter teardown.
"""
from __future__ import annotations

import os
import sys
import threading


def pytest_terminal_summary(terminalreporter, exitstatus, config):  # pragma: no cover - process-control hook
    def _finish() -> None:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        finally:
            os._exit(int(exitstatus))

    timer = threading.Timer(1.0, _finish)
    timer.daemon = True
    timer.start()
