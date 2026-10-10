#!/usr/bin/env python3
"""PreToolUse hook: route Agent/Task sub-agent calls to a model tier.

Rules
  0. subagent_type not in ROUTED_TYPES         -> leave it alone (custom agents keep their
                                                  frontmatter model; fork ignores `model`).
  1. The brief already names a model           -> leave it alone.
  2. Classifier confidence < MIN_CONF          -> no output (inherit session model).
  3. Tier is "top" (not in TIER_TO_MODEL)      -> no output (inherit session model).
  4. Any error whatsoever                      -> exit 0 silently (pass through).

Otherwise emit hookSpecificOutput.updatedInput with `model` filled in.

Env
  ROUTER_STYLE   anthropic (default) | openai | jev
                 jev = Ollama /v1/systemone typed-decision endpoint (local, no key)
  ROUTER_URL     endpoint URL (defaults per style)
  ROUTER_MODEL   classifier model (default per style: claude-haiku-5-5 / nimble)
  ROUTER_API_KEY key override; else ANTHROPIC_API_KEY / OPENAI_API_KEY,
                 else the contents of ~/.claude/router/api_key
  ROUTER_MIN_CONF confidence threshold (default 0.5)
  ROUTER_TIMEOUT  seconds (default 8)
                  (a non-numeric or non-finite value falls back to the default)
  ROUTER_TYPES    comma-separated subagent_types to route (default general-purpose;
                  a missing subagent_type counts as general-purpose)
"""
import json
import math
import os
import re
import sys
import time
import urllib.request

ROUTER_DIR = os.path.expanduser("~/.claude/router")
LOG_PATH = os.path.join(ROUTER_DIR, "routes.jsonl")

# "top" is intentionally absent: top-tier work inherits the session model (Fable).
TIER_TO_MODEL = {"small": "haiku", "mid": "sonnet"}

STYLE = os.environ.get("ROUTER_STYLE", "anthropic").lower()
DEFAULT_URL = {
    "anthropic": "https://api.anthropic.com/v1/messages",
    "openai": "https://api.openai.com/v1/chat/completions",
    "jev": "http://localhost:11434/v1/systemone",
}
URL = os.environ.get("ROUTER_URL") or DEFAULT_URL.get(STYLE, DEFAULT_URL["anthropic"])
MODEL = os.environ.get("ROUTER_MODEL") or ("nimble" if STYLE == "jev" else "claude-haiku-5-5")


def env_float(name, default):
    # Parsed at import time, outside rule 4's try, so a bad value must not raise.
    try:
        value = float(os.environ.get(name, default))
    except ValueError:
        return default
    return value if math.isfinite(value) else default


MIN_CONF = env_float("ROUTER_MIN_CONF", 0.5)
TIMEOUT = env_float("ROUTER_TIMEOUT", 8.0)
MAX_BRIEF_CHARS = 4000
ROUTED_TYPES = {t.strip() for t in
                os.environ.get("ROUTER_TYPES", "general-purpose").split(",") if t.strip()}

TIER_CRITERIA = {
    "small": "Mechanical, well-specified, low-risk: file lookups, grep/search, summarizing given "
             "text, renaming, formatting, running a known command and reporting output. "
             "Documents: fixing typos or formatting, changing a given value, translating or "
             "reformatting an existing section without changing its meaning.",
    "mid": "Ordinary engineering: implementing a clearly scoped feature or fix, writing tests, "
           "reviewing a bounded diff, routine multi-file changes, research with a clear question. "
           "Documents: drafting a document, guide or worked examples from a clear spec or source "
           "material; updating manuals or test scripts to match described changes; turning notes "
           "into a structured document.",
    "top": "Hard or open-ended: architecture/design decisions, subtle debugging, security analysis, "
           "ambiguous requirements, long multi-step autonomy, anything where a mistake is costly. "
           "Documents: strategy, policy or design documents that need judgment and trade-offs, "
           "compliance or legal assessments, or writing from unclear or undecided requirements.",
}

