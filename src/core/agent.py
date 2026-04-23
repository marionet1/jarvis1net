from dataclasses import dataclass

from .types import AgentConfig


@dataclass
class AgentReply:
    selected_model: str
    text: str


def run_agent_turn(user_input: str, config: AgentConfig) -> AgentReply:
    selected_model = config.model
    text = "\n".join([f"Model: {selected_model}", "", f"Input: {user_input}"])

    return AgentReply(selected_model=selected_model, text=text)
