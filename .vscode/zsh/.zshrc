if [[ -f "$HOME/.zshrc" ]]; then
  source "$HOME/.zshrc"
fi
export ZDOTDIR="$VSCODE_WORKSPACE_ZDOTDIR"

if [[ -f "$VSCODE_WORKSPACE_FOLDER/.venv/bin/activate" ]]; then
  source "$VSCODE_WORKSPACE_FOLDER/.venv/bin/activate"
fi
