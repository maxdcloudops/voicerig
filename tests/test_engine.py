"""The engine with fakes: no LiveKit, no Docker. The point of most of these is that a check CAN fail."""
import asyncio
import time

from voicerig.config import Step
from voicerig.engine import QUESTION, percentile, run_steps, wilson


class FakeStub:
    def __init__(self):
        self._calls = []

    def calls(self):
        return list(self._calls)

    def add(self, path, body=None, method="POST"):
        self._calls.append({"t": time.time(), "method": method, "path": path, "body": body or {}})


class FakeCall:
    """`script` maps an utterance to what the 'agent' does in reaction: a callable(call, stub)."""

    def __init__(self, stub, script=None):
        self.events, self.stub, self.script, self.speech_ended_at = [], stub, script or {}, 0.0
        self.tool_calls, self.tool_hook = [], False

    def tool(self, name, **arguments):
        self.tool_calls.append({"t": time.time(), "name": name, "arguments": arguments})

    def agent(self, text, role="assistant"):
        now = time.time()
        self.events.append({"role": role, "text": text, "_t": now, "_t_first": now})

    async def say(self, name, **kw):
        self.speech_ended_at = time.time()
        await asyncio.sleep(0.01)
        fn = self.script.get(name)
        if fn:
            fn(self, self.stub)


def steps(*specs):
    out = []
    for kind, params in specs:
        level, _, k = kind.partition("_") if kind not in ("say", "wait") else ("", "", kind)
        out.append(Step(k if level else kind, level, params))
    return out


def run(call, stub, *specs):
    return asyncio.run(run_steps(steps(*specs), call, stub, time.time() - 60))


def test_question_detector_finds_a_question_followed_by_more_text():
    assert QUESTION.search("Shall I cancel order 4417? Just say yes.")
    assert QUESTION.search("Shall I cancel it?")
    assert not QUESTION.search("Done, it has been cancelled.")
    assert not QUESTION.search("Is it?" + " x" * 200)      # the question is more than 240 characters from the end


def test_pass_when_agent_asks_then_acts_on_yes():
    stub = FakeStub()
    call = FakeCall(stub, {
        "cancel": lambda c, s: c.agent("Shall I cancel order 4417?"),
        "yes": lambda c, s: s.add("/orders/cancel", {"order_id": "4417"}),
    })
    v = run(call, stub,
            ("say", {"audio": "cancel"}),
            ("expect_agent_says", {"question": True, "within": 1}),
            ("expect_no_call", {"path": "/orders/cancel", "for": 0.1}),
            ("say", {"audio": "yes"}),
            ("expect_call", {"path": "/orders/cancel", "within": 1, "body_contains": {"order_id": "4417"}}))
    assert v.status == "PASS", v.notes
    assert len(v.latencies) == 1 and v.latencies[0] >= 0


def test_expect_no_call_FAILS_when_the_agent_acted_NEGATIVE_CONTROL():
    stub = FakeStub()
    call = FakeCall(stub, {"cancel": lambda c, s: (c.agent("Done."), s.add("/orders/cancel"))})
    v = run(call, stub, ("say", {"audio": "cancel"}), ("expect_no_call", {"path": "/orders/cancel", "for": 0.1}))
    assert v.status == "FAIL"
    assert "/orders/cancel" in v.notes[-1]


def test_expect_failure_is_FAIL_but_require_failure_is_INCONCLUSIVE():
    stub = FakeStub()
    call = FakeCall(stub, {"cancel": lambda c, s: c.agent("Your order is cancelled.")})
    spec = ("agent_says", {"question": True, "within": 0.3})
    assert run(call, stub, ("say", {"audio": "cancel"}), ("expect_" + spec[0], spec[1])).status == "FAIL"
    call2 = FakeCall(FakeStub(), {"cancel": lambda c, s: c.agent("Your order is cancelled.")})
    v = run(call2, FakeStub(), ("say", {"audio": "cancel"}), ("require_" + spec[0], spec[1]))
    assert v.status == "INCONCLUSIVE" and "precondition" in v.notes[-1]


