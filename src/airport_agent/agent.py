"""The agent loop: the model picks tools, code runs them, a validator checks the answer."""

import logging
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field

from airport_agent.answer import NO_DATA, Answer, Status, build_notes
from airport_agent.config import Settings
from airport_agent.dataset import Dataset
from airport_agent.evidence import Evidence, gather_evidence
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
from airport_agent.tools import build_registry
from airport_agent.tools.registry import ToolRegistry
from airport_agent.tools.result import ToolResult
from airport_agent.validator import Rules, Violation, ViolationKind, validate_answer

logger = logging.getLogger(__name__)
EMPTY_ANSWER = "(empty answer)"
TOO_MANY_ROUNDS = "too many tool rounds"


@dataclass
class Turn:
    """One question and what came of it. `messages` is what the model sees this turn; `kept` is
    what later turns see, so a rejected draft and its feedback are left out of it."""

    messages: list[Message]
    results: list[ToolResult] = field(default_factory=list)
    kept: list[Message] = field(default_factory=list)
    system: str = ""
    evidence: Evidence | None = None  # rebuilt only when a tool has run since

    @classmethod
    def start(cls, question: str, system: str) -> "Turn":
        message = Message("user", text=question)

        return cls([message], kept=[message], system=system)

    def add(self, message: Message, *, keep: bool) -> None:
        self.messages.append(message)

        if keep:
            self.kept.append(message)


class Agent:
    """One conversation. Keeps the last `history_turns` turns so follow-ups can be resolved."""

    def __init__(
        self,
        llm: LlmClient,
        registry: ToolRegistry,
        settings: Settings,
        city_codes: Mapping[str, frozenset[str]],
        *,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> None:
        self._llm = llm
        self._system_prompt = system_prompt
        self._registry = registry
        self._settings = settings
        self._rules = Rules.from_settings(settings, city_codes)
        self._tools = registry.definitions()
        self._turns: deque[Turn] = deque(maxlen=settings.history_turns)

    def ask(self, question: str) -> Answer:
        turn = Turn.start(question, self._system())

        try:
            answer = self._converse(turn, question)
        except LlmError:
            logger.exception("model call failed")

            return Answer(Status.UNAVAILABLE, UNAVAILABLE_TEXT)

        if answer.status is Status.ANSWERED:
            self._turns.append(Turn(turn.kept, turn.results))
        else:
            self._turns.append(self._declined(turn, answer))

        return answer

    @staticmethod
    def _declined(turn: Turn, answer: Answer) -> Turn:
        """Keep the question and the tools' results, so options stay visible, but not the drafts."""
        kept = list(turn.kept)

        while kept and kept[-1].role == "assistant":  # the last draft, or a call never run
            kept.pop()

        return Turn([*kept, Message("assistant", text=answer.text)], list(turn.results))

    def _converse(self, turn: Turn, question: str) -> Answer:
        rounds = 0
        rejections = 0

        while True:
            reply = self._next_reply(turn, with_tools=rounds < self._settings.max_tool_rounds)

            if reply.tool_calls:
                if rounds >= self._settings.max_tool_rounds:
                    return self._fall_back(turn, (TOO_MANY_ROUNDS,))

                rounds += 1
                turn.add(self._run_tools(reply.tool_calls, turn), keep=True)
                continue

            violations = self._check(reply.text, turn, question)

            if not violations:
                return self._answered(turn, reply)

            rejections += 1

            if rejections > self._settings.validation_retries:
                return self._fall_back(turn, tuple(v.text for v in violations))

            self._reject(turn, violations)

    def _next_reply(self, turn: Turn, *, with_tools: bool) -> Message:
        """After the last allowed tool round the model gets no tools, so it must answer."""
        reply = self._llm.complete(
            system=turn.system,
            messages=[m for t in self._turns for m in t.messages] + turn.messages,
            tools=self._tools,
            allow_tools=with_tools,
        )
        turn.add(reply, keep=bool(reply.tool_calls))  # a text draft is kept only once accepted

        return reply

    def _system(self) -> str:
        last = next((t for t in reversed(self._turns) if any(r.ok for r in t.results)), None)
        note = entities_note(last.results, self._settings.remembered_airports) if last else ""

        return f"{self._system_prompt}\n{note}" if note else self._system_prompt

    def _run_tools(self, calls: tuple[ToolCall, ...], turn: Turn) -> Message:
        outputs = []

        for call in calls:
            result = self._registry.call(call.name, call.arguments)
            turn.results.append(result)
            turn.evidence = None
            outputs.append(ToolOutput(call.id, result.model_dump_json(exclude={"sources"})))

        return Message("user", tool_outputs=tuple(outputs))

    def _check(self, text: str, turn: Turn, question: str) -> list[Violation]:
        if not text.strip():
            return [Violation(ViolationKind.EMPTY, EMPTY_ANSWER)]

        if turn.evidence is None:
            turn.evidence = gather_evidence(self._basis(turn), question)

        return validate_answer(text, turn.evidence, self._rules)

    def _basis(self, turn: Turn) -> list[ToolResult]:
        """What an answer may quote: this turn's tool results, or earlier ones if it made no call.
        Pooling both would let an old number vouch for an unrelated new one."""
        return turn.results or [r for t in self._turns for r in t.results]

    def _reject(self, turn: Turn, violations: list[Violation]) -> None:
        turn.add(Message("user", text=rejection_feedback(violations)), keep=False)

    def _answered(self, turn: Turn, reply: Message) -> Answer:
        """The notes come from the same results the validator checked the reply against."""
        turn.kept.append(reply)
        basis = self._basis(turn)
        notes = build_notes(basis) if basis else NO_DATA

        return Answer(Status.ANSWERED, reply.text, notes, tuple(turn.results))

    def _fall_back(self, turn: Turn, problems: tuple[str, ...]) -> Answer:
        """Unverifiable wording: show what this turn's tools returned, or abstain if none did."""
        usable = [r for r in turn.results if r.ok]

        if not usable:
            return self._abstain(problems)

        logger.warning("showed tool figures only: %s", "; ".join(problems))
        text = f"{DATA_ONLY_TEXT}\n{figures_text(usable, self._settings.fallback_max_lines)}"

        return Answer(Status.DATA_ONLY, text, build_notes(usable), tuple(usable), problems)

    @staticmethod
    def _abstain(problems: tuple[str, ...]) -> Answer:
        logger.warning("abstained: %s", "; ".join(problems))

        return Answer(Status.ABSTAINED, ABSTAIN_TEXT, problems=problems)


def build_agent(llm: LlmClient, data: Dataset, *, system_prompt: str = SYSTEM_PROMPT) -> Agent:
    """The agent as it runs for a user: tools, settings and the covered cities all from `data`."""
    registry = build_registry(data)

    return Agent(llm, registry, data.settings, data.city_codes(), system_prompt=system_prompt)
