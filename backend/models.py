from pydantic import BaseModel


class DecisionRequest(BaseModel):
    decision: str
    context: str = ""


class AgentResponse(BaseModel):
    name: str
    role: str
    color: str
    response: str


class DebateResponse(BaseModel):
    decision: str
    agents: list[AgentResponse]
    judge: AgentResponse
