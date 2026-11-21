"""Tests for scripts/tool-adoption.py (synthetic transcripts only)."""

import importlib.util
import json
import pathlib
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("tool_adoption", HERE / "tool-adoption.py")
tool_adoption = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool_adoption)


def use(name, ts, **inp):
    return json.dumps({"timestamp": ts, "message": {"content": [
        {"type": "tool_use", "name": name, "input": inp}]}}) + "\n"


class Counts(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        (self.dir / "subagents").mkdir()
        main = [
            use("Bash", "2026-11-22T01:00", command="graphify explain end_grant"),
            use("Bash", "2026-11-22T01:01", command="cd x && grep -rn foo plugins"),
            use("Bash", "2026-11-22T01:02", command="graphify hook status"),  # upkeep, not use
            use("Bash", "2026-11-20T01:00", command="grep old"),  # before --since
            use("Skill", "2026-11-22T01:03", skill="superpowers:systematic-debugging"),
            use("Agent", "2026-11-22T01:04", subagent_type="egm:egm-design", model="opus", effort="high"),
        ]
        agent = [
            use("Bash", "2026-11-22T02:00", command="graphify affected \"seal_once\""),
            use("Grep", "2026-11-22T02:01", pattern="x"),
            use("Bash", "2026-11-22T02:02", command="rg -n seal plugins"),
        ]
        (self.dir / "main.jsonl").write_text("".join(main))
        (self.dir / "subagents" / "a.jsonl").write_text("".join(agent))

    def test_counts_since_and_by_who(self):
        r = tool_adoption.count(self.dir, "2026-11-21T00:00")
        t = r["tools"]
        self.assertEqual(t.get("graphify:main"), 1)
        self.assertEqual(t.get("graphify:agent"), 1)
        self.assertEqual(t.get("grep:main"), 1)
        self.assertEqual(t.get("grep:agent"), 2)
        self.assertEqual(t.get("egm-agent"), 1)
        self.assertEqual(t.get("superpowers-skill"), 1)
        self.assertAlmostEqual(r["graph_share"], 2 / 5)
        self.assertEqual(r["agents"], {"egm:egm-design opus high": 1})

    def test_workflow_roles_by_model(self):
        wf = self.dir / "subagents" / "workflows" / "wf_x"
        wf.mkdir(parents=True)
        (wf / "journal.jsonl").write_text(
            json.dumps({"type": "started", "agentId": "a1", "label": "challenge:T"}) + "\n"
            + json.dumps({"type": "started", "agentId": "a2", "label": "fixer"}) + "\n"
            + json.dumps({"type": "started", "agentId": "a3", "label": "review:E"}) + "\n")

        def agent(model, ts, out):
            return json.dumps({"type": "assistant", "timestamp": ts, "message": {
                "model": model, "usage": {"output_tokens": out}}}) + "\n"
        (wf / "agent-a1.jsonl").write_text(agent("claude-sonnet-5-5", "2026-11-22T03:00", 100))
        (wf / "agent-a2.jsonl").write_text(agent("claude-opus-5-5", "2026-11-22T03:01", 250))
        (wf / "agent-a3.jsonl").write_text(agent("claude-opus-5-5", "2026-11-19T03:00", 999))  # too old
        roles = tool_adoption.count(self.dir, "2026-11-21T00:00")["roles"]
        self.assertEqual(roles, {
            "challenge sonnet-5-5": {"agents": 1, "output_tokens": 100},
            "fix opus-5-5": {"agents": 1, "output_tokens": 250},
        })

    def test_min_share_fails_below(self):
        self.assertEqual(tool_adoption.main(
            ["--since", "2026-11-21T00:00", "--dir", str(self.dir), "--min-graph-share", "0.5"]), 1)
        self.assertEqual(tool_adoption.main(
            ["--since", "2026-11-21T00:00", "--dir", str(self.dir), "--min-graph-share", "0.3"]), 0)


if __name__ == "__main__":
    unittest.main()
