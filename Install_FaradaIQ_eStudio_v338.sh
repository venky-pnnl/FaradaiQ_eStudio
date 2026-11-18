#!/bin/bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
PYTHON_URL='https://www.python.org/ftp/python/3.12.10/python-3.12.10-macos11.pkg'
# PyPA cibuildwheel publishes the SHA-256 for this exact python.org package.
PYTHON_SHA256='8373e58da4ea146b3eb1c1f9834f19a319440b6b679b06050b1f9ee3237aa8e4'

fail() {
  printf '%s\n' "$1" >&2
  exit 1
}

find_python() {
  local candidate executable
  for candidate in "${FARADAIQ_PYTHON:-python3.12}" \
    "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12" \
    /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12 python3.12 python3; do
    if [[ "$candidate" == */* ]]; then
      executable="$candidate"
    else
      executable="$(type -P -- "$candidate" || true)"
    fi
    # Apple's system executable can prompt to install Xcode command-line tools.
    if [[ "$PLATFORM" == Darwin && "$executable" == /usr/bin/python3 ]]; then
      continue
    fi
    if [[ -f "$executable" && -x "$executable" ]] && "$executable" -I -c \
      'import sys, tkinter, venv; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)' \
      >/dev/null 2>&1; then
      printf '%s\n' "$executable"
      return 0
    fi
  done
  return 1
}

bootstrap_python() (
  local staging pkg
  staging="$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/FaradaIQ-Python.XXXXXXXX")" \
    || fail 'Could not create a temporary folder for the Python download.'
  trap '/bin/rm -rf -- "$staging"' EXIT
  pkg="$staging/python-3.12.10-macos11.pkg"

  printf '%s\n' 'Downloading Python 3.12.10 with Tk from python.org.'
  /usr/bin/curl --disable --fail --location --show-error --progress-bar \
    --proto '=https' --proto-redir '=https' --tlsv1.2 --retry 2 \
    --connect-timeout 20 --max-time 600 --output "$pkg" "$PYTHON_URL" \
    || fail 'Could not download Python from python.org. Check your network connection and run the eStudio installer again.'

  printf '%s\n' 'Verifying the Python installer checksum and macOS package signature.'
  printf '%s  %s\n' "$PYTHON_SHA256" "$pkg" | /usr/bin/shasum -a 256 -c - \
    || fail 'Python installer SHA-256 verification failed.'
  /usr/sbin/pkgutil --check-signature "$pkg" \
    || fail 'macOS could not verify the Python package signature.'

  printf '%s\n' 'Installing Python. Enter your Mac administrator password if Terminal prompts for it.'
  /usr/bin/sudo /usr/sbin/installer -pkg "$pkg" -target / \
    || fail 'Python installation did not complete. Use an authorized Mac administrator account or ask IT to install Python 3.12 with Tk.'
)

plan=false
for arg in "$@"; do
  case "$arg" in
    --plan) plan=true ;;
    -h|--help)
      printf '%s\n' 'Usage: Install_FaradaIQ_eStudio_v338.command [--install-root DIRECTORY] [--no-launch] [--plan]'
      exit 0 ;;
  esac
done

PLATFORM="$(/usr/bin/uname -s)"
if [[ "$plan" == false && "$PLATFORM" != Darwin ]]; then
  fail 'Run this installer on macOS. Use the Windows BAT on Windows.'
fi
if [[ "$plan" == false ]]; then
  log_dir="$HOME/Library/Logs/FaradaIQ eStudio"
  if /bin/mkdir -p "$log_dir" && [[ -w "$log_dir" ]]; then
    log_file="$log_dir/install_v338_$(/bin/date '+%Y%m%d_%H%M%S').log"
    exec > >(/usr/bin/tee -a "$log_file") 2> >(/usr/bin/tee -a "$log_file" >&2)
    printf 'Installation log: %s\n' "$log_file"
  fi
  printf '%s\n' 'FaradaIQ eStudio v338 for macOS' 'Checking for Python 3.12 with Tk.'
fi

if ! PYTHON="$(find_python)"; then
  if [[ "$plan" == true ]]; then
    printf '%s\n' 'The macOS installer would download and install Python 3.12 with Tk before installing eStudio.'
    exit 0
  fi
  bootstrap_python
  hash -r
  PYTHON="$(find_python)" || fail 'After installation, Python 3.12 with Tk was still unavailable. Check the Python installer output above.'
fi

if [[ "$plan" == false ]]; then
  printf 'Using Python at %s\n' "$PYTHON"
fi
exec "$PYTHON" "$ROOT/scripts/macos_install.py" "$@"
