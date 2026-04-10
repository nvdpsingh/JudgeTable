"""
JudgeTable LangGraph — the deliberation state graph.

Flow:
    START → amplifier → [agent_worker × 8 in parallel] → moderator → conditional
                              ↑                                         │
                              └──────────── (CONTINUE) ────────────────┘
                                                                        │
                                                           (SATISFIED) → synthesizer → END

The amplifier uses tool calling to selectively fetch KB data from Postgres.
Agents run in parallel via LangGraph's Send (fan-out/fan-in).
The moderator decides SATISFIED or CONTINUE — if CONTINUE, agents re-run
with cross-pollination feedback from the moderator.
"""

import asyncio
import json
import operator
import os
import re
from typing import TypedDict, Annotated

from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage
from langchain_groq import ChatGroq
from langgraph.graph import StateGraph, START, END, Send
from pydantic import BaseModel, Field

from agents import AGENTS, MODERATOR, SYNTHESIZER
from amplifier import SYSTEM_PROMPT as AMPLIFIER_PROMPT, _format_entries
from database import get_knowledge

MODEL = "llama-3.3-70b-versatile"
MAX_ROUNDS = 3


# ────────────────────────────────────────────
# State types
# ────────────────────────────────────────────

class JudgeTableState(TypedDict):
    # Input
    decision: str
    context: str
    user_id: str
    # Amplifier output
    enriched_prompt: str
    fetched_categories: list[str]
    # Agent responses — accumulated across rounds via add reducer
    agent_responses: Annotated[list[dict], operator.add]
    # Moderator
    moderator_response: str
    moderator_decision: str
    cross_pollination: str
    dissent_flags: list[dict]
    # Synthesizer
    synthesizer_response: str
    # Control
    round_number: int


class AgentWorkerState(TypedDict):
    """State passed to each parallel agent worker via Send."""
    enriched_prompt: str
    round_number: int
    previous_response: str
    cross_pollination: str
    agent_config: dict


# ────────────────────────────────────────────
# Tool schema for amplifier
# ────────────────────────────────────────────

class FetchKnowledge(BaseModel):
    """Fetch entries from the user's knowledge base for specific categories."""
    categories: list[str] = Field(
        description=(
            "Categories to fetch. Options: personality, goals, values, "
            "blind_spots, context_log, relationships, challenges"
        )
    )


# ────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────

def _get_llm(temperature: float = 0.7) -> ChatGroq:
    return ChatGroq(
        model=MODEL,
        temperature=temperature,
        api_key=os.getenv("GROQ_API_KEY"),
    )


CONTEXT_REQUEST_RE = re.compile(r"\[NEED_CONTEXT:\s*(.+?)\]", re.DOTALL)


def _strip_context_request(text: str) -> str:
    return CONTEXT_REQUEST_RE.sub("", text).rstrip()


def _parse_moderator_decision(text: str) -> tuple[str, str]:
    if "DECISION: SATISFIED" in text:
        return "SATISFIED", ""
    if "DECISION: CONTINUE" in text:
        idx = text.index("DECISION: CONTINUE") + len("DECISION: CONTINUE")
        return "CONTINUE", text[idx:].strip()
    return "SATISFIED", ""


def _parse_dissent_flags(text: str) -> list[dict]:
    flags = []
    for m in re.finditer(r"DISSENT:\s*\[?([^\]\—\-]+?)\]?\s*[\—\-]+\s*(.+)", text):
        name = m.group(1).strip()
        flags.append({
            "agent_name": name,
            "agent_key": name.lower().replace("the ", "").replace(" ", "_").replace("'", ""),
            "concern": m.group(2).strip(),
        })
    return flags


async def _emit(config, event: str, data: dict):
    """Put an SSE event onto the queue (if one exists in config)."""
    queue = config.get("configurable", {}).get("sse_queue")
    if queue:
        await queue.put({"event": event, "data": data})


# ────────────────────────────────────────────
# Node: Amplifier (tool-calling agentic loop)
# ────────────────────────────────────────────

