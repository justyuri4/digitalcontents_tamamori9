if [[ -f "$HOME/.zprofile" ]]; then
  source "$HOME/.zprofile"
fi
export ZDOTDIR="$VSCODE_WORKSPACE_ZDOTDIR"
