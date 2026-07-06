"""Agent-CLI resolution for `manju auto` (§10 — the autopilot shell).

`manju auto` drives ANY one-shot agent CLI, not just Claude Code. The
underlying collaboration surface is already agent-agnostic (files + CLI +
MCP); this module generalizes the last Claude-specific piece — which binary
gets the composed prompt.

Resolution order (first hit wins):

1. ``--agent`` flag           — a template or a known agent name
2. ``MANJU_AGENT`` env var    — same grammar
3. ``project.yaml: agent:``   — same grammar, recorded per project
4. PATH probe                 — first of the known agents found on PATH

A *template* is a shell-style string containing ``{prompt}`` (e.g.
``claude -p {prompt}``); a bare *name* (``codex``) expands via KNOWN_AGENTS.
A template without ``{prompt}`` gets the prompt appended as one final
argument. Structured integrations should prefer `manju serve-mcp` — this
shell exists for agents that only speak "prompt in, work out".
"""

from __future__ import annotations

import os
import shlex
import shutil

# One-shot prompt invocation per agent CLI, verified against each tool's
# documented non-interactive mode. Bare names in --agent/MANJU_AGENT/
# project.yaml expand through this table; PATH probing walks it in order.
KNOWN_AGENTS: dict[str, str] = {
    "claude": "claude -p {prompt}",          # Claude Code
    "codex": "codex exec {prompt}",          # OpenAI Codex CLI
    "gemini": "gemini -p {prompt}",          # Gemini CLI
    "qwen": "qwen -p {prompt}",              # Qwen Code (gemini-cli lineage)
    "aider": "aider --message {prompt}",     # aider
}


class AgentResolutionError(RuntimeError):
    """No agent CLI could be resolved — carries the full how-to in one line."""


def _expand(spec: str) -> str:
    """A bare known name becomes its template; anything else IS a template."""
    return KNOWN_AGENTS.get(spec.strip(), spec)


def build_command(template: str, prompt: str) -> list[str]:
    """Split the template shell-style and substitute ``{prompt}`` as a WHOLE
    argument (never word-split, never quoted-injected). A template without
    the placeholder gets the prompt appended as the final argument."""
    words = shlex.split(template)
    if not words:
        raise AgentResolutionError("agent template is empty")
    if any("{prompt}" in w for w in words):
        return [w.replace("{prompt}", prompt) if "{prompt}" in w else w
                for w in words]
    return [*words, prompt]


def resolve_agent(project_agent: str | None, flag: str | None) -> str:
    """Return the agent TEMPLATE per the resolution order, or raise with a
    one-line message naming every way to configure one."""
    for spec in (flag, os.environ.get("MANJU_AGENT"), project_agent):
        if spec and spec.strip():
            return _expand(spec)
    for name, template in KNOWN_AGENTS.items():
        if shutil.which(name):
            return template
    raise AgentResolutionError(
        "no agent CLI found — install one of "
        + "/".join(KNOWN_AGENTS)
        + ", or set --agent/MANJU_AGENT/project.yaml:agent to a template like "
        + '"claude -p {prompt}" (structured integrations: manju serve-mcp)'
    )
