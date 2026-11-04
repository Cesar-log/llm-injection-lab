"""LLM Injection Lab - chat interface.

Run with:  streamlit run app.py
"""
import streamlit as st

from levels import LEVELS
from levels.base import SECRET
from llm import MODEL, LLMError, chat

st.set_page_config(page_title="LLM Injection Lab", page_icon="🔓")

# ---------- sidebar: level picker + flag check ----------
with st.sidebar:
    st.title("🔓 LLM Injection Lab")
    names = [f"Level {lv.number} - {lv.name}" for lv in LEVELS]
    idx = st.selectbox("Level", range(len(LEVELS)), format_func=lambda i: names[i])
    level = LEVELS[idx]
    st.caption(f"Model: `{MODEL}`")

    st.divider()
    guess = st.text_input("Found the password? Submit it here:")
    if guess:
        if guess.strip() == SECRET:
            st.success("Correct! Level cleared. 🎉")
        else:
            st.error("Wrong password.")

    if st.button("Reset chat"):
        st.session_state.pop(f"history_{idx}", None)
        st.rerun()

# ---------- main: chat ----------
st.header(f"Level {level.number}: {level.name}")
st.write(level.description)

key = f"history_{idx}"
history = st.session_state.setdefault(key, [])

for msg in history:
    with st.chat_message(msg["role"]):
        st.write(msg["content"])

prompt = st.chat_input("Talk to ShopBot...")
if prompt:
    history.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.write(prompt)

    with st.chat_message("assistant"):
        blocked = level.check_input(prompt) if level.check_input else None
        if blocked:
            answer = blocked
        else:
            try:
                with st.spinner("Thinking..."):
                    answer = chat(level.system_prompt, history, level.examples)
            except LLMError as e:
                history.pop()  # don't keep a question with no answer
                st.error(str(e))
                st.stop()
            if level.check_output:
                answer = level.check_output(answer) or answer
        st.write(answer)

    history.append({"role": "assistant", "content": answer})
