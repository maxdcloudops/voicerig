"""The scenario engine: runs a scenario's steps against a `call` and a `stub` and returns a verdict.

It imports no LiveKit, so it is unit-tested on the host with fakes. What it needs from its two collaborators:

    call.events                list of {"role": "user"|"assistant", "text", "_t", "_t_first"} (wall-clock floats)
    await call.say(name, lead=, tail=)     speak WAV `name`; returns when playout finished
    call.speech_ended_at       wall-clock time the last spoken words ended
    stub.calls()               list of {"t", "method", "path", "body"} the agent sent to the fake backend
    call.tool_calls            list of {"t", "name", "arguments"} the agent reported through the tool-call hook
    call.tool_hook             True once the agent announced that hook (without it, absence of a tool call is not evidence)

Verdicts, as in the first rig this came from:
    PASS          the behaviour under test happened (or, for a negative check, did not)
    FAIL          an `expect_*` step did not hold: the agent did something wrong
    INCONCLUSIVE  a `require_*` step did not hold: the conversation never reached the state the scenario tests
                  (e.g. the model did not ask for confirmation this time). The scenario is retried; INCONCLUSIVE
                  after the last attempt still fails the run. Silence is not a pass.
"""
from __future__ import annotations

import asyncio
import fnmatch
import re
import time
from dataclasses import dataclass, field
from typing import Any

from .config import Step

QUESTION = re.compile(r"\?[\s\S]{0,240}$")        # a "?" within the last 240 characters: ends in a question,
                                                    # even when a trailing sentence follows it ("...? Just say yes.")


