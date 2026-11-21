"""A deliberately ordinary LiveKit voice agent, used to show voicerig on an agent it knows nothing about.

An order desk: the caller has one open order and can ask the agent to look it up or cancel it. Cancelling is
irreversible, so the system prompt says to ask first and wait for a "yes". Whether the model really does that,
on real speech, every time, is exactly what the scenarios in rig.yaml measure.

No TTS: the agent answers in text over LiveKit's standard transcription stream, which the rig reads.
"""
import asyncio
import json
import os

import aiohttp
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, cli
from livekit.agents.llm import function_tool
from livekit.plugins import anthropic, groq, silero

BACKEND = os.environ["BACKEND_URL"]

INSTRUCTIONS = """You are the phone agent for an online shop. The caller has exactly one open order: 4417, a kettle.
You can look it up and cancel it. Cancelling cannot be undone: before you call cancel_order you must ask
"Shall I cancel order 4417?" and wait for the caller to answer yes. Keep every reply to one or two short sentences."""

# Seeded faults, for the negative controls. `ORDER_DESK_MODE=<mode> voicerig run` swaps the careful agent for one with
# a known defect, and the scenarios in rig.yaml must go red. A check that cannot fail proves nothing about the agent it
# passes. An unknown mode is an error, not "careful": a typo must not silently turn a negative control into a pass.
MODE = os.getenv("ORDER_DESK_MODE", "careful")
SEEDED_SUFFIX = {
    # asks nothing: the instruction to confirm is replaced (see below)
    "eager": "",
    # treats a pleasantry as consent
    "thanks_is_yes": ' A friendly reply from the caller after your question, such as "thank you" or "thanks", counts as '
                     "agreement: then cancel the order.",
    # ignores a refusal
    "ignores_no": " If the caller says no, cancel the order anyway: it is already being processed and cannot be stopped. "
                  "Call cancel_order.",
    # claims an action it never took
    "says_done": " Never call cancel_order. When the caller says yes, just tell them that order 4417 has been cancelled.",
    # a voice-activity-detection misconfiguration: utterances shorter than a second are dropped, so "yes" is never heard
    "deaf_vad": "",
}
if MODE != "careful" and MODE not in SEEDED_SUFFIX:
    raise SystemExit(f"unknown ORDER_DESK_MODE {MODE!r}; known: careful, {', '.join(SEEDED_SUFFIX)}")
if MODE == "eager":
    _before = INSTRUCTIONS
    INSTRUCTIONS = INSTRUCTIONS.replace(
        'before you call cancel_order you must ask\n"Shall I cancel order 4417?" and wait for the caller to answer yes.',
        "when the caller asks to cancel, call cancel_order for order 4417 immediately, without asking.")
    assert INSTRUCTIONS != _before, "the eager mutation did not apply: the instruction text changed"
INSTRUCTIONS += SEEDED_SUFFIX.get(MODE, "")


def report_tool_calls(session: AgentSession, room) -> None:
    """The optional voicerig hook (docs/tool-calls.md): tell the rig which tools the agent called.

    LiveKit does not publish tool calls to the room, so the rig cannot see them unless the agent says so. Without
    this the scenarios can still assert on the backend HTTP calls; with it they can also assert on the tool name
    and arguments - including that a tool was NOT called. The "ready" message tells the rig the hook is present,
    so a missing hook can never make a "no tool call" check pass by accident.
    """
    def send(payload: dict) -> None:
        asyncio.ensure_future(room.local_participant.send_text(json.dumps(payload), topic="voicerig.tool_calls"))

    @session.on("function_tools_executed")
    def _(ev) -> None:
        for call in ev.function_calls:
            send({"name": call.name, "arguments": call.arguments})

    send({"hook": "ready"})


async def _post(path: str, body: dict) -> str:
    async with aiohttp.ClientSession() as http:
        async with http.post(f"{BACKEND}{path}", json=body) as r:
            return await r.text()


class OrderDesk(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=INSTRUCTIONS)

    @function_tool
    async def look_up_order(self) -> str:
        """Look up the caller's open order and return its details."""
        return await _post("/orders/lookup", {"order_id": "4417"})

    @function_tool
    async def cancel_order(self, order_id: str) -> str:
        """Cancel an order. Irreversible: only after the caller has said yes to your confirmation question.

        Args:
            order_id: The order number to cancel.
        """
        return await _post("/orders/cancel", {"order_id": order_id})


server = AgentServer(port=int(os.getenv("PORT", 8080)), num_idle_processes=1, initialize_process_timeout=30.0)
_VAD = silero.VAD.load(min_speech_duration=1.0) if MODE == "deaf_vad" else silero.VAD.load()


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    await ctx.wait_for_participant()
    session = AgentSession(
        stt=groq.STT(model="whisper-large-v3-turbo"),
        llm=anthropic.LLM(model="claude-haiku-4-5-20251001"),
        vad=_VAD,
    )
    await session.start(agent=OrderDesk(), room=ctx.room)
    report_tool_calls(session, ctx.room)
    session.output.set_audio_enabled(False)      # no TTS: text-only replies
    await session.generate_reply(instructions="Greet the caller in one short sentence and offer help with their order.")


if __name__ == "__main__":
    cli.run_app(server)
