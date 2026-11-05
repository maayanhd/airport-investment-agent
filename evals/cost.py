"""What a run cost, from token counts and the prices in `config.yaml`."""

from airport_agent.config import ModelPrice
from airport_agent.llm import Usage

MILLION = 1_000_000


def cost_usd(usage: Usage, price: ModelPrice) -> float:
    input_cost = usage.input_tokens * price.input_usd_per_million
    output_cost = usage.output_tokens * price.output_usd_per_million

    return (input_cost + output_cost) / MILLION