def test_a_failed_step_stops_the_scenario():
    stub = FakeStub()
    call = FakeCall(stub)
    v = run(call, stub, ("expect_call", {"path": "/x", "within": 0.1}), ("expect_call", {"path": "/y", "within": 30}))
    assert v.status == "FAIL"             # and it returned at once rather than waiting 30 s for /y


def test_default_window_is_since_the_last_say_and_since_start_widens_it():
    stub = FakeStub()
    call = FakeCall(stub, {"b": lambda c, s: None})
    stub.add("/early")                                       # happened before any say
    t_start = time.time() - 5
    stub._calls[0]["t"] = time.time() - 1
    v = asyncio.run(run_steps(steps(("say", {"audio": "b"}), ("expect_no_call", {"path": "/early", "for": 0.05})), call, stub, t_start))
    assert v.status == "PASS"                                # the call predates the last say
    v = asyncio.run(run_steps(steps(("say", {"audio": "b"}), ("expect_no_call", {"path": "/early", "for": 0.05, "since": "start"})), call, stub, t_start))
    assert v.status == "FAIL"


def test_user_heard_and_regex_are_case_insensitive():
    stub = FakeStub()
    call = FakeCall(stub, {"yes": lambda c, s: c.agent("Yes.", role="user")})
    v = run(call, stub, ("say", {"audio": "yes"}), ("expect_user_heard", {"matches": r"\byes\b", "within": 1}))
    assert v.status == "PASS"
    call = FakeCall(stub, {"yes": lambda c, s: c.agent("Yeah.", role="user")})
    v = run(call, stub, ("say", {"audio": "yes"}), ("expect_user_heard", {"matches": r"\byes\b", "within": 0.3}))
    assert v.status == "FAIL" and "Yeah" in v.notes[-1]


def test_no_agent_reply():
    stub = FakeStub()
    quiet = FakeCall(stub)
    assert run(quiet, stub, ("say", {"audio": "x"}), ("expect_no_agent_reply", {"for": 0.1})).status == "PASS"
    chatty = FakeCall(stub, {"x": lambda c, s: c.agent("Hello!")})
    assert run(chatty, stub, ("say", {"audio": "x"}), ("expect_no_agent_reply", {"for": 0.1})).status == "FAIL"


def test_body_contains_is_substring_for_strings_and_exact_otherwise():
    stub = FakeStub()
    stub.add("/apply", {"company": "Acme Inc", "n": 3})
    call = FakeCall(stub)
    ok = run(call, stub, ("expect_call", {"path": "/apply", "within": 0.1, "since": "start", "body_contains": {"company": "acme", "n": 3}}))
    assert ok.status == "PASS"
    bad = run(call, stub, ("expect_call", {"path": "/apply", "within": 0.1, "since": "start", "body_contains": {"n": 4}}))
    assert bad.status == "FAIL"


def test_path_globs():
    stub = FakeStub()
    stub.add("/api/v2/apply")
    v = run(FakeCall(stub), stub, ("expect_call", {"path": "/api/*/apply", "within": 0.1, "since": "start"}))
    assert v.status == "PASS"


def test_question_and_matches_combine():
    stub = FakeStub()
    call = FakeCall(stub, {"x": lambda c, s: c.agent("Can you tell me which city?")})
    spec = {"question": True, "matches": "apply", "within": 0.3}
    v = run(call, stub, ("say", {"audio": "x"}), ("expect_agent_says", spec))
    assert v.status == "FAIL" and "ask a question and say anything matching /apply/" in v.notes[-1]    # a question, but not about applying
    call = FakeCall(stub, {"x": lambda c, s: c.agent("Shall I apply to the Acme role?")})
    assert run(call, stub, ("say", {"audio": "x"}), ("expect_agent_says", spec)).status == "PASS"
    call = FakeCall(stub, {"x": lambda c, s: c.agent("I will apply now.")})
    assert run(call, stub, ("say", {"audio": "x"}), ("expect_agent_says", spec)).status == "FAIL"        # about applying, but no question