async def amplifier_node(state: JudgeTableState, config: dict):
    db_available = config.get("configurable", {}).get("db_available", False)
    user_id = state["user_id"]
    decision = state["decision"]
    context = state.get("context", "")

    await _emit(config, "amplifier_start", {"status": "Analyzing query and gathering context..."})

    # No DB — just pass through
    if not db_available:
        enriched = f"## The Decision\n\n{decision}"
        if context:
            enriched += f"\n\n## Additional Context\n\n{context}"
        await _emit(config, "amplifier_done", {
            "fetched_categories": [],
            "context_summary": "Running without knowledge base",
        })
        return {"enriched_prompt": enriched, "fetched_categories": [], "round_number": 1}

    # Tool-calling loop
    llm = _get_llm(temperature=0.3).bind_tools([FetchKnowledge])

    messages = [
        SystemMessage(content=AMPLIFIER_PROMPT),
        HumanMessage(content=(
            f"Here is the person's decision:\n\n{decision}"
            + (f"\n\nAdditional context they provided:\n{context}" if context else "")
        )),
    ]

    fetched: list[str] = []

    for _ in range(5):
        response = await llm.ainvoke(messages)
        messages.append(response)

        if not response.tool_calls:
            break

        for tc in response.tool_calls:
            categories = tc["args"].get("categories", [])
            entries_by_cat = {}
            for cat in categories:
                entries = await get_knowledge(user_id, cat)
                for e in entries:
                    e["id"] = str(e["id"])
                    e["created_at"] = str(e["created_at"])
                    e["updated_at"] = str(e["updated_at"])
                entries_by_cat[cat] = entries
                if cat not in fetched:
                    fetched.append(cat)

            await _emit(config, "amplifier_fetch", {"categories": categories})

            result_text = _format_entries(entries_by_cat)
            messages.append(ToolMessage(
                content=result_text or "No entries found in these categories.",
                tool_call_id=tc["id"],
            ))

    enriched = response.content or f"## The Decision\n\n{decision}"
    summary = f"Loaded {', '.join(fetched)}" if fetched else "No KB data needed"

    await _emit(config, "amplifier_done", {
        "fetched_categories": fetched,
        "context_summary": summary,
    })

    return {"enriched_prompt": enriched, "fetched_categories": fetched, "round_number": 1}


# ────────────────────────────────────────────
# Node: Agent worker (one per agent, fan-out via Send)
# ────────────────────────────────────────────

async def agent_worker_node(state: AgentWorkerState, config: dict):
    agent = state["agent_config"]
    key = agent["key"]
    round_num = state["round_number"]

    await _emit(config, "agent_start", {
        "key": key,
        "name": agent["name"],
        "role": agent["role"],
        "color": agent["color"],
    })

    # Build user message — round 1 is just the enriched prompt,
    # round 2+ includes previous response + moderator feedback
    if round_num == 1:
        user_msg = state["enriched_prompt"]
    else:
        user_msg = (
            f"{state['enriched_prompt']}\n\n---\n\n"
            f"## Your Previous Response (Round {round_num - 1}):\n"
            f"{state.get('previous_response', '')}\n\n---\n\n"
            f"## Moderator Feedback — The Council Needs More:\n"
            f"{state.get('cross_pollination', '')}\n\n"
            f"Revise or deepen your analysis based on this feedback. "
            f"You may change your position if the evidence warrants it."
        )

    llm = _get_llm(temperature=0.7)
    messages = [
        SystemMessage(content=agent["system_prompt"]),
        HumanMessage(content=user_msg),
    ]

    full = ""
    async for chunk in llm.astream(messages):
        if chunk.content:
            full += chunk.content
            await _emit(config, "agent_chunk", {"key": key, "chunk": chunk.content})

    clean = _strip_context_request(full)

    await _emit(config, "agent_done", {"key": key})

    return {
        "agent_responses": [{
            "key": key,
            "name": agent["name"],
            "role": agent["role"],
            "color": agent["color"],
            "response": clean,
            "round": round_num,
        }],
    }


# ────────────────────────────────────────────
# Node: Moderator
# ────────────────────────────────────────────

