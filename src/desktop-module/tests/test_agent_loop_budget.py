"""
Regression test for the agent-loop tool-call budget (core/agent_loop.py).

Background: Unified Idle Mind and workers run the shared agent loop. On cloud,
the model can return a tool_calls array with many
entries AND keep doing so turn after turn, so max_turns alone doesn't bound the
number of executed tool calls — one runaway background cycle fired 50+ search_web
calls. run_agent_loop now enforces two hard ceilings:

  - max_calls_per_turn: at most N calls executed from a single tool_calls array
    (the rest is deferred to the next turn);
  - max_tool_calls: a hard TOTAL across the whole loop, after which further tool
    requests are refused and the model is forced to synthesize a final answer.

Run: python3 tests/test_agent_loop_budget.py
"""
import asyncio
import json
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _install_stubs(calls):
    """Stub the heavy lazy imports run_agent_loop pulls in (tools, the cloud
    LLM router, observability) so the test runs without google/genai/etc."""
    tools_mod = types.ModuleType("tools_and_config.tools")

    def execute_tool(name, args):
        calls["n"] += 1
        return f"result for {name} {args}"

    tools_mod.execute_tool = execute_tool
    sys.modules["tools_and_config.tools"] = tools_mod

    gen_mod = types.ModuleType("core.brain.generate_llm_resp")
    gen_mod.generate = lambda *a, **k: ""
    sys.modules["core.brain.generate_llm_resp"] = gen_mod

    obs_mod = types.ModuleType("core.observability")

    class _Rec:
        def log_step(self, *a, **k):
            pass

    obs_mod.get_recorder = lambda: _Rec()
    obs_mod.observe = lambda *a, **k: (lambda f: f)
    sys.modules["core.observability"] = obs_mod


def _runaway_llm(prompt):
    """Always asks for a 20-call batch and never finishes — the failure mode."""
    batch = [{"tool": "search_web", "args": {"query": f"q{i}-{len(prompt)}"}}
             for i in range(20)]
    return json.dumps({"tool_calls": batch})


async def _run():
    calls = {"n": 0}
    _install_stubs(calls)
    from core.agent_loop import run_agent_loop

    ok, result, *_ = await run_agent_loop(
        "task", llm_fn=_runaway_llm, max_turns=8,
        max_tool_calls=6, max_calls_per_turn=3, label="TEST")

    assert calls["n"] <= 6, f"tool-call budget exceeded: {calls['n']} > 6"
    print(f"PASS: runaway loop bounded at {calls['n']} calls (<= max_tool_calls=6); "
          f"loop ended success={ok}, result={result!r}")


if __name__ == "__main__":
    asyncio.run(_run())
