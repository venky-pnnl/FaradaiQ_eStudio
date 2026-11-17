import hashlib
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile, ZipInfo

POWERSHELL = os.environ.get("ESTUDIO_PWSH") or shutil.which("pwsh")
PACKAGE = Path(__file__).resolve().parents[1]
DRIVER = r'''
function Invoke-RestMethod {
    param([string]$Uri, $Headers, [int]$TimeoutSec)
    if ($Uri -ne 'https://api.github.com/repos/cli/cli/releases/latest') { throw "Unexpected URL: $Uri" }
    Add-Content -LiteralPath $env:TEST_CALLS -Value 'metadata cli/cli'
    if ($env:TEST_SCENARIO -eq 'network-blocked') { throw 'HTTPS blocked by test fixture' }
    @{
        tag_name = 'v2.100.0'
        assets = @(
            @{name = $env:TEST_ASSET; digest = ('sha256:' + $env:TEST_DIGEST)}
        )
    } | ConvertTo-Json -Depth 4 | ConvertFrom-Json
}
function Invoke-WebRequest {
    param([string]$Uri, [string]$OutFile, [int]$TimeoutSec, [switch]$UseBasicParsing)
    $expected = 'https://github.com/cli/cli/releases/download/v2.100.0/' + $env:TEST_ASSET
    if ($Uri -ne $expected) { throw "Unexpected URL: $Uri" }
    Add-Content -LiteralPath $env:TEST_CALLS -Value "download $env:TEST_ASSET"
    Copy-Item -LiteralPath $env:TEST_ZIP -Destination $OutFile
}
& $env:TEST_DISTRIBUTE -Action $env:TEST_ACTION
'''
TOOL = r'''
import os
from pathlib import Path
import shutil
import sys

name = Path(sys.argv[0]).stem
args = sys.argv[1:]
with open(os.environ["TEST_CALLS"], "a") as stream:
    stream.write(" ".join([name] + args) + "\n")
if name == "winget":
    if os.environ["TEST_SCENARIO"] == "install-failed":
        print("installer blocked", file=sys.stderr)
        sys.exit(23)
    package_id = args[args.index("--id") + 1]
    target = (Path(os.environ["ProgramFiles"]) / "GitHub CLI" / "gh.exe"
              if package_id == "GitHub.cli" else
              Path(os.environ["ProgramFiles"]) / "Git" / "cmd" / "git.exe")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(os.environ["TEST_STUB"], target)
elif name == "gh" and args[:2] == ["auth", "status"]:
    if os.environ["TEST_SCENARIO"] == "login":
        print("Authentication required", file=sys.stderr)
        sys.exit(1)
elif name == "gh" and args[:2] == ["repo", "view"]:
    if os.environ["TEST_SCENARIO"] == "new-repo":
        print("Repository does not exist", file=sys.stderr)
        sys.exit(1)
'''

