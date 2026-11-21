# Asserting on tool calls

By default voicerig sees what a caller could see - the transcripts - plus the HTTP calls your agent makes to the
stub backend. LiveKit does not publish an agent's tool calls to the room, so to assert "the agent called
`cancel_order` with `order_id=4417`" (or, as important, "the agent did **not** call it") the agent has to say so.
That is a few lines in the agent under test:

```python
import asyncio, json

def report_tool_calls(session, room):
    def send(payload):
        asyncio.ensure_future(room.local_participant.send_text(json.dumps(payload), topic="voicerig.tool_calls"))

    @session.on("function_tools_executed")
    def _(ev):
        for call in ev.function_calls:
            send({"name": call.name, "arguments": call.arguments})

    send({"hook": "ready"})          # announces that the hook exists - see below

# in your entrypoint, after `await session.start(...)`:
report_tool_calls(session, ctx.room)
```

It needs `livekit-agents` 1.x (`function_tools_executed` is emitted after a batch of tools has run). Nothing is
read from voicerig; the agent only publishes a text stream on the topic `voicerig.tool_calls`, which does nothing
when no rig is listening.

## Steps

| step | meaning |
|---|---|
| `expect_tool_call: {name, args_contains, within}` | the agent called a tool whose name matches (`fnmatch` glob) and whose arguments contain the given values (strings match as case-insensitive substrings) |
| `expect_no_tool_call: {name, for}` | for `for` seconds the agent called no matching tool |

`require_tool_call` and `require_no_tool_call` are the INCONCLUSIVE variants, as for every other step.

## Why the `ready` message exists

"The agent did not call `cancel_order`" is true of every agent that does not have the hook - including one that
cancels on every request. So a rig that never received `{"hook": "ready"}` does not let `expect_no_tool_call`
pass: the step is **INCONCLUSIVE**, with a note saying the hook is missing. A missing `expect_tool_call` names the
same cause. A negative check that can pass for the wrong reason is worse than no check.

The event fires after the tool has run, so it reports a side effect that has already happened; it does not
prevent one. For "must not happen" assertions pair it with `expect_no_call` on the backend path where you can.