SYSTEM = """You route sub-agent tasks to a model tier. Reply with ONLY a JSON object:
{"tier": "small" | "mid" | "top", "confidence": <0..1>}

small: mechanical, well-specified, low-risk — file lookups, grep/search, summarizing given text,
       renaming, formatting, running a known command and reporting output.
       Documents: fixing typos or formatting, changing a given value, translating or reformatting
       an existing section without changing its meaning.
mid:   ordinary engineering — implementing a clearly scoped feature or fix, writing tests,
       code review of a bounded diff, multi-file but routine changes, research with a clear question.
       Documents: drafting a document, guide or worked examples from a clear spec or source material;
       updating manuals or test scripts to match described changes; turning notes into a structured
       document.
top:   hard or open-ended — architecture/design decisions, subtle debugging, security analysis,
       ambiguous requirements, long multi-step autonomy, anything where a mistake is costly.
       Documents: strategy, policy or design documents that need judgment and trade-offs,
       compliance or legal assessments, or writing from unclear or undecided requirements.
confidence: how sure you are of the tier. Use < 0.5 when the brief is too vague to judge."""


def api_key():
    for name in ("ROUTER_API_KEY",
                 "ANTHROPIC_API_KEY" if STYLE == "anthropic" else "OPENAI_API_KEY"):
        if os.environ.get(name):
            return os.environ[name]
    path = os.path.join(ROUTER_DIR, "api_key")
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip()
    return None


def post(body, headers):
    headers["content-type"] = "application/json"
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.load(resp)


def confidence(value):
    # json accepts NaN/Infinity, and NaN < MIN_CONF is False, so reject anything odd.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"bad confidence {value!r}")
    if not (math.isfinite(value) and 0 <= value <= 1):
        raise ValueError(f"bad confidence {value!r}")
    return float(value)


def classify_jev(brief):
    data = post({
        "model": MODEL,
        "state": {"brief": brief},
        "questions": {"tier": {
            "type": "choice",
            "instructions": "Which model tier should handle this sub-agent task?",
            "criteria": TIER_CRITERIA,
        }},
    }, {})
    ans = data["answers"]["tier"]
    return str(ans["choice"]).lower(), confidence(ans.get("confidence", 0))


def classify(brief):
    if STYLE == "jev":
        tier, conf = classify_jev(brief)
        if tier not in TIER_CRITERIA:
            raise ValueError(f"bad tier {tier!r}")
        return tier, conf
    key = api_key()
    if not key:
        raise RuntimeError("no API key")
    if STYLE == "openai":
        body = {"model": MODEL, "max_tokens": 50, "temperature": 0,
                "messages": [{"role": "system", "content": SYSTEM},
                             {"role": "user", "content": brief}]}
        headers = {"authorization": f"Bearer {key}"}
    else:
        body = {"model": MODEL, "max_tokens": 50, "system": SYSTEM,
                "messages": [{"role": "user", "content": brief}]}
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    data = post(body, headers)
    if STYLE == "openai":
        text = data["choices"][0]["message"]["content"]
    else:
        text = "".join(b.get("text", "") for b in data["content"])
    out = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
    tier = str(out["tier"]).lower()
    if tier not in ("small", "mid", "top"):
        raise ValueError(f"bad tier {tier!r}")
    return tier, confidence(out.get("confidence", 0))


def log(event):
    try:
        os.makedirs(ROUTER_DIR, exist_ok=True)
        event["ts"] = time.time()
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass


def main():
    payload = json.load(sys.stdin)
    if payload.get("tool_name") not in ("Agent", "Task"):
        return
    tool_input = payload.get("tool_input") or {}
    base = {"session_id": payload.get("session_id"),
            "description": tool_input.get("description", "")[:120]}
    agent_type = tool_input.get("subagent_type") or "general-purpose"

    # Rule 0: only route generic agents.
    if agent_type not in ROUTED_TYPES:
        log({**base, "action": "skipped", "subagent_type": agent_type})
        return

    # Rule 1: explicit model wins.
    if tool_input.get("model"):
        log({**base, "action": "explicit", "model": tool_input["model"]})
        return

    brief = (f"Description: {tool_input.get('description', '')}\n"
             f"Agent type: {agent_type}\n\n"
             f"{tool_input.get('prompt', '')}")[:MAX_BRIEF_CHARS]
    tier, conf = classify(brief)
    model = TIER_TO_MODEL.get(tier)

    # Rules 2 & 3: low confidence or top tier -> inherit session model.
    if conf < MIN_CONF or model is None:
        log({**base, "action": "inherited", "tier": tier, "confidence": conf})
        return

    log({**base, "action": "filled", "tier": tier, "confidence": conf, "model": model})
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
        "permissionDecisionReason": f"model router: {tier} ({conf:.2f}) -> {model}",
        "updatedInput": {**tool_input, "model": model},
    }}))


if __name__ == "__main__":
    # Rule 4: never block, never print on failure.
    try:
        main()
    except Exception as e:
        log({"action": "error", "error": f"{type(e).__name__}: {e}"[:200]})
    sys.exit(0)
