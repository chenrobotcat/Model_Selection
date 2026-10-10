#!/usr/bin/env bash
# Install the Claude Code model router into ~/.claude (idempotent).
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
SETTINGS="$CLAUDE_DIR/settings.json"

mkdir -p "$CLAUDE_DIR/hooks" "$CLAUDE_DIR/router"
cp "$SRC/route_model.py" "$CLAUDE_DIR/hooks/route_model.py"
cp "$SRC/route_stats.py" "$CLAUDE_DIR/router/route_stats.py"
chmod +x "$CLAUDE_DIR/hooks/route_model.py" "$CLAUDE_DIR/router/route_stats.py"
echo "Copied hook and stats script into $CLAUDE_DIR"

# Merge the PreToolUse hook into settings.json, replacing any earlier router entry.
# Back up only on first install, so the backup stays a pre-router copy for uninstalling.
BACKUP="$SETTINGS.bak-router"
if [ -f "$SETTINGS" ] && [ ! -e "$BACKUP" ]; then
  cp "$SETTINGS" "$BACKUP"
  echo "Backed up $SETTINGS to $BACKUP"
fi
python3 - "$SETTINGS" "$SRC/settings.hooks.json" <<'EOF'
import json, os, sys
path, new_path = sys.argv[1], sys.argv[2]
settings = json.load(open(path)) if os.path.exists(path) else {}
hooks = settings.setdefault("hooks", {})
for event, entries in json.load(open(new_path))["hooks"].items():
    current = [e for e in hooks.get(event, [])
               if not any("route_model.py" in h.get("command", "") for h in e.get("hooks", []))]
    hooks[event] = current + entries
with open(path, "w") as f:
    json.dump(settings, f, indent=2, ensure_ascii=False)
    f.write("\n")
EOF
echo "Merged hook into $SETTINGS"

# Tell the orchestrator to delegate without naming a model.
MARKER="# Sub-agent delegation (model router)"
if ! grep -qF "$MARKER" "$CLAUDE_DIR/CLAUDE.md" 2>/dev/null; then
  cat >> "$CLAUDE_DIR/CLAUDE.md" <<EOF

$MARKER
Delegate independent sub-tasks with the Agent tool and do not set \`model\` in the brief;
the model router hook picks haiku / sonnet by difficulty, and hard tasks inherit the session model.
EOF
  echo "Added delegation instruction to $CLAUDE_DIR/CLAUDE.md"
fi

if command -v ollama >/dev/null 2>&1; then
  echo "Tip: make sure the classifier model is pulled: ollama pull nimble"
else
  echo "Warning: ollama not found. Install Ollama >= 0.35 or change ROUTER_STYLE in settings.hooks.json."
fi
