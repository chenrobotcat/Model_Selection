# Claude Code Model Router

A `PreToolUse` hook for [Claude Code](https://claude.com/claude-code) that picks a model for each
sub-agent automatically. Before every `Agent` (formerly `Task`) tool call, it sends the brief to a
small, cheap classifier, asks which difficulty tier the task belongs to, and writes the matching
`model` back through `updatedInput`.

By default the classifier is a local Jev-style decision model served by Ollama, so routing is free and
takes about 0.3 s per call.

## How it works

```
Orchestrator calls the Agent tool
   │  tool_input = {description, prompt, subagent_type, model?}
   ▼
PreToolUse hook (matcher: Agent|Task) → route_model.py
   ├─ Rule 0: subagent_type not routed (custom agent, fork)  → pass through
   ├─ Rule 1: brief already sets `model`                      → pass through
   ├─ classify the brief → {tier: small | mid | top, confidence}
   ├─ Rule 2: confidence < 0.5                                → inherit session model
   ├─ Rule 3: tier == top                                     → inherit session model
   └─ small → haiku, mid → sonnet                             → emit updatedInput
   (Rule 4: any error → exit 0 with no output, so the call proceeds unchanged)
```

| Rule | Condition | Behavior | Why |
|---|---|---|---|
| 0 | `subagent_type` not in `ROUTER_TYPES` | Untouched | Custom agents keep their frontmatter `model`; `fork` ignores `model` anyway |
| 1 | Brief already sets `model` | Untouched | An explicit choice by you or the orchestrator wins |
| 2 | Confidence below `ROUTER_MIN_CONF` | Inherit session model | Never downgrade when unsure |
| 3 | Tier is `top` | Inherit session model | Hard work stays on your strongest model |
| 4 | Any error (Ollama down, timeout, bad response) | Silent pass-through | The hook never blocks work or pollutes context |

`top` is deliberately missing from `TIER_TO_MODEL`, so top-tier tasks run on whatever model the
session uses (for example `claude --model fable`).

Every decision is appended to `~/.claude/router/routes.jsonl`. `route_stats.py` summarizes it.

## Files

| File | Purpose |
|---|---|
| `route_model.py` | The hook. Installed to `~/.claude/hooks/` |
| `route_stats.py` | Log summary. Installed to `~/.claude/router/` |
| `settings.hooks.json` | The `hooks` block that is merged into `~/.claude/settings.json` |
| `install.sh` | Installs everything above (idempotent) |

## Requirements

- Claude Code with `updatedInput` support for `PreToolUse` hooks (tested on 2.1.296)
- Python 3.8+ (standard library only)
- One classifier backend:
  - **jev (default):** [Ollama](https://ollama.com) server ≥ 0.35, which provides `/v1/systemone`.
    See [Ollama now supports Jev-style decision models](https://ollama.com/blog/ollama-now-supports-jev-style-decision-models).
  - **anthropic:** an `ANTHROPIC_API_KEY`
  - **openai:** any OpenAI-compatible endpoint plus a key

## Deployment

### 1. Get the code

```bash
git clone https://github.com/chenrobotcat/Model_Selection.git
cd Model_Selection
```

### 2. Prepare the classifier

```bash
# Default backend: local Ollama
ollama serve            # skip if Ollama already runs as an app or service
ollama pull nimble      # 9B decision model; tev1 (4B) and tev1:0.8b are smaller options
```

Check that the endpoint answers:

```bash
curl -s http://localhost:11434/v1/systemone -d '{
  "model": "nimble",
  "state": {"brief": "grep the repo for parse_config"},
  "questions": {"tier": {"type": "choice", "instructions": "Which tier?",
    "criteria": {"small": "mechanical", "mid": "routine engineering", "top": "hard"}}}}'
```

### 3. Install the hook

```bash
./install.sh
```

The script:

1. Copies `route_model.py` to `~/.claude/hooks/` and `route_stats.py` to `~/.claude/router/`
2. Backs up `~/.claude/settings.json` to `settings.json.bak-router`, then merges in the `PreToolUse`
   hook from `settings.hooks.json` (an earlier router entry is replaced, not duplicated)
3. Appends a delegation instruction to `~/.claude/CLAUDE.md`, so the orchestrator delegates
   sub-tasks without naming a model

<details>
<summary>Manual installation</summary>

```bash
mkdir -p ~/.claude/hooks ~/.claude/router
cp route_model.py ~/.claude/hooks/
cp route_stats.py ~/.claude/router/
```

Merge the `hooks` block from `settings.hooks.json` into `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Agent|Task",
        "hooks": [
          {
            "type": "command",
            "command": "ROUTER_STYLE=jev python3 ~/.claude/hooks/route_model.py",
            "timeout": 15
          }
        ]
      }
    ]
  }
}
```

Add this to `~/.claude/CLAUDE.md`:

```markdown
Delegate independent sub-tasks with the Agent tool and do not set `model` in the brief.
```

</details>

### 4. Set the session model (optional)

Top-tier and low-confidence tasks inherit the session model. To make that your strongest model:

```bash
claude --model fable      # or /model inside a session, or "model" in settings.json
```

### 5. Verify

```bash
# Test the hook directly; prints updatedInput with "model": "haiku"
echo '{"tool_name":"Agent","tool_input":{"description":"list","prompt":"list files in src/"}}' \
  | ROUTER_STYLE=jev python3 ~/.claude/hooks/route_model.py
```

Then, in a Claude Code session, have it delegate something and check the log:

```bash
tail -n 1 ~/.claude/router/routes.jsonl   # should show this session's session_id
```

`claude --debug` also shows whether the hook fires.

**When changes take effect:** running sessions pick up `settings.json` changes and every hook call
re-runs the script, so the hook works in existing sessions. `CLAUDE.md` is read only when a session
starts, so the delegation instruction needs a new session.

### Updating

```bash
git pull && ./install.sh
```

### Uninstalling

Remove the router entry from `hooks.PreToolUse` in `~/.claude/settings.json` (or restore
`settings.json.bak-router`), delete the "Sub-agent delegation (model router)" section from
`~/.claude/CLAUDE.md`, then:

```bash
rm ~/.claude/hooks/route_model.py
rm -r ~/.claude/router
```

## Configuration

Set these as environment variables in the hook `command` (for example
`ROUTER_STYLE=jev ROUTER_MIN_CONF=0.7 python3 ~/.claude/hooks/route_model.py`).

| Variable | Default | Description |
|---|---|---|
| `ROUTER_STYLE` | `anthropic` (`jev` in `settings.hooks.json`) | `jev`, `anthropic` or `openai` |
| `ROUTER_URL` | Per style | Classifier endpoint |
| `ROUTER_MODEL` | `nimble` (jev), otherwise `claude-haiku-5-5` | Classifier model |
| `ROUTER_API_KEY` | — | Key override; otherwise `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`, then `~/.claude/router/api_key`. Not needed for jev |
| `ROUTER_MIN_CONF` | `0.5` | Below this, the session model is kept. Raise it to downgrade less often |
| `ROUTER_TIMEOUT` | `8` | Classifier timeout in seconds |
| `ROUTER_TYPES` | `general-purpose` | Comma-separated `subagent_type`s to route. Add only agents without a frontmatter `model` |

To change the tier definitions or the tier-to-model mapping, edit `TIER_CRITERIA` and `TIER_TO_MODEL`
in `route_model.py` (for example, add `"top": "fable"` to pin top-tier tasks), then re-run
`./install.sh`.

## Statistics

```bash
python3 ~/.claude/router/route_stats.py 7    # last 7 days
```

It shows counts per action (`filled`, `inherited`, `explicit`, `skipped`, `error`), the small / mid /
top distribution, which models were set, and grouped errors. A large `error` count usually means
Ollama is not running.
