"""Streamlit chat: `streamlit run ui/app.py`. Talks to the API; holds no data and no model key."""

import os

import httpx
import streamlit as st

from airport_agent.chat_render import UNREACHABLE, Reply, escape_markdown, reply_from_response
from airport_agent.config import load_settings

API_URL = os.environ.get("AIRPORT_AGENT_API", "http://127.0.0.1:8000")
WAITING_TEXT = "Checking the data and writing the answer..."
TIMEOUT_SECONDS = load_settings().request_budget_seconds  # as long as the server may take


def ask(message: str, session_id: str | None) -> Reply:
    body = {"message": message, "session_id": session_id}

    try:
        response = httpx.post(f"{API_URL}/chat", json=body, timeout=TIMEOUT_SECONDS)
    except (httpx.HTTPError, httpx.InvalidURL):
        return reply_from_response(UNREACHABLE, "")

    return reply_from_response(response.status_code, response.text)


def show(role: str, reply: Reply) -> None:
    with st.chat_message(role):
        st.markdown(reply.text)

        if reply.label:
            st.warning(reply.label)

        if reply.notes:
            with st.expander("Assumptions, caveats and data sources"):
                st.markdown(reply.notes)


def main() -> None:
    st.title("Airport Investment Intelligence Agent")
    st.caption("US airports only. Scores and delay rates are proxies, not observed demand.")

    if "history" not in st.session_state:
        st.session_state.history = []
        st.session_state.session_id = None

    for role, reply in st.session_state.history:
        show(role, reply)

    message = st.chat_input("Ask about a US airport or region")

    if not message:
        return

    question = Reply(escape_markdown(message))
    show("user", question)

    with st.spinner(WAITING_TEXT):
        answer = ask(message, st.session_state.session_id)

    if answer.reset_session:
        st.session_state.session_id = None
    elif answer.session_id:
        st.session_state.session_id = answer.session_id

    st.session_state.history += [("user", question), ("assistant", answer)]
    show("assistant", answer)


main()
