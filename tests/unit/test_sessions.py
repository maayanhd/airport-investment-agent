from airport_agent.sessions import SessionStore


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _store(clock: Clock, *, ttl: float = 60, max_sessions: int = 3) -> SessionStore:
    return SessionStore(
        object,  # type: ignore[arg-type]
        ttl_seconds=ttl,
        max_sessions=max_sessions,
        clock=clock,
    )


def test_a_session_is_found_by_its_id_and_ids_are_unique() -> None:
    store = _store(Clock())
    first, session = store.create()
    second, _ = store.create()

    assert first != second
    assert store.get(first) is session
    assert store.get("0" * 32) is None


def test_an_idle_session_expires_but_use_keeps_it_alive() -> None:
    clock = Clock()
    store = _store(clock, ttl=60)
    kept, _ = store.create()
    idle, _ = store.create()

    clock.now = 50
    assert store.get(kept) is not None
    clock.now = 100

    assert store.get(kept) is not None
    assert store.get(idle) is None


def test_the_least_recently_used_session_goes_when_the_store_is_full() -> None:
    store = _store(Clock(), max_sessions=2)
    oldest, _ = store.create()
    middle, _ = store.create()
    store.get(oldest)
    newest, _ = store.create()

    assert store.get(middle) is None
    assert store.get(oldest) is not None
    assert store.get(newest) is not None
    assert len(store) == 2


def test_a_discarded_session_is_gone_and_discarding_twice_is_harmless() -> None:
    store = _store(Clock())
    session_id, _ = store.create()

    store.discard(session_id)
    store.discard(session_id)

    assert store.get(session_id) is None
    assert len(store) == 0
