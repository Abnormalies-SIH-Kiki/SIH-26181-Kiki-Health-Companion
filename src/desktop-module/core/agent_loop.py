"""
Shared multi-turn agent loop for every autonomous JSON-protocol agent in
KikiFast: Unified Idle Mind, background workers, and self-extend.

Extracted from core/workers/worker_brain.py so the engine has a clear home of its
own, independent of the worker subsystem. Workers, Unified Idle Mind, and self-extend
all import run_agent_loop from here (or via the back-compat re-export still kept
in worker_brain). The loop is tool-agnostic: it runs whatever tools.execute_tool
exposes, so ANY tool is automatically available to brain / worker / idle agents.
"""

import asyncio
import json
from typing import Optional

def _extract_json_object(text: str) -> Optional[dict]:
    """Pull the agent-protocol JSON object out of an LLM response that may wrap
    it in prose and/or a code fence.

    The small local model frequently emits a lead-in line before the JSON —
    some agent prompts even ask for one (e.g. "CURIOUS: ...\\n{...}"). The
    old parser only stripped ``` fences, so any bare prose prefix made
    json.loads fail at column 1 and kicked off a useless "escape your quotes"
    retry loop. This finds the first BALANCED top-level {...} object (respecting
    strings/escapes) and parses it, regardless of surrounding text.

    Returns the parsed dict, or None if no valid JSON object is found.
    """
    if not text:
        return None

    candidates = []
    # 1. Fenced block first (most explicit) — try ```json then any ``` fence.
    if "```json" in text:
        try:
            candidates.append(text.split("```json", 1)[1].split("```", 1)[0])
        except IndexError:
            pass
    if "```" in text:
        parts = text.split("```")
        if len(parts) >= 3:
            candidates.append(parts[1])
    # 2. The whole thing (covers a clean JSON-only response).
    candidates.append(text)

    for cand in candidates:
        cand = cand.strip()
        if not cand:
            continue
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

    # 3. Last resort: scan for the first balanced {...} object embedded in prose.
    for src in candidates:
        obj = _scan_balanced_object(src)
        if obj is not None:
            return obj
    return None


def _scan_balanced_object(text: str) -> Optional[dict]:
    """Find and parse the first balanced JSON object in `text`, tracking string
    state so braces inside string literals don't throw off the depth count."""
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    chunk = text[start:i + 1]
                    try:
                        obj = json.loads(chunk)
                        if isinstance(obj, dict):
                            return obj
                    except json.JSONDecodeError:
                        break  # malformed from this start; try the next {
        start = text.find("{", start + 1)
    return None



# Max LLM turns per worker execution (prevent infinite loops)
MAX_LLM_TURNS = 10

# Hard tool-call ceilings (prevent runaway research loops). On cloud the model
# can return a tool_calls array with many entries AND keep doing so turn after
# turn — turn limits alone don't bound the number of executed calls (seen live:
# a background agent that fired 50+ search_web calls). 0 = unlimited.
DEFAULT_MAX_TOOL_CALLS = 0          # total across the whole loop (caller-set)
DEFAULT_MAX_CALLS_PER_TURN = 5      # safety net: cap a single mega-batch turn
# How many extra wrap-up nudges to give a model that keeps requesting tools
# after the budget is spent before we bail with what we have.
_MAX_FORCED_WRAPS = 2

# --- Context budget (chars, ~3.5 chars/token) ---
# The local box has a hard 7k-token context; the agent prompt + tool results
# must stay well under it or the request errors out. These are conservative
# defaults; callers may pass tighter budgets.
MAX_PROMPT_CHARS = 20000
MAX_TOOL_RESULT_CHARS = 3000


def _truncate_middle(text: str, max_chars: int) -> str:
    """Keep the head and tail of an oversized blob (search results usually have
    the good stuff at the top, error context at the bottom)."""
    if len(text) <= max_chars:
        return text
    head = int(max_chars * 0.7)
    tail = max_chars - head
    return f"{text[:head]}\n…[{len(text) - max_chars} chars compressed]…\n{text[-tail:]}"


