"""Conversations held in memory, one agent each, so a follow-up reaches the same history."""

import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field

from airport_agent.agent import Agent


@dataclass
class Session:
    agent: Agent
    last_used: float
    lock: threading.Lock = field(default_factory=threading.Lock)  # one question at a time


class SessionStore:
    """Sessions expire after `ttl_seconds` idle; past `max_sessions` the least recently used goes.
    Lost on restart, and not shared between processes: fine for one server, see the README."""

    def __init__(
        self,
        make_agent: Callable[[], Agent],
        *,
        ttl_seconds: float,
        max_sessions: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._make_agent = make_agent
        self._ttl = ttl_seconds
        self._max = max_sessions
        self._clock = clock
        self._sessions: OrderedDict[str, Session] = OrderedDict()
        self._guard = threading.Lock()

    def create(self) -> tuple[str, Session]:
        session = Session(self._make_agent(), self._clock())
        session_id = uuid.uuid4().hex

        with self._guard:
            self._drop_expired()

            while len(self._sessions) >= self._max:
                self._sessions.popitem(last=False)

            self._sessions[session_id] = session

        return session_id, session

    def get(self, session_id: str) -> Session | None:
        with self._guard:
            self._drop_expired()
            session = self._sessions.get(session_id)

            if session is not None:
                session.last_used = self._clock()
                self._sessions.move_to_end(session_id)

            return session

    def discard(self, session_id: str) -> None:
        with self._guard:
            self._sessions.pop(session_id, None)

    def _drop_expired(self) -> None:
        now = self._clock()
        stale = [i for i, s in self._sessions.items() if now - s.last_used > self._ttl]

        for session_id in stale:
            del self._sessions[session_id]

    def __len__(self) -> int:
        with self._guard:
            return len(self._sessions)
