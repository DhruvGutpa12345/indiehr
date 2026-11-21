#!/usr/bin/env python3
"""How much the helper tools were really used, from Claude Code's session transcripts.

Counts, since a time, the tool calls of the main session and of its agents:

- graphify CLI calls (explain, affected, query, path, …) against grep/rg in Bash and the Grep tool;
- EGM reviewer agents (``egm:*`` agent types);
- Superpowers skills (``superpowers:*``);
- agents by model and effort.

Read-only. Transcripts live outside the repository, in ``~/.claude/projects/<project slug>``;
nothing is written. Used at every ``/run-until`` close and by the weekly upkeep task.

    scripts/tool-adoption.py --since 2026-11-21T00:00
    scripts/tool-adoption.py --since 2026-11-21T00:00 --min-graph-share 0.3   # exit 1 below it
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys

GRAPHIFY_SKIP = re.compile(r"graphify\s+(hook|claude|install|uninstall|extract|update|cluster-only)\b")
GRAPHIFY_USE = re.compile(r"(^|[\s;&|(])graphify\s+\w")
GREP = re.compile(r"(^|[\s;&|(])(grep|rg|egrep)\s")


def default_dir(repo: pathlib.Path) -> pathlib.Path:
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(repo.resolve()))
    return pathlib.Path.home() / ".claude" / "projects" / slug


def count(transcripts: pathlib.Path, since: str) -> dict:
    tools: collections.Counter = collections.Counter()
    agents: collections.Counter = collections.Counter()
    skills: collections.Counter = collections.Counter()
    for path in sorted(transcripts.rglob("*.jsonl")):
        who = "agent" if "subagents" in path.parts else "main"
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"tool_use"' not in line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if (record.get("timestamp") or "") < since:
                    continue
                content = (record.get("message") or {}).get("content")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    name, args = block.get("name"), block.get("input") or {}
                    if name == "Bash":
                        cmd = args.get("command", "")
                        if GRAPHIFY_USE.search(cmd) and not GRAPHIFY_SKIP.search(cmd):
                            tools[f"graphify:{who}"] += 1
                        elif GREP.search(cmd):
                            tools[f"grep:{who}"] += 1
                    elif name == "Grep":
                        tools[f"grep:{who}"] += 1
                    elif name in ("Agent", "Task"):
                        kind = args.get("subagent_type") or "general-purpose"
                        agents[(kind, args.get("model") or "-", args.get("effort") or "-")] += 1
                        if kind.startswith("egm:"):
                            tools["egm-agent"] += 1
                    elif name == "Skill":
                        skill = args.get("skill") or ""
                        skills[skill] += 1
                        if skill.startswith("superpowers:"):
                            tools["superpowers-skill"] += 1
    roles = workflow_roles(transcripts, since)
    graph = tools["graphify:main"] + tools["graphify:agent"]
    grep = tools["grep:main"] + tools["grep:agent"]
    return {
        "tools": dict(tools),
        "graph_share": graph / (graph + grep) if graph + grep else 0.0,
        "agents": {" ".join(k): v for k, v in sorted(agents.items())},
        "skills": dict(skills),
        "roles": roles,
    }


ROLE_ALIASES = {"builder": "build", "fixer": "fix", "verifier": "verify", "reviewer": "review",
                "challenger": "challenge"}


def role_of(label: str) -> str:
    """'review:T' -> 'review', 'fixer-2' -> 'fix', 'challenge:E' -> 'challenge'."""
    base = re.sub(r"[-_ ]?\d+$", "", (label or "?").split(":")[0].strip().lower())
    return ROLE_ALIASES.get(base, base)


def workflow_roles(transcripts: pathlib.Path, since: str) -> dict:
    """Workflow agents since `since`, as {'<role> <model>': {'agents': n, 'output_tokens': t}}.

    A workflow's journal names each agent's label; the agent's own transcript (beside it)
    names the model and the time it started."""
    out: dict = {}
    for journal in transcripts.rglob("journal.jsonl"):
        with journal.open(encoding="utf-8", errors="replace") as fh:
            started = [json.loads(line) for line in fh if '"started"' in line]
        for record in started:
            if record.get("type") != "started":
                continue
            agent = journal.parent / f"agent-{record.get('agentId')}.jsonl"
            if not agent.is_file():
                continue
            model, first, tokens = None, None, 0
            with agent.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if '"assistant"' not in line:
                        continue
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    message = entry.get("message") or {}
                    model = model or message.get("model")
                    first = first or entry.get("timestamp")
                    tokens += (message.get("usage") or {}).get("output_tokens", 0)
            if not model or (first or "") < since:
                continue
            key = f"{role_of(record.get('label', ''))} {model.replace('claude-', '')}"
            slot = out.setdefault(key, {"agents": 0, "output_tokens": 0})
            slot["agents"] += 1
            slot["output_tokens"] += tokens
    return dict(sorted(out.items()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", required=True, help="ISO time in UTC, e.g. 2026-11-21T00:00")
    parser.add_argument("--dir", type=pathlib.Path, help="transcript folder (default: this repo's)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--min-graph-share", type=float, default=None,
                        help="exit 1 when graphify calls / (graphify + grep) is below this")
    args = parser.parse_args(argv)
    folder = args.dir or default_dir(pathlib.Path(__file__).resolve().parent.parent)
    if not folder.is_dir():
        print(f"tool-adoption: no transcripts at {folder}", file=sys.stderr)
        return 2
    result = count(folder, args.since)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        t = result["tools"]
        print(f"since {args.since}")
        print(f"  graphify calls: main {t.get('graphify:main', 0)}, agents {t.get('graphify:agent', 0)}")
        print(f"  grep calls:     main {t.get('grep:main', 0)}, agents {t.get('grep:agent', 0)}")
        print(f"  graph share:    {result['graph_share']:.0%}")
        print(f"  EGM agents: {t.get('egm-agent', 0)}   Superpowers skills: {t.get('superpowers-skill', 0)}")
        for key, value in result["agents"].items():
            print(f"  agent {key}: {value}")
        if result["roles"]:
            print("  workflow agents by role and model (agents, output tokens):")
            for key, value in result["roles"].items():
                print(f"    {key}: {value['agents']}, {value['output_tokens']:,}")
    if args.min_graph_share is not None and result["graph_share"] < args.min_graph_share:
        print(f"tool-adoption: graph share {result['graph_share']:.0%} is below "
              f"{args.min_graph_share:.0%}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