@dataclass
class Verdict:
    status: str                                   # PASS | FAIL | INCONCLUSIVE
    notes: list[str] = field(default_factory=list)
    latencies: list[float] = field(default_factory=list)   # seconds, end of user speech -> first agent text
    stats: dict = field(default_factory=dict)              # filled for `repeat` scenarios: counts, rate, 95% interval, latency


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for k successes in n trials. Behaves at 0/n and n/n, unlike the normal approximation:
    "20 of 20" is not "100% +/- 0", it is "at least ~84%"."""
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def aggregate_status(counts: dict, n: int, min_pass_rate: float) -> str:
    """The verdict of a scenario repeated `n` times. PASS when enough repetitions passed; otherwise FAIL if any
    repetition failed, and INCONCLUSIVE only when none did - every shortfall was a state that was never reached.
    Calling that a FAIL would blame the agent for a condition the run could not observe."""
    if n > 0 and counts["PASS"] / n >= min_pass_rate:
        return "PASS"
    return "FAIL" if counts["FAIL"] else "INCONCLUSIVE"


def percentile(xs: list[float], q: float) -> float:
    """Linear-interpolation percentile, q in [0, 100]. With few samples p95 is close to the maximum - say so."""
    s = sorted(xs)
    if not s:
        raise ValueError("no samples")
    pos = (len(s) - 1) * q / 100
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def _short(text: str, n: int = 90) -> str:
    text = " ".join(text.split())
    return repr(text if len(text) <= n else text[: n - 1] + "…")


def _events(call, role: str, since: float) -> list[dict]:
    return [e for e in call.events if e["role"] == role and e["_t"] >= since and e.get("text")]


def _body_matches(body: dict, want: dict) -> bool:
    for k, v in want.items():
        got = body.get(k)
        if isinstance(v, str) and isinstance(got, str):
            if v.lower() not in got.lower():
                return False
        elif got != v:
            return False
    return True


def _calls(stub, since: float, path: str | None, body_contains: dict | None = None) -> list[dict]:
    return [c for c in stub.calls()
            if c["t"] >= since and (path is None or fnmatch.fnmatch(c["path"], path))
            and (not body_contains or _body_matches(c.get("body") or {}, body_contains))]


def _tools(call, since: float, name: str | None, args_contains: dict | None = None) -> list[dict]:
    out = []
    for c in getattr(call, "tool_calls", []):
        args = c.get("arguments")
        if c["t"] >= since and (name is None or fnmatch.fnmatch(c["name"], name)) \
                and (not args_contains or _body_matches(args if isinstance(args, dict) else {}, args_contains)):
            out.append(c)
    return out


async def _poll(fn, within: float, every: float = 0.25):
    end = time.time() + within
    while True:
        got = fn()
        if got:
            return got
        if time.time() >= end:
            return None
        await asyncio.sleep(every)


async def run_steps(steps: list[Step], call, stub, t_start: float) -> Verdict:
    v = Verdict("PASS")
    last_say = t_start
    for step in steps:
        p = step.params
        if step.kind == "say":
            last_say = time.time()
            await call.say(p["audio"], **{k: p[k] for k in ("lead", "tail") if k in p})
            continue
        if step.kind == "wait":
            await asyncio.sleep(p["seconds"])
            continue

        since = t_start if p.get("since") == "start" else last_say
        bad = "FAIL" if step.level == "expect" else "INCONCLUSIVE"

        def fail(why: str) -> Verdict:
            v.status = bad
            v.notes.append(("did not hold: " if bad == "FAIL" else "precondition not reached: ") + why)
            return v

        if step.kind in ("agent_says", "user_heard"):
            role = "assistant" if step.kind == "agent_says" else "user"
            # `question` and `matches` combine: "a question that mentions applying", not "any question"
            rx = re.compile(p["matches"], re.IGNORECASE) if p.get("matches") else None
            within = p.get("within", 30)

            def find():
                for e in _events(call, role, since):
                    text = e["text"].strip()
                    if (not p.get("question") or QUESTION.search(text)) and (rx is None or rx.search(text)):
                        return e
                return None
            ev = await _poll(find, within)
            if ev is None:
                verb = "say" if role == "assistant" else "hear"
                past = "said" if role == "assistant" else "heard"
                seen = [e["text"] for e in _events(call, role, since)]
                wanted = " and ".join(([ "ask a question" ] if p.get("question") else []) + ([f"{verb} anything matching /{p['matches']}/"] if p.get("matches") else []))
                got = f"last {past}: {_short(seen[-1])}" if seen else f"nothing {past} at all"
                return fail(f"the agent did not {wanted} within {within:g}s ({got})")
            v.notes.append(f"{'agent said' if role == 'assistant' else 'agent heard'}: {_short(ev['text'])}")
            if role == "assistant" and getattr(call, "speech_ended_at", 0) and ev.get("_t_first", 0) > call.speech_ended_at:
                v.latencies.append(ev["_t_first"] - call.speech_ended_at)
        elif step.kind == "call":
            within = p.get("within", 30)
            got = await _poll(lambda: _calls(stub, since, p["path"], p.get("body_contains")), within)
            if not got:
                return fail(f"no call to {p['path']} within {within:g}s (calls so far: "
                            f"{[c['path'] for c in _calls(stub, since, None)] or 'none'})")
            v.notes.append(f"backend received {got[0]['method']} {got[0]['path']}")
        elif step.kind == "no_call":
            await asyncio.sleep(p.get("for", 10))
            got = _calls(stub, since, p.get("path"))
            if got:
                return fail(f"backend received {got[0]['method']} {got[0]['path']} (body: {_short(str(got[0].get('body')))})")
            v.notes.append(f"no call to {p.get('path', 'the backend')} for {p.get('for', 10):g}s")
        elif step.kind == "tool_call":
            within = p.get("within", 30)
            got = await _poll(lambda: _tools(call, since, p["name"], p.get("args_contains")), within)
            if not got:
                seen = [c["name"] for c in _tools(call, since, None)]
                hint = "" if getattr(call, "tool_hook", False) else \
                    " - the agent never announced the voicerig tool-call hook, so no tool call can be seen (docs/tool-calls.md)"
                return fail(f"no tool call {p['name']} within {within:g}s (tool calls so far: {seen or 'none'}){hint}")
            v.notes.append(f"agent called tool {got[0]['name']}({_short(str(got[0].get('arguments')), 60)})")
        elif step.kind == "no_tool_call":
            if not getattr(call, "tool_hook", False):
                # without the hook "no tool call" is true of ANY agent - silence is not a pass
                v.status = "INCONCLUSIVE"
                v.notes.append("precondition not reached: the agent never announced the voicerig tool-call hook, "
                               "so an absent tool call proves nothing (docs/tool-calls.md)")
                return v
            await asyncio.sleep(p.get("for", 10))
            got = _tools(call, since, p["name"])
            if got:
                return fail(f"agent called tool {got[0]['name']}({_short(str(got[0].get('arguments')), 60)})")
            v.notes.append(f"no tool call {p['name']} for {p.get('for', 10):g}s")
        elif step.kind == "no_agent_reply":
            await asyncio.sleep(p.get("for", 10))
            said = _events(call, "assistant", since)
            if said:
                return fail(f"agent replied: {_short(said[-1]['text'])}")
            v.notes.append(f"agent stayed silent for {p.get('for', 10):g}s")
    return v


def summarize(results: dict[str, Verdict]) -> dict[str, int]:
    return {s: sum(1 for r in results.values() if r.status == s) for s in ("PASS", "FAIL", "INCONCLUSIVE")}
