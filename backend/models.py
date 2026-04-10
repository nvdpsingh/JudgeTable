from pydantic import BaseModel


# --- Debate ---

class DecisionRequest(BaseModel):
    decision: str
    context: str = ""
    user_id: str = "00000000-0000-0000-0000-000000000001"


class AgentResponse(BaseModel):
    name: str
    key: str
    role: str
    color: str
    response: str


class DissentFlag(BaseModel):
    agent_name: str
    agent_key: str
    concern: str


class DebateResponse(BaseModel):
    decision: str
    agents: list[AgentResponse]
    moderator: AgentResponse
    synthesizer: AgentResponse
    dissent_flags: list[DissentFlag]


# --- Knowledge Base ---

class KnowledgeEntryCreate(BaseModel):
    category: str
    title: str
    content: str
    metadata: dict = {}


class KnowledgeEntryUpdate(BaseModel):
    title: str
    content: str
    metadata: dict = {}


class KnowledgeEntry(BaseModel):
    id: str
    category: str
    title: str
    content: str
    metadata: dict = {}


# --- Decision Log ---

class DecisionOutcome(BaseModel):
    outcome: str


# --- Agent Weights ---

class AgentWeightUpdate(BaseModel):
    agent_key: str
    weight: float
