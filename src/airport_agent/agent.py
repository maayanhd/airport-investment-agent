"""The agent loop: the model picks tools, code runs them, a validator checks the answer."""

import logging
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field

from airport_agent.answer import NO_DATA, Answer, Status, build_notes
from airport_agent.config import Settings
from airport_agent.evidence import gather_evidence
from airport_agent.fallback import figures_text
from airport_agent.llm import LlmClient, LlmError, Message, ToolCall, ToolOutput
from airport_agent.prompts import (
    ABSTAIN_TEXT,
    DATA_ONLY_TEXT,
    SYSTEM_PROMPT,
    UNAVAILABLE_TEXT,
    entities_note,
    rejection_feedback,
)
from airport_agent.tools.registry import ToolRegistry
from airport_agent.tools.result import ToolResult
from airport_agent.validator import Rules, Violation, ViolationKind, validate_answer

logger = logging.getLogger(__name__)
EMPTY_ANSWER = "(empty answer)"
TOO_MANY_ROUNDS = "too many tool rounds"


@dataclass
class Turn:
    messages: list[Message]
    results: list[ToolResult] = field(default_factory=list)
    rejected: set[int] = field(default_factory=set)  # ids of drafts and feedback not to remember

    def remembered(self) -> list[Message]:
        return [m for m in self.messages if id(m) not in self.rejected]


class Agent:
    """One conversation. Keeps the last `history_turns` turns so follow-ups can be resolved."""

    def __init__(
        self,
        llm: LlmClient,
        registry: ToolRegistry,
        settings: Settings,
        city_codes: Mapping[str, frozenset[str]] | None = None,
    ) -> None:
        self._llm = llm
        self._registry = registry
        self._settings = settings
        self._rules = Rules.from_settings(settings, city_codes)
        self._tools = registry.definitions()
        self._turns: deque[Turn] = deque(maxlen=settings.history_turns)

    def ask(self, question: str) -> Answer:
        turn = Turn([Message("user", text=question)])

        try:
            answer = self._converse(turn, question)
        except LlmError:
            logger.exception("model call failed")

            return Answer(Status.UNAVAILABLE, UNAVAILABLE_TEXT)

        if answer.status is Status.ANSWERED:
            self._turns.append(Turn(turn.remembered(), turn.results))
        else:  # keep the question and any tool results, but never the rejected drafts
            reply = Message("assistant", text=answer.text)
            self._turns.append(Turn([turn.messages[0], reply], list(answer.results)))

        return answer

    def _converse(self, turn: Turn, question: str) -> Answer:
        rounds = 0
        rejections = 0

        while True:
            reply = self._next_reply(turn, with_tools=rounds < self._settings.max_tool_rounds)

            if reply.tool_calls:
                if rounds >= self._settings.max_tool_rounds:
                    return self._fall_back(turn, (TOO_MANY_ROUNDS,))

                rounds += 1
                turn.messages.append(self._run_tools(reply.tool_calls, turn))
                continue

            violations = self._check(reply.text, turn, question)

            if not violations:
                return self._answered(turn, reply.text)

            rejections += 1

            if rejections > self._settings.validation_retries:
                return self._fall_back(turn, tuple(v.text for v in violations))

            self._reject(turn, reply, violations)

    def _next_reply(self, turn: Turn, *, with_tools: bool) -> Message:
        """After the last allowed tool round the model gets no tools, so it must answer."""
        reply = self._llm.complete(
            system=self._system(),
            messages=[m for t in self._turns for m in t.messages] + turn.messages,
            tools=self._tools if with_tools else [],
        )
        turn.messages.append(reply)

        return reply

    def _system(self) -> str:
        last = next((t for t in reversed(self._turns) if any(r.ok for r in t.results)), None)
        note = entities_note(last.results) if last else ""

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

        return validate_answer(text, gather_evidence(results, question), self._rules)

    def _reject(self, turn: Turn, draft: Message, violations: list[Violation]) -> None:
        feedback = Message("user", text=rejection_feedback(violations))
        turn.messages.append(feedback)
        turn.rejected |= {id(draft), id(feedback)}

    def _answered(self, turn: Turn, text: str) -> Answer:
        """A reply that used no tool rests on the last results it could draw on, or on nothing."""
        basis = turn.results or (self._turns[-1].results if self._turns else [])
        notes = build_notes(basis) if basis else NO_DATA

        return Answer(Status.ANSWERED, text, notes, tuple(turn.results))

    def _fall_back(self, turn: Turn, problems: tuple[str, ...]) -> Answer:
        """Unverifiable wording: show what this turn's tools returned, or abstain if none did."""
        usable = [r for r in turn.results if r.ok]

        if not usable:
            return self._abstain(problems)

        logger.warning("showed tool figures only: %s", "; ".join(problems))
        text = f"{DATA_ONLY_TEXT}\n{figures_text(usable)}"

        return Answer(Status.DATA_ONLY, text, build_notes(usable), tuple(usable), problems)

    @staticmethod
    def _abstain(problems: tuple[str, ...]) -> Answer:
        logger.warning("abstained: %s", "; ".join(problems))

        return Answer(Status.ABSTAINED, ABSTAIN_TEXT, problems=problems)