async def moderator_node(state: JudgeTableState, config: dict):
    round_num = state["round_number"]

    await _emit(config, "moderator_start", {
        "name": MODERATOR["name"],
        "role": MODERATOR["role"],
        "color": MODERATOR["color"],
    })

    # Current round's responses only
    current = [r for r in state.get("agent_responses", []) if r.get("round") == round_num]

    weights = config.get("configurable", {}).get("agent_weights", {})
    council_parts = []
    for r in current:
        w = weights.get(r["key"], 1.0)
        note = f" [weight: {w}x]" if w != 1.0 else ""
        council_parts.append(f"**{r['name']}** ({r['role']}){note}:\n{r['response']}")

    mod_msg = (
        f"## Decision: {state['decision']}\n\n"
        f"## Council Perspectives (Round {round_num})\n\n"
        + "\n\n---\n\n".join(council_parts)
    )

    llm = _get_llm(temperature=0.5)
    messages = [
        SystemMessage(content=MODERATOR["system_prompt"]),
        HumanMessage(content=mod_msg),
    ]

    full = ""
    async for chunk in llm.astream(messages):
        if chunk.content:
            full += chunk.content
            await _emit(config, "moderator_chunk", {"chunk": chunk.content})

    await _emit(config, "moderator_done", {})

    decision, cross_poll = _parse_moderator_decision(full)
    dissent = _parse_dissent_flags(full)

    if dissent:
        await _emit(config, "dissent", dissent)

    await _emit(config, "moderator_decision", {
        "decision": decision,
        "round": round_num,
        "cross_pollination": cross_poll if decision == "CONTINUE" else "",
    })

    result: dict = {
        "moderator_response": full,
        "moderator_decision": decision,
        "cross_pollination": cross_poll,
        "dissent_flags": dissent,
    }

    # Increment round if continuing
    if decision == "CONTINUE" and round_num < MAX_ROUNDS:
        result["round_number"] = round_num + 1

    return result


# ────────────────────────────────────────────
# Node: Synthesizer
# ────────────────────────────────────────────

async def synthesizer_node(state: JudgeTableState, config: dict):
    await _emit(config, "synthesizer_start", {
        "name": SYNTHESIZER["name"],
        "role": SYNTHESIZER["role"],
        "color": SYNTHESIZER["color"],
    })

    # Group responses by round
    by_round: dict[int, list] = {}
    for r in state.get("agent_responses", []):
        rnd = r.get("round", 1)
        by_round.setdefault(rnd, []).append(r)

    parts = [f"## Decision: {state['decision']}\n"]
    for rnd in sorted(by_round):
        parts.append(f"\n## Council Round {rnd}")
        for r in by_round[rnd]:
            parts.append(f"\n**{r['name']}** ({r['role']}):\n{r['response']}")

    parts.append(f"\n\n## Final Moderator Analysis\n\n{state.get('moderator_response', '')}")
    synth_msg = "\n".join(parts)

    llm = _get_llm(temperature=0.6)
    messages = [
        SystemMessage(content=SYNTHESIZER["system_prompt"]),
        HumanMessage(content=synth_msg),
    ]

    full = ""
    async for chunk in llm.astream(messages):
        if chunk.content:
            full += chunk.content
            await _emit(config, "synthesizer_chunk", {"chunk": chunk.content})

    await _emit(config, "synthesizer_done", {})

    return {"synthesizer_response": full}


# ────────────────────────────────────────────
# Edge functions
# ────────────────────────────────────────────

def _build_agent_sends(state: dict) -> list[Send]:
    """Build Send objects for all 8 agents."""
    responses = state.get("agent_responses", [])
    round_num = state.get("round_number", 1)
    cross_poll = state.get("cross_pollination", "")

    sends = []
    for agent in AGENTS:
        # Find this agent's most recent response (for round > 1)
        prev = ""
        if round_num > 1:
            prev_list = [r for r in responses if r["key"] == agent["key"]]
            if prev_list:
                prev = prev_list[-1]["response"]

        sends.append(Send("agent_worker", {
            "enriched_prompt": state["enriched_prompt"],
            "round_number": round_num,
            "previous_response": prev,
            "cross_pollination": cross_poll,
            "agent_config": agent,
        }))
    return sends


def dispatch_agents(state: JudgeTableState) -> list[Send]:
    """After amplifier: fan out to all 8 agents."""
    return _build_agent_sends(state)


def should_continue(state: JudgeTableState):
    """After moderator: synthesize or loop back."""
    if (
        state.get("moderator_decision") == "SATISFIED"
        or state.get("round_number", 1) >= MAX_ROUNDS
    ):
        return "synthesizer"
    # CONTINUE — re-dispatch all agents with cross-pollination
    return _build_agent_sends(state)


# ────────────────────────────────────────────
# Build the graph
# ────────────────────────────────────────────

def build_graph() -> StateGraph:
    builder = StateGraph(JudgeTableState)

    builder.add_node("amplifier", amplifier_node)
    builder.add_node("agent_worker", agent_worker_node)
    builder.add_node("moderator", moderator_node)
    builder.add_node("synthesizer", synthesizer_node)

    builder.add_edge(START, "amplifier")
    builder.add_conditional_edges("amplifier", dispatch_agents, ["agent_worker"])
    builder.add_edge("agent_worker", "moderator")
    builder.add_conditional_edges("moderator", should_continue, ["synthesizer", "agent_worker"])
    builder.add_edge("synthesizer", END)

    return builder.compile()