def _compact_conversation(conversation: list[str], max_chars: int, label: str) -> str:
    """
    Smart context compression for the agent loop.

    The joined conversation must fit the local box's context. Priorities:
      1. conversation[0] (the task prompt with ALL tool/JSON instructions —
         the small voice model gets lost without them) is kept VERBATIM.
      2. The most recent 2 exchanges are kept (current working state).
      3. Older middle entries (stale tool results) are squeezed to short stubs.
    """
    joined = "\n\n---\n\n".join(conversation)
    if len(joined) <= max_chars or len(conversation) <= 3:
        return joined
    head = conversation[0]
    middle = conversation[1:-2]
    recent = conversation[-2:]
    # Even squeezed, each middle entry still costs ~220 chars (150-char stub +
    # compression marker + separator) — budget for that floor up front.
    middle_floor = len(middle) * 220
    budget_middle = max_chars - len(head) - sum(len(r) for r in recent) - 200
    if budget_middle < middle_floor:
        # head + recent + squeezed middle bust the budget (long research hits
        # this: big task prompt, many turns). The head must stay verbatim —
        # clamp the recent entries instead so the box's 7k ctx is never
        # exceeded.
        per_recent = max(700, (max_chars - len(head) - middle_floor - 200) // 2)
        recent = [_truncate_middle(r, per_recent) for r in recent]
        budget_middle = max_chars - len(head) - sum(len(r) for r in recent) - 200
    budget_middle = max(0, budget_middle)
    per_entry = max(150, budget_middle // max(1, len(middle)))
    squeezed = [
        e if len(e) <= per_entry
        else e[:per_entry] + " …[older step compressed; key facts above]"
        for e in middle
    ]
    out = "\n\n---\n\n".join([head] + squeezed + recent)
    print(f"[{label}] 🗜 Compressed agent context {len(joined)} → {len(out)} chars "
          f"(budget {max_chars}).")
    return out


async def run_agent_loop(prompt: str, llm_fn=None, max_turns: int = MAX_LLM_TURNS,
                         label: str = "WorkerBrain", stop_event=None,
                         min_tool_calls: int = 0,
                         max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
                         max_calls_per_turn: int = DEFAULT_MAX_CALLS_PER_TURN,
                         max_prompt_chars: int = MAX_PROMPT_CHARS,
                         max_tool_result_chars: int = MAX_TOOL_RESULT_CHARS,
                         continue_guidance_fn=None,
                         session_id=None, tool_executor=None, tool_guard_fn=None,
                         progress_fn=None,
                         verification_tools: tuple = (),
                         allow_unverified_finish: bool = False
                         ) -> tuple[bool, str, str | None, dict | None, list[str]]:
    """
    Reusable multi-turn LLM + tools agent loop (the execution engine shared by
    workers and Unified Idle Mind).

    - `prompt`: the full task prompt (must describe the JSON tool_calls/status
      protocol — see execute_worker's prompt for the canonical shape).
    - `llm_fn`: callable(prompt_text) -> str. Defaults to the multi-provider
      reasoning router (generate_llm purpose="reasoning").
    - `stop_event`: optional threading.Event; checked between turns so the loop
      can be interrupted when a caller's policy requires it.
    - `min_tool_calls`: minimum number of tool calls that MUST be made before
      the agent is allowed to complete/fail.
    - `max_tool_calls`: HARD ceiling on the total number of executed tool calls
      across the whole loop (0 = unlimited). Once reached, further tool requests
      are refused and the model is forced to synthesize a final answer from what
      it already gathered — this is what stops runaway search loops (the model
      otherwise batches large tool_calls arrays turn after turn).
    - `max_calls_per_turn`: cap on how many tool calls a SINGLE turn may execute
      (0 = unlimited). Stops one turn from firing a 20-call mega-batch; the
      remainder is deferred to the next turn (and counted against the total).
    - `continue_guidance_fn`: optional callable(total_tool_calls, tools_used) -> str. When
      given, its return value replaces the generic "Continue with your task"
      tail appended after each round of tool results — lets callers coach the
      model turn-by-turn.
    - `verification_tools`: tool names the `min_tool_calls` nudge should offer
      the model. Without them the nudge can only say "use a tool (like
      search_web)", which is useless advice for a task whose real verification
      tool is `get_care_plan` — see `_force_tool_call`.
    - `allow_unverified_finish`: on the FINAL turn only, accept a completion
      that made no tool call instead of exhausting the loop. The returned
      final_json carries `unverified: True` so the caller can refuse to present
      it as a confirmed action. Off by default — a caller must opt in.

    - `progress_fn`: optional callable(phase, detail) used to narrate live what
      the agent is doing — `phase` is one of "thinking" / "tool" / "done", and
      `detail` is a tool name (for "tool") or a short summary (for "done"). It
      drives the OLED status line; failures are swallowed so it never affects
      the run.

    Returns (success, result_text, speak_text_or_None, final_json_or_None, tools_used).
    final_json is the parsed terminal JSON object (callers can read extra fields
    like persistence fields from it).
    """
    from core.brain.generate_llm_resp import generate as generate_llm
    from tools_and_config.tools import execute_tool
    from core.observability import get_recorder
    _rec = get_recorder()   # session_id is None for callers that don't observe

    def _progress(phase, detail=""):
        if progress_fn is None:
            return
        try:
            progress_fn(phase, detail)
        except Exception:
            pass

    if llm_fn is None:
        llm_fn = lambda p: generate_llm(p, thinking_level="HIGH", purpose="reasoning")

    loop = asyncio.get_running_loop()
    conversation = [prompt]
    tools_used = []          # unique tool names (for reporting)
    total_tool_calls = 0     # every executed call (min_tool_calls counts THESE)
    seen_call_sigs = set()   # exact (tool, args) repeats are short-circuited
    calls_log = []           # compact action history — survives compression
    forced_wraps = 0         # wrap-up nudges sent after the budget is spent
    last_nudged_response = None   # spot a model that just repeats itself
    unverified_finish = False     # completed on the last turn with no tool call

    def _force_tool_call(kind: str, response_text: str, turn_index: int) -> bool:
        """Handle a model that tried to finish before `min_tool_calls`.

        Returns True when the caller should retry the turn, False when the
        finish is allowed to stand (`unverified_finish` is then set).

        The nudge used to be one fixed sentence that named `search_web` and was
        appended verbatim on every rejection. For a task whose real verification
        tool is something else entirely the advice is unusable, so the model
        re-emitted the identical JSON until the turn budget ran out and a good
        answer was discarded as a hard failure. Live case, 2026-08-28 23:35:
        "Did I drink water recently?" burned all six turns and Kiki spoke "that
        care action did not complete" instead of the answer it already had.
        So: name the tools this task actually has, notice a verbatim repeat, and
        on the final turn stop destroying the answer.
        """
        nonlocal last_nudged_response, unverified_finish
        last_turn = turn_index >= max_turns - 1
        if last_turn and allow_unverified_finish:
            unverified_finish = True
            print(f"[{label}] {kind} without a tool call on the final turn — "
                  f"accepting it as UNVERIFIED rather than discarding the answer.")
            return False

        repeated = response_text is not None and response_text == last_nudged_response
        last_nudged_response = response_text
        menu = ", ".join(verification_tools) if verification_tools else \
            "the tools listed above"
        print(f"[{label}] {kind} without making required tool calls. "
              f"Forcing tool call{' (repeated response)' if repeated else ''}.")
        if repeated:
            msg = ("SYSTEM ERROR:\nYou sent the SAME answer again without calling "
                   "a tool. Repeating it verifies nothing. Do NOT restate it. Pick "
                   f"exactly ONE tool from: {menu} — whichever would confirm or "
                   "refute what you just claimed — and emit ONLY a JSON tool_calls "
                   "object for it now.")
        else:
            msg = (f"SYSTEM ERROR:\nYou {kind} without checking anything with a "
                   "tool. What you have above is context you were handed, not "
                   "something you verified; answering from it alone is how a wrong "
                   "or invented answer ends up spoken out loud. Call the tool that "
                   f"actually checks this. Available here: {menu}. Emit a JSON "
                   "tool_calls object.")
        if last_turn:
            msg += ("\nThis is your LAST turn — make the tool call now or the "
                    "whole request fails.")
        conversation.append(msg)
        return True

    for turn in range(max_turns):
        if stop_event is not None and stop_event.is_set():
            print(f"[{label}] Stopped between turns (interrupt).")
            return False, "Interrupted", None, None, tools_used

        print(f"[{label}] LLM turn {turn + 1}/{max_turns}")
        _progress("thinking", "reasoning" if turn == 0 else f"reasoning (step {turn + 1})")

        prompt_text = _compact_conversation(conversation, max_prompt_chars, label)
        try:
            response = await loop.run_in_executor(None, lambda: llm_fn(prompt_text))
        except Exception as e:
            print(f"[{label}] LLM call failed: {e}")
            return False, f"LLM error: {e}", None, None, tools_used

        if not response:
            print(f"[{label}] LLM returned empty response (preempted or failed)")
            if session_id:
                _rec.log_step(session_id, "llm", turn=turn + 1,
                              prompt=prompt_text, response="(empty / preempted)")
            return False, "LLM returned empty response", None, None, tools_used

        print(f"[{label}] LLM response: {response[:300]}...")
        if session_id:
            _rec.log_step(session_id, "llm", turn=turn + 1,
                          prompt=prompt_text, response=response)

        # Parse response. The model often wraps the protocol JSON in prose
        # (some agents ask for a short lead-in line), so we
        # extract the first balanced {...} object rather than assuming the whole
        # response is JSON — otherwise a bare prose prefix fails at column 1 and
        # triggers a useless "escape your quotes" retry loop.
        parsed = _extract_json_object(response)
        if parsed is None:
            _json_error = "no JSON object found in response"
            # Truncated final JSON (token limit hit mid-answer) is NOT a
            # quote-escaping problem: telling the model to "fix quotes" sends
            # it back to re-researching in a loop. Ask for a compact re-emit.
            stripped = response.rstrip().rstrip("`").rstrip()
            if '"status"' in response and (
                    "Unterminated string" in str(_json_error)
                    or not stripped.endswith("}")):
                print(f"[{label}] Final JSON truncated by token limit — asking for a shorter re-emit.")
                conversation.append(
                    "SYSTEM ERROR:\nYour last response was CUT OFF mid-JSON by the length limit. "
                    "Do NOT call any tools. Re-send ONLY the final JSON object now, much more compact: "
                    "keep journal.details under 600 characters in plain English (no long quotes, no "
                    "foreign-language text). Make sure it ends with the closing }."
                )
                continue
            # If the response clearly looks like an attempt at JSON mapping (has tool_calls or status),
            # but failed to parse (usually due to unescaped quotes), feed the error back!
            if "status" in response or "tool_calls" in response or "{" in response:
                print(f"[{label}] JSON Decode Error. Feeding back to LLM to fix.")
                error_msg = f"Your response was invalid JSON. Ensure all quotes inside python code strings are properly escaped (use \\\" instead of \"). Error details: {_json_error}"
                conversation.append(f"SYSTEM ERROR:\n{error_msg}\n\nPlease output valid JSON.")
                continue

            # Response is plain text — treat as final result only if no JSON structures exist
            if (min_tool_calls > 0 and total_tool_calls < min_tool_calls
                    and _force_tool_call("returned plain text", response, turn)):
                continue

            print(f"[{label}] Non-JSON response, treating as completion")
            return True, response, None, None, tools_used

        # A response that requests tools AND declares an outcome in the same
        # object has not achieved that outcome — the actions it is describing
        # have not run yet. Reading the status first silently discarded the
        # tool calls, which is how Kiki came to say "मैंने आपके केयर प्लान में
        # गर्दन की कसरत जोड़ दिया है" one millisecond after emitting an
        # update_care_plan call that never executed. The plan was never
        # written and nothing fired at 18:40.
        #
        # So: run the tools, then let the model declare an outcome next turn,
        # once the results in front of it are real. max_turns/max_tool_calls
        # still bound the loop, and a model that keeps re-claiming completion
        # just spends its budget instead of lying about finished work.
        if (parsed.get("status") in ("completed", "failed")
                and parsed.get("tool_calls")):
            claimed = parsed.get("status")
            pending_count = len(parsed.get("tool_calls") or [])
            print(f"[{label}] ⚠ status={claimed} arrived with {pending_count} "
                  f"unexecuted tool call(s) — running them first; the claim is "
                  f"not true yet.")
            parsed = dict(parsed)
            parsed.pop("status", None)

        # Check if completed
        if parsed.get("status") == "completed":
            if (min_tool_calls > 0 and total_tool_calls < min_tool_calls
                    and _force_tool_call("tried to complete", response, turn)):
                continue
            final_result = parsed.get("summary", "Task completed successfully")
            speak_text = parsed.get("speak_text") if parsed.get("speak") else None
            if unverified_finish:
                parsed["unverified"] = True
            _progress("done", final_result)
            print(f"[{label}] Task completed: {final_result}")
            if speak_text:
                print(f"[{label}] Will speak: {speak_text[:100]}")
            return True, final_result, speak_text, parsed, tools_used

        if parsed.get("status") == "failed":
            if (min_tool_calls > 0 and total_tool_calls < min_tool_calls
                    and _force_tool_call("tried to fail", response, turn)):
                continue
            reason = parsed.get("reason", "Unknown failure")
            speak_text = parsed.get("speak_text") if parsed.get("speak") else None
            print(f"[{label}] Task failed: {reason}")
            return False, reason, speak_text, parsed, tools_used

        # Handle tool calls
        tool_calls = parsed.get("tool_calls", [])
        if not tool_calls:
            if (min_tool_calls > 0 and total_tool_calls < min_tool_calls
                    and _force_tool_call("tried to finish", response, turn)):
                continue
            # Completed without explicit status
            if unverified_finish:
                parsed["unverified"] = True
            return True, response, None, parsed, tools_used

        # Hard tool-call budget: once the total ceiling is spent, refuse any
        # further tool calls and force the model to synthesize a final answer
        # from what it already gathered. This is what stops runaway loops where
        # the model keeps batching searches turn after turn.
        if max_tool_calls and total_tool_calls >= max_tool_calls:
            forced_wraps += 1
            if forced_wraps > _MAX_FORCED_WRAPS:
                print(f"[{label}] 🧮 Tool-call budget ({max_tool_calls}) exhausted and the "
                      f"model keeps requesting tools — bailing with what's gathered.")
                return False, f"Tool-call budget ({max_tool_calls}) exhausted", None, parsed, tools_used
            print(f"[{label}] 🧮 Tool-call budget ({max_tool_calls}) reached "
                  f"({total_tool_calls} calls) — refusing further calls, forcing wrap-up.")
            conversation.append(
                "SYSTEM: You have reached the tool-call budget for this task "
                f"({total_tool_calls}/{max_tool_calls} calls used). Do NOT request any more "
                "tools. Synthesize everything you have learned so far and respond NOW with your "
                "FINAL JSON (e.g. {\"status\": \"completed\", \"summary\": \"...\"}).")
            continue

        # Execute tool calls
        tool_results = []
        calls_this_turn = 0
        for tc in tool_calls:
            if stop_event is not None and stop_event.is_set():
                print(f"[{label}] Stopped before tool call (interrupt).")
                return False, "Interrupted", None, None, tools_used
            # Stop mid-array if the global budget runs out or this turn has
            # already executed its allowance — the rest is deferred / dropped.
            if max_tool_calls and total_tool_calls >= max_tool_calls:
                print(f"[{label}] 🧮 Tool-call budget reached mid-turn — dropping "
                      f"{len(tool_calls) - calls_this_turn} remaining call(s) this turn.")
                break
            if max_calls_per_turn and calls_this_turn >= max_calls_per_turn:
                print(f"[{label}] ✂ Per-turn tool-call cap ({max_calls_per_turn}) reached — "
                      f"deferring {len(tool_calls) - calls_this_turn} call(s) to the next turn.")
                break
            tool_name = tc.get("tool", "")
            tool_args = tc.get("args")
            if not isinstance(tool_args, dict):
                # Common compact-model variant:
                # {"tool":"search_web","query":"..."} instead of nesting
                # parameters under "args". Normalize it rather than executing
                # an empty call and sending the model into a correction loop.
                tool_args = {
                    key: value for key, value in tc.items()
                    if key not in {"tool", "name", "args"}
                }
            print(f"[{label}] Calling tool: {tool_name}({tool_args})")
            _progress("tool", tool_name)
            sig = f"{tool_name}|{json.dumps(tool_args, sort_keys=True, default=str)}"
            if sig in seen_call_sigs:
                # The model forgot it already did this (older results get
                # compressed away) — don't burn the box on a repeat.
                print(f"[{label}] ♻️ Duplicate tool call skipped.")
                tool_results.append({
                    "tool": tool_name, "args": tool_args,
                    "result": ("DUPLICATE CALL SKIPPED: you already ran this exact tool with these exact "
                               "arguments earlier in this task. Use what you already learned, try a "
                               "DIFFERENT query/tool, or finish with the final JSON now."),
                })
                continue
            seen_call_sigs.add(sig)
            calls_log.append(f"{tool_name}({json.dumps(tool_args, sort_keys=True, default=str)[:100]})")
            if tool_name not in tools_used:
                tools_used.append(tool_name)
            total_tool_calls += 1
            calls_this_turn += 1

            if tool_guard_fn is not None:
                try:
                    allowed, denial = tool_guard_fn(
                        tool_name, tool_args, parsed, total_tool_calls, tuple(tools_used))
                except Exception as e:
                    allowed, denial = False, f"tool policy error: {e}"
                if not allowed:
                    print(f"[{label}] ⛔ Tool blocked: {tool_name} — {denial}")
                    tool_results.append({
                        "tool": tool_name, "args": tool_args,
                        "result": f"BLOCKED BY BACKGROUND POLICY: {denial}",
                    })
                    continue

            if tool_name == "execute_python_code" and tool_executor is None:
                result = await _execute_python_code(tool_args.get("code", ""))
            else:
                try:
                    result = await loop.run_in_executor(
                        None,
                        lambda tn=tool_name, ta=tool_args: (
                            tool_executor(tn, ta) if tool_executor is not None
                            else execute_tool(tn, ta)
                        )
                    )
                except Exception as e:
                    result = f"Error: {e}"

            result_str = _truncate_middle(str(result), max_tool_result_chars)
            print(f"[{label}] Tool result:\n{result_str[:1000]}")
            if session_id:
                _rec.log_step(session_id, "tool", tool=tool_name,
                              args=json.dumps(tool_args, default=str)[:1000],
                              result=result_str)
            tool_results.append({
                "tool": tool_name,
                "args": tool_args,
                "result": result_str
            })

        # Feed tool results back into conversation
        results_text = "\n".join([
            f"Tool '{r['tool']}' returned: {r['result']}"
            for r in tool_results
        ])
        guidance = None
        if continue_guidance_fn is not None:
            try:
                guidance = continue_guidance_fn(total_tool_calls, tools_used)
            except Exception as e:
                print(f"[{label}] continue_guidance_fn failed: {e}")
        if not guidance:
            guidance = ("Continue with your task. If done, respond with "
                        "{\"status\": \"completed\", \"summary\": \"...\"}. "
                        "If you need more tool calls, respond with {\"tool_calls\": [...]}.")
        # Budget just got spent on this round → steer straight to wrap-up
        # (overrides any caller coaching, which would keep it digging).
        if max_tool_calls and total_tool_calls >= max_tool_calls:
            guidance = ("You have now used your entire tool-call budget "
                        f"({total_tool_calls}/{max_tool_calls}). Do NOT call any more tools. "
                        "Respond with your FINAL JSON now, synthesizing everything above.")
        # Compact action history: full results of older steps get compressed
        # away, after which the model forgets what it searched and repeats
        # itself — this one-liner survives every compression.
        already = ""
        if calls_log:
            already = ("ACTIONS ALREADY TAKEN (do NOT repeat these or near-identical ones): "
                       + "; ".join(calls_log[-8:]) + "\n\n")
        conversation.append(f"TOOL RESULTS:\n{results_text}\n\n{already}{guidance}")

    # Exhausted all turns
    print(f"[{label}] Exhausted {max_turns} LLM turns")
    return False, f"Exhausted max LLM turns ({max_turns})", None, None, tools_used