# --- tool calls (reported by the agent-side hook) -------------------------------------------------------------

def hooked(stub, script=None):
    call = FakeCall(stub, script)
    call.tool_hook = True
    return call


def test_tool_call_passes_when_the_agent_called_it_with_matching_arguments():
    stub = FakeStub()
    call = hooked(stub, {"yes": lambda c, s: c.tool("cancel_order", order_id="4417")})
    v = run(call, stub, ("say", {"audio": "yes"}), ("expect_tool_call", {"name": "cancel_*", "args_contains": {"order_id": "4417"}, "within": 1}))
    assert v.status == "PASS"


def test_tool_call_fails_on_wrong_arguments_and_names_what_it_saw():
    stub = FakeStub()
    call = hooked(stub, {"yes": lambda c, s: c.tool("cancel_order", order_id="9999")})
    v = run(call, stub, ("say", {"audio": "yes"}), ("expect_tool_call", {"name": "cancel_order", "args_contains": {"order_id": "4417"}, "within": 0.3}))
    assert v.status == "FAIL" and "cancel_order" in v.notes[-1]


def test_missing_tool_call_says_the_hook_is_absent_when_it_never_announced_itself():
    stub = FakeStub()
    call = FakeCall(stub)
    v = run(call, stub, ("say", {"audio": "yes"}), ("expect_tool_call", {"name": "cancel_order", "within": 0.3}))
    assert v.status == "FAIL" and "hook" in v.notes[-1]


def test_require_tool_call_is_inconclusive_not_a_fail():
    stub = FakeStub()
    v = run(hooked(stub), stub, ("say", {"audio": "yes"}), ("require_tool_call", {"name": "look_up_order", "within": 0.3}))
    assert v.status == "INCONCLUSIVE"


def test_no_tool_call_fails_when_a_matching_tool_was_called():              # negative control
    stub = FakeStub()
    call = hooked(stub, {"cancel": lambda c, s: c.tool("cancel_order", order_id="4417")})
    v = run(call, stub, ("say", {"audio": "cancel"}), ("expect_no_tool_call", {"name": "cancel_order", "for": 0.2}))
    assert v.status == "FAIL"


def test_no_tool_call_passes_only_with_the_hook_present():
    stub = FakeStub()
    assert run(hooked(stub), stub, ("say", {"audio": "no"}), ("expect_no_tool_call", {"name": "cancel_order", "for": 0.1})).status == "PASS"


def test_no_tool_call_is_inconclusive_without_the_hook_never_a_free_pass():
    stub = FakeStub()
    v = run(FakeCall(stub), stub, ("say", {"audio": "no"}), ("expect_no_tool_call", {"name": "cancel_order", "for": 0.1}))
    assert v.status == "INCONCLUSIVE" and "hook" in v.notes[-1]


# --- statistics ---------------------------------------------------------------------------------------------

def test_wilson_interval_is_not_a_point_estimate_at_the_extremes():
    lo, hi = wilson(20, 20)
    assert 0.83 < lo < 0.85 and hi == 1.0           # "20 of 20" means "at least ~84%", not "100%"
    lo, hi = wilson(0, 20)
    assert lo == 0.0 and 0.15 < hi < 0.17
    lo, hi = wilson(10, 20)
    assert 0.29 < lo < 0.30 and 0.70 < hi < 0.71 and abs((lo + hi) / 2 - 0.5) < 0.01


def test_wilson_with_no_trials_is_everything_not_a_division_by_zero():
    assert wilson(0, 0) == (0.0, 1.0)


def test_percentile_interpolates():
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert percentile(xs, 50) == 3.0 and percentile(xs, 0) == 1.0 and percentile(xs, 100) == 5.0
    assert abs(percentile(xs, 95) - 4.8) < 1e-9
