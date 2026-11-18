"""Enable the public eStudio GitHub Pages site on its first release upload."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

REPOSITORY = "venky-pnnl/faradaiq_estudio"


def configure_homepage(repository, api):
    # A configured fork or private repository should keep its own publishing policy.
    if repository.casefold() != REPOSITORY:
        return None
    endpoint = f"repos/{repository}"
    repo = api(endpoint)
    if repo.get("private") is not False:
        return None

    pages_endpoint = endpoint + "/pages"
    if repo["has_pages"]:
        pages = api(pages_endpoint)
    else:
        pages = api(
            "--method", "POST", pages_endpoint,
            "-f", "source[branch]=main", "-f", "source[path]=/docs",
        )

    url = pages["html_url"]
    if not repo.get("homepage"):
        api("--method", "PATCH", endpoint, "-f", "homepage=" + url)
    return url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gh", required=True)
    parser.add_argument("--repository", required=True)
    args = parser.parse_args()

    def api(*parameters):
        result = subprocess.run(
            [args.gh, "api", "--hostname", "github.com", *parameters],
            capture_output=True, check=True, text=True, encoding="utf-8", timeout=30,
        )
        return json.loads(result.stdout)

    try:
        url = configure_homepage(args.repository, api)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError) as error:
        detail = getattr(error, "stderr", "") or str(error)
        print(f"GitHub Pages was not confirmed: {detail.strip()}", file=sys.stderr)
        return 1
    if url:
        print(f"Project homepage: {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
