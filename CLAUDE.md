# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Claude Code `PreToolUse` hook that picks a model for each sub-agent. For every `Agent`/`Task` call it
classifies the brief as small / mid / top. It then returns `hookSpecificOutput.updatedInput` with
`model` set (small → `haiku`, mid → `sonnet`), or prints nothing so the sub-agent inherits the session
model. Everything is Python standard library only, with no dependencies, build step or test suite.

## Repo vs. deployed copy

The repo is the source. Claude Code runs the **installed copies**: `~/.claude/hooks/route_model.py`,
`~/.claude/router/route_stats.py`, and the hook entry in `~/.claude/settings.json`. After editing,
run `./install.sh` (idempotent) to deploy. The hook script is re-executed on every Agent call, so a
deployed change takes effect immediately, even in running sessions.

## Testing

Test by piping a hook payload into the script. Set `HOME` to a scratch directory so the run doesn't
write to the real log at `~/.claude/router/routes.jsonl`:

```bash
echo '{"tool_name":"Agent","tool_input":{"description":"x","prompt":"list files in src/"}}' \
  | ROUTER_STYLE=jev HOME=/path/to/scratch python3 route_model.py
cat /path/to/scratch/.claude/router/routes.jsonl
```

- jev mode needs a local Ollama server (≥ 0.35) with `nimble` pulled (`ollama pull nimble`).
- For deterministic rule tests, point `ROUTER_URL` at a stub HTTP server and set `ROUTER_API_KEY=x`.
  An anthropic-style stub returns `{"content":[{"type":"text","text":"{\"tier\":\"small\",\"confidence\":0.9}"}]}`.
- To test `install.sh` without touching the real config, run `CLAUDE_DIR=/path/to/scratch ./install.sh`.
- Stats: `python3 route_stats.py 7` (reads `$HOME/.claude/router/routes.jsonl`).

## Design invariants

- **Rule order in `main()`:** 0 skip non-routed `subagent_type`, then 1 explicit `model` wins, then
  classify, then 2/3 low confidence or `top` → no output. Rule 4: `__main__` catches every exception,
  logs it and exits 0 with **no stdout**. Any stdout is parsed by Claude Code, so never print debug
  output. The hook must never block a call.
- **`top` is deliberately absent from `TIER_TO_MODEL`.** A missing mapping means "inherit the session
  model". That is how rule 3 works, so don't "fix" it by adding a default.
- **`updatedInput` replaces the whole tool input**, so it is `{**tool_input, "model": ...}`, not just
  the changed field.
- **Rule 0 exists because the Agent tool's `model` parameter overrides an agent definition's frontmatter
  `model`.** Routing a custom agent would silently clobber its configured model. `fork` ignores `model`.
  Only types in `ROUTER_TYPES` (default `general-purpose`; a missing type counts as that) are routed.
- **Three classifier styles share one `classify()`:** `jev` (Ollama `/v1/systemone`, a typed `choice`
  question built from `TIER_CRITERIA`, which reads `answers.tier.choice` / `.confidence`), `anthropic`
  and `openai` (both prompt with `SYSTEM` and regex out a JSON object). `TIER_CRITERIA` and `SYSTEM` both
  describe the tiers, so keep them in sync. The script defaults to `anthropic`; the shipped
  `settings.hooks.json` sets `ROUTER_STYLE=jev` in the hook command.
- **Log schema is shared with `route_stats.py`:** `action` ∈ {`filled`, `inherited`, `explicit`,
  `skipped`, `error`} plus `tier`, `confidence`, `model`, `subagent_type`, `session_id`, `ts`. Update
  both files when you add an action or field.
- **`install.sh` is idempotent.** It replaces any existing hook entry whose command contains
  `route_model.py` and appends to `CLAUDE.md` only if the `# Sub-agent delegation (model router)`
  marker is missing. It backs up `settings.json` to `settings.json.bak-router` only if no backup
  exists, so the backup stays a pre-router copy for uninstalling. Keep these checks if you change the
  hook command or that text.
