#!/usr/bin/env bash
# Lumus demo installer. Safe to re-run. No sudo, nothing is deleted, no secrets are read or written.
#   curl -fsSL https://raw.githubusercontent.com/Lumus-ai/lumus-code/demo/install.sh | bash
# What it does:
#   1. clones (or fast-forward updates) the `demo` branch into ~/lumus-code-demo
#   2. makes a private Python venv in ~/.local/share/lumus-demo and installs playwright there
#   3. creates ~/.keys/ (mode 700) and an empty ~/.keys/hands.txt (mode 600) for your own Gemini API key
#   4. writes a `lumus-demo` launcher into ~/.local/bin
set -eu

REPO_URL="${LUMUS_DEMO_REPO:-https://github.com/Lumus-ai/lumus-code.git}"
DIR="${LUMUS_DEMO_DIR:-$HOME/lumus-code-demo}"
VENV="${LUMUS_DEMO_VENV:-$HOME/.local/share/lumus-demo/venv}"
BIN="$HOME/.local/bin"
KEYS="$HOME/.keys"

need() { command -v "$1" >/dev/null 2>&1 || { echo "ERROR: '$1' is required but not installed." >&2; exit 1; }; }
need git
if ! command -v python3 >/dev/null 2>&1; then
  if [ "$(uname -s)" = "Darwin" ]; then echo "ERROR: python3 is required. On macOS: xcode-select --install  (or: brew install python), then run this again." >&2; exit 1; fi
fi
need python3

echo "==> 1/4 code: $DIR"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" pull --ff-only || echo "WARN: could not fast-forward $DIR (local changes?). Leaving it as is."
else
  git clone --depth 1 -b demo "$REPO_URL" "$DIR"
fi

echo "==> 2/4 python venv with playwright: $VENV"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV" || { echo "ERROR: python3 -m venv failed (on Debian/Ubuntu: sudo apt install python3-venv)." >&2; exit 1; }
fi
"$VENV/bin/python" -m pip install --quiet --upgrade playwright   # attaches to your own Chrome, no browser download

echo "==> 3/4 key folder: $KEYS"
mkdir -p "$KEYS"; chmod 700 "$KEYS"
[ -e "$KEYS/hands.txt" ] || { : > "$KEYS/hands.txt"; chmod 600 "$KEYS/hands.txt"; }

echo "==> 4/4 launcher: $BIN/lumus-demo"
mkdir -p "$BIN"
cat > "$BIN/lumus-demo" <<LAUNCHER
#!/usr/bin/env bash
# lumus-demo launcher (written by install.sh). Usage: lumus-demo [--choose] | ui | chrome|init|run|brain-prompt|brain-reply|step|status|report|update
DIR="$DIR"
VENV="$VENV"
export LUMUS_HANDS_URL="\${LUMUS_HANDS_URL:-https://generativelanguage.googleapis.com/v1beta/openai}"
export LUMUS_HANDS_MODEL="\${LUMUS_HANDS_MODEL:-gemini-3.1-flash-lite}"
export LUMUS_HANDS_KEY_FILE="\${LUMUS_HANDS_KEY_FILE:-\$HOME/.keys/hands.txt}"
case "\${1:-}" in
  "") set -- start ;;
  --choose) set -- start --choose ;;
  update) exec git -C "\$DIR" pull --ff-only ;;
  run|step|hands)
    if [ ! -s "\$LUMUS_HANDS_KEY_FILE" ] && ! "\$VENV/bin/python" "\$DIR/script/lumus_providers.py" has-active; then
      echo "No model for the hands yet. Run: lumus-demo ui  (the page walks you through it in a minute), or paste a Gemini key into \$LUMUS_HANDS_KEY_FILE." >&2
      exit 1
    fi ;;
esac
exec "\$VENV/bin/python" "\$DIR/script/lumus-loop.py" "\$@"
LAUNCHER
chmod 755 "$BIN/lumus-demo"

echo
echo "Done."
case ":$PATH:" in *":$BIN:"*) ;; *)
  if [ "$(uname -s)" = "Darwin" ]; then RC="~/.zshrc"; else RC="~/.bashrc or ~/.zshrc"; fi
  echo "NOTE: $BIN is not in your PATH. Add it:  export PATH=\"\$HOME/.local/bin:\$PATH\"  (put the line in $RC)";; esac
echo "Start:   lumus-demo        (first launch asks: local chat page or terminal; the page walks you through connecting a free model, no keys in files by hand)"
echo "Terminal power use: lumus-demo chrome (brain Chrome), lumus-demo init task.txt --test \"python3 -m unittest discover -s tests\", lumus-demo run"
