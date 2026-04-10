"""
Amplifier Agent — the intelligent router that sits between the user and the council.

Uses Groq tool calling to selectively fetch KB data from Postgres,
then builds an enriched prompt tailored to the user's query.
Also handles agent callbacks when agents need more context mid-deliberation.
"""

import json

from groq import AsyncGroq
from database import get_knowledge

MODEL = "llama-3.3-70b-versatile"

SYSTEM_PROMPT = """\
You are the Amplifier Agent. You are the first step in a council of 8 advisors who help a person think through their decisions.

Your job is to prepare the richest possible context for the council. You have access to the person's knowledge base stored in a database.

**Your process:**
1. Read the person's query carefully
2. Decide which knowledge base categories are relevant to THIS specific query
3. Fetch them using your tool
4. Compose a clear, structured context brief that will be prepended to the user's query

**Available categories:**
- `personality` — Who they are, traits, tendencies, communication style
- `goals` — What they're working toward, short and long-term
- `values` — Core principles, non-negotiables, what guides their decisions
- `blind_spots` — Known patterns, repeated mistakes, tendencies they struggle with
- `context_log` — Recent events, conversations, actions, what's been happening lately
- `relationships` — Key people in their life and the dynamics
- `challenges` — Current friction points, obstacles, active problems

**Rules:**
- Do NOT fetch everything. Only fetch what's genuinely relevant to this query.
- A career decision needs goals, values, maybe relationships and challenges — probably not personality.
- A relationship question needs relationships, values, blind_spots — probably not goals.
- Use your judgment. Quality of context > quantity.

**Output format:**
After fetching, write a structured brief with clear sections. Start with:
"## Context Brief for the Council"
Then include each fetched category as a subsection. End with:
"## The Decision"
And restate the user's decision and any additional context they provided.

Be concise but complete. The council agents will read this brief before deliberating."""

CALLBACK_SYSTEM_PROMPT = """\
You are the Amplifier Agent handling a context callback. An agent in the council has requested additional context during deliberation.

You will receive:
1. The original query
2. The agent's name and what context they're requesting
3. Access to the knowledge base

Fetch the relevant data and compose a focused context supplement. Be concise — only include what was specifically requested. Format it as:

"## Additional Context (requested by {agent_name})"
Then the relevant information."""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "fetch_knowledge",
            "description": "Fetch entries from the user's knowledge base for specific categories. Returns all entries in those categories.",
            "parameters": {
                "type": "object",
                "properties": {
                    "categories": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "personality", "goals", "values",
                                "blind_spots", "context_log",
                                "relationships", "challenges",
                            ],
                        },
                        "description": "Which knowledge base categories to fetch",
                    },
                },
                "required": ["categories"],
            },
        },
    },
]


def _format_entries(entries_by_category: dict) -> str:
    """Format KB entries into readable text for the LLM."""
    parts = []
    category_labels = {
        "personality": "Personality Profile",
        "goals": "Goals & Aspirations",
        "values": "Values & Principles",
        "blind_spots": "Known Blind Spots & Patterns",
        "context_log": "Recent Context & Events",
        "relationships": "Key Relationships",
        "challenges": "Current Challenges",
    }
    for cat, entries in entries_by_category.items():
        label = category_labels.get(cat, cat)
        if not entries:
            parts.append(f"### {label}\n(No entries found)")
        else:
            items = "\n".join(f"- **{e['title']}**: {e['content']}" for e in entries)
            parts.append(f"### {label}\n{items}")
    return "\n\n".join(parts)


async def run_amplifier(
    client: AsyncGroq,
    user_id: str,
    decision: str,
    context: str,
    on_tool_call: callable = None,
) -> dict:
    """
    Run the amplifier agent's tool-calling loop.

    Returns:
        {
            "enriched_prompt": str,      # The full context brief + decision
            "fetched_categories": list,   # Which KB categories were fetched
            "context_summary": str,       # Short summary for the UI
        }
    """
    user_msg = f"Here is the person's decision:\n\n{decision}"
    if context:
        user_msg += f"\n\nAdditional context they provided:\n{context}"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    fetched_categories = []
    max_iterations = 5

    for _ in range(max_iterations):
        response = await client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            temperature=0.3,
            max_tokens=2048,
        )

        msg = response.choices[0].message
        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ] if msg.tool_calls else None,
        })

        if not msg.tool_calls:
            # Done — the final message content is the enriched prompt
            return {
                "enriched_prompt": msg.content or "",
                "fetched_categories": fetched_categories,
                "context_summary": f"Loaded {', '.join(fetched_categories)}" if fetched_categories else "No KB data needed",
            }

        # Execute tool calls
        for tool_call in msg.tool_calls:
            if tool_call.function.name == "fetch_knowledge":
                args = json.loads(tool_call.function.arguments)
                categories = args.get("categories", [])

                entries_by_cat = {}
                for cat in categories:
                    entries = await get_knowledge(user_id, cat)
                    # Convert UUID and datetime for JSON serialization
                    for e in entries:
                        e["id"] = str(e["id"])
                        e["created_at"] = str(e["created_at"])
                        e["updated_at"] = str(e["updated_at"])
                    entries_by_cat[cat] = entries
                    if cat not in fetched_categories:
                        fetched_categories.append(cat)

                if on_tool_call:
                    await on_tool_call(categories)

                result_text = _format_entries(entries_by_cat)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result_text if result_text else "No entries found in these categories.",
                })

    # Fallback if max iterations hit
    last_content = messages[-1].get("content", "") if isinstance(messages[-1], dict) else ""
    return {
        "enriched_prompt": last_content,
        "fetched_categories": fetched_categories,
        "context_summary": f"Loaded {', '.join(fetched_categories)}" if fetched_categories else "No KB data loaded",
    }


async def handle_agent_callback(
    client: AsyncGroq,
    user_id: str,
    decision: str,
    agent_name: str,
    context_request: str,
) -> str:
    """
    Handle a context callback from an agent that needs more information.

    Returns: additional context string to append to the agent's conversation.
    """
    messages = [
        {"role": "system", "content": CALLBACK_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Original decision: {decision}\n\n"
                f"Agent requesting context: {agent_name}\n"
                f"What they need: {context_request}"
            ),
        },
    ]

    max_iterations = 3

    for _ in range(max_iterations):
        response = await client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            temperature=0.3,
            max_tokens=1024,
        )

        msg = response.choices[0].message
        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ] if msg.tool_calls else None,
        })

        if not msg.tool_calls:
            return msg.content or ""

        for tool_call in msg.tool_calls:
            if tool_call.function.name == "fetch_knowledge":
                args = json.loads(tool_call.function.arguments)
                categories = args.get("categories", [])

                entries_by_cat = {}
                for cat in categories:
                    entries = await get_knowledge(user_id, cat)
                    for e in entries:
                        e["id"] = str(e["id"])
                        e["created_at"] = str(e["created_at"])
                        e["updated_at"] = str(e["updated_at"])
                    entries_by_cat[cat] = entries

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": _format_entries(entries_by_cat) or "No entries found.",
                })

    return ""
