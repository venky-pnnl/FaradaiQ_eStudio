"""Run the current release contracts without collecting historical releases."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


def release_test_files(root: Path) -> list[str]:
    version = json.loads((root / 'release.json').read_text(encoding='utf-8'))['version']
    names = [
        f'test_estudio_{version}.py', 'test_estudio_distribution.py',
        f'test_browser_entry_{version}.py', f'test_browser_data_{version}.py',
        f'test_browser_analysis_{version}.py', 'test_browser_platform_launchers.py',
        f'test_browser_server_{version}.py', f'test_managed_scilink_{version}.py',
        f'test_managed_scilink_ui_{version}.py', f'test_managed_platform_install_{version}.py',
        f'test_publish_payload_{version}.py', f'test_analysis_tools_setup_{version}.py',
        f'test_browser_plotting_{version}.py', f'test_browser_catalog_{version}.py',
        f'test_browser_science_controls_{version}.py', f'test_browser_services_{version}.py',
    ]
    paths = {root / 'tests' / name for name in names}
    paths.update((root / 'tests').glob(f'test_{version}_*.py'))
    missing = sorted(str(path) for path in paths if not path.is_file())
    if missing:
        raise FileNotFoundError('Missing release tests: ' + ', '.join(missing))
    return sorted(path.relative_to(root).as_posix() for path in paths)


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    raise SystemExit(subprocess.call([sys.executable, '-m', 'pytest', *release_test_files(root),
                                     *sys.argv[1:]], cwd=root))
