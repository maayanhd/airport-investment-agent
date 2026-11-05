"""The agent loop: the model picks tools, code runs them, a validator checks the answer."""

import logging
from collections import deque
from dataclasses import dataclass, field

from airport_agent.answer import Answer, Status, build_notes
from airport_agent.config import Settings
from airport_agent.llm import LlmClient, LlmError, Message, ToolCall, ToolOutput
from airport_agent.prompts import (
    ABSTAIN_TEXT,
    SYSTEM_PROMPT,
    UNAVAILABLE_TEXT,
    entities_note,
    rejection_feedback,
)
from airport_agent.tools.registry import ToolRegistry
from airport_agent.tools.result import ToolResult
from airport_agent.validator import Violation, ViolationKind, gather_evidence, validate_answer

logger = logging.getLogger(__name__)
EMPTY_ANSWER = "(empty answer)"


@dataclass
class Turn:
    messages: list[Message]
    results: list[ToolResult] = field(default_factory=list)


class Agent:
    """One conversation. Keeps the last `history_turns` turns so follow-ups can be resolved."""

    def __init__(self, llm: LlmClient, registry: ToolRegistry, settings: Settings) -> None:
        self._llm = llm
        self._registry = registry
        self._settings = settings
        self._turns: deque[Turn] = deque(maxlen=settings.history_turns)

    def ask(self, question: str) -> Answer:
        turn = Turn([Message("user", text=question)])

        try:
            answer = self._converse(turn, question)
        except LlmError:
            logger.exception("model call failed")

            return Answer(Status.UNAVAILABLE, UNAVAILABLE_TEXT)

        if answer.status is Status.ANSWERED:
            self._turns.append(turn)
        else:  # keep the question but never the rejected drafts
            self._turns.append(Turn([turn.messages[0], Message("assistant", text=answer.text)]))

        return answer

    def _converse(self, turn: Turn, question: str) -> Answer:
        rounds = 0
        rejections = 0

        while True:
            reply = self._llm.complete(
                system=self._system(),
                messages=[m for t in self._turns for m in t.messages] + turn.messages,
                tools=self._registry.definitions(),
            )
            turn.messages.append(reply)

            if reply.tool_calls:
                rounds += 1

                if rounds > self._settings.max_tool_rounds:
                    return self._abstain(("too many tool rounds",))

                turn.messages.append(self._run_tools(reply.tool_calls, turn))
                continue

            violations = self._check(reply.text, turn, question)

            if not violations:
                return Answer(
                    Status.ANSWERED, reply.text, build_notes(turn.results), tuple(turn.results)
                )

            rejections += 1

            if rejections > self._settings.validation_retries:
                return self._abstain(tuple(v.text for v in violations))

            turn.messages.append(Message("user", text=rejection_feedback(violations)))

    def _system(self) -> str:
        note = entities_note(self._turns[-1].results) if self._turns else ""

        return f"{SYSTEM_PROMPT}\n{note}" if note else SYSTEM_PROMPT

    def _run_tools(self, calls: tuple[ToolCall, ...], turn: Turn) -> Message:
        outputs = []

        for call in calls:
            result = self._registry.call(call.name, call.arguments)
            turn.results.append(result)
            outputs.append(ToolOutput(call.id, result.model_dump_json(exclude={"sources"})))

        return Message("user", tool_outputs=tuple(outputs))

    def _check(self, text: str, turn: Turn, question: str) -> list[Violation]:
        if not text.strip():
            return [Violation(ViolationKind.EMPTY, EMPTY_ANSWER)]

        results = [r for t in self._turns for r in t.results] + turn.results
        settings = self._settings

        return validate_answer(
            text,
            gather_evidence(results, question),
            coverage=settings.coverage_airports,
            max_plain_count=settings.max_plain_count,
            years=settings.year_range,
        )

    @staticmethod
    def _abstain(problems: tuple[str, ...]) -> Answer:
        return Answer(Status.ABSTAINED, ABSTAIN_TEXT, problems=problems)