@unittest.skipUnless(POWERSHELL and os.name == "posix", "requires PowerShell 7 and POSIX test executables")
class SetupTest(unittest.TestCase):
    def run_setup(self, scenario, *, command_path=(), installed=(), action="Configure",
                  architecture="AMD64", native_architecture="", asset_arch="amd64"):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            stub = root / "stub"
            stub.write_text("#!" + sys.executable + "\n" + TOOL)
            stub.chmod(0o755)
            for command in command_path:
                path = bin_dir / command
                path.write_bytes(stub.read_bytes())
                path.chmod(0o755)
            program_files = root / "Program Files"
            for command in installed:
                folder = program_files / ("Git/cmd" if command == "git" else "GitHub CLI")
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / f"{command}.exe"
                path.write_bytes(stub.read_bytes())
                path.chmod(0o755)
            log = root / "calls"
            appdata = root / "appdata"
            appdata.mkdir()
            asset = f"gh_2.100.0_windows_{asset_arch}.zip"
            package = root / asset
            with ZipFile(package, "w") as archive:
                entry = ZipInfo("../gh.exe" if scenario == "bad-entry" else "bin/gh.exe")
                entry.create_system = 3
                entry.external_attr = 0o100755 << 16
                archive.writestr(entry, stub.read_bytes())
            digest = hashlib.sha256(package.read_bytes()).hexdigest()
            if scenario == "bad-hash":
                digest = "0" * 64
            runner = root / "driver.ps1"
            runner.write_text(DRIVER)
            env = dict(os.environ, PATH=str(bin_dir), ProgramFiles=str(program_files),
                       LOCALAPPDATA=str(appdata), TEST_CALLS=str(log), TEST_STUB=str(stub),
                       TEST_SCENARIO=scenario, TEST_DISTRIBUTE=str(PACKAGE / "scripts/distribute.ps1"),
                       TEST_ACTION=action, TEST_ZIP=str(package), TEST_DIGEST=digest, TEST_ASSET=asset,
                       PROCESSOR_ARCHITECTURE=architecture, PROCESSOR_ARCHITEW6432=native_architecture)
            if scenario == "portable-reuse":
                portable = appdata / "FaradaIQ/eStudio/tools/GitHubCLI/gh.exe"
                portable.parent.mkdir(parents=True)
                shutil.copy2(stub, portable)
            result = subprocess.run(
                [POWERSHELL, "-NoProfile", "-NonInteractive", "-File", str(runner)],
                env=env, text=True, capture_output=True, timeout=30,
            )
            calls = log.read_text().splitlines() if log.exists() else []
            config = appdata / "FaradaIQ/eStudio/github.json"
            return result, calls, json.loads(config.read_text(encoding="utf-8-sig")) if config.exists() else None

    def test_both_missing_install_and_configure_in_same_process(self):
        result, calls, config = self.run_setup("normal", command_path=("winget",))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(config["repository"], "venky-pnnl/FaradaiQ_eStudio")
        self.assertEqual([call.split()[3] for call in calls if call.startswith("winget ")],
                         ["Git.Git", "GitHub.cli"])
        self.assertFalse(any(call.startswith("gh repo create ") for call in calls))

    def test_installed_off_path_skips_winget(self):
        result, calls, config = self.run_setup("login", command_path=("git", "winget"), installed=("gh",))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIsNotNone(config)
        self.assertTrue(any(call.startswith("gh auth login ") for call in calls))
        self.assertFalse(any(call.startswith("winget ") for call in calls))

    def test_ready_tools_keep_repository_creation_behavior(self):
        result, calls, config = self.run_setup("new-repo", command_path=("git", "gh"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIsNotNone(config)
        self.assertTrue(any(call.startswith("gh repo create venky-pnnl/FaradaiQ_eStudio --public ") for call in calls))

    def test_winget_failure_uses_portable_cli(self):
        result, calls, config = self.run_setup("install-failed", command_path=("git", "winget"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("WinGet exit code 23", result.stdout + result.stderr)
        self.assertIsNotNone(config)
        self.assertTrue(any(call.startswith("gh auth setup-git") for call in calls))

    def test_winget_absent_downloads_cli_and_configures_repository(self):
        result, calls, config = self.run_setup("normal", command_path=("git",))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls[:2], ["metadata cli/cli", "download gh_2.100.0_windows_amd64.zip"])
        self.assertEqual(config["repository"], "venky-pnnl/FaradaiQ_eStudio")

    def test_portable_download_selects_native_architecture(self):
        for process, native, asset_arch in [("x86", "ARM64", "arm64"), ("x86", "", "386")]:
            with self.subTest(asset_arch=asset_arch):
                result, calls, _ = self.run_setup("normal", command_path=("git",),
                    architecture=process, native_architecture=native, asset_arch=asset_arch)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(f"download gh_2.100.0_windows_{asset_arch}.zip", calls)

    def test_portable_cli_is_reused(self):
        result, calls, config = self.run_setup("portable-reuse", command_path=("git",))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIsNotNone(config)
        self.assertTrue(all(call.startswith("gh ") for call in calls))

    def test_invalid_download_is_not_executed(self):
        for scenario, message in [("bad-hash", "SHA-256"), ("bad-entry", "expected gh.exe")]:
            with self.subTest(scenario=scenario):
                result, calls, config = self.run_setup(scenario, command_path=("git",))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stdout + result.stderr)
                self.assertIsNone(config)
                self.assertTrue(all(not call.startswith("gh ") for call in calls))

    def test_network_failure_reports_official_download(self):
        result, calls, config = self.run_setup("network-blocked", command_path=("git",))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("https://github.com/cli/cli/releases/latest", result.stdout + result.stderr)
        self.assertIsNone(config)
        self.assertEqual(calls, ["metadata cli/cli"])

    def test_publisher_uses_same_dependency_check(self):
        result, calls, config = self.run_setup("network-blocked", command_path=("git",), action="Upload")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("HTTPS blocked by test fixture", result.stdout + result.stderr)
        self.assertIsNone(config)
        self.assertEqual(calls, ["metadata cli/cli"])


if __name__ == "__main__":
    unittest.main()
