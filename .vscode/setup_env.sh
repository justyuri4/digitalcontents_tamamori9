#!/bin/sh
set -eu

if [ "$(uname -s)" != "Darwin" ]; then
  printf '%s\n' "This setup script supports macOS only." >&2
  exit 1
fi

if [ -x /opt/homebrew/bin/brew ]; then
  PATH="/opt/homebrew/bin:$PATH"
  export PATH
elif [ -x /usr/local/bin/brew ]; then
  PATH="/usr/local/bin:$PATH"
  export PATH
fi

if ! command -v brew >/dev/null 2>&1; then
  if ! command -v curl >/dev/null 2>&1; then
    printf '%s\n' "curl is required to install Homebrew." >&2
    exit 1
  fi
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  if [ -x /opt/homebrew/bin/brew ]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [ -x /usr/local/bin/brew ]; then
    eval "$(/usr/local/bin/brew shellenv)"
  else
    printf '%s\n' "Homebrew was not found in a standard installation path." >&2
    exit 1
  fi
fi

if ! command -v pyenv >/dev/null 2>&1; then
  brew install pyenv
fi

python_version=$(tr -d '[:space:]' < .python-version)
if [ -z "$python_version" ]; then
  printf '%s\n' ".python-version is empty." >&2
  exit 1
fi

pyenv install -s "$python_version"
if [ ! -x .venv/bin/python ]; then
  PYENV_VERSION="$python_version" pyenv exec python -m venv .venv
fi

if [ -f requirements.txt ]; then
  .venv/bin/python -m pip install -r requirements.txt
fi