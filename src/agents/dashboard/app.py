"""Thin Streamlit UI over src.agents.dashboard. Streamlit is imported lazily."""
from __future__ import annotations

from src.agents import dashboard as dash


def main() -> None:
    import streamlit as st  # lazy: not installed until separately approved

    st.set_page_config(page_title="Client Attendance Agents")
    st.title("Client Attendance Agents")
    tabs = st.tabs(["Generate", "Upload", "Archive", "Recipients", "Log"])

    with tabs[0]:
        scope = st.selectbox("Report", list(dash.SCOPES))
        if st.button("Generate"):
            st.json(dash.generate(scope))

    with tabs[1]:
        up = st.file_uploader("Choose a file (staged under 07_LOGS/uploads, not the drop folder)")
        if up is not None and st.button("Save upload"):
            st.json(dash.save_upload(up.name, up.getvalue()))

    with tabs[2]:
        st.dataframe(dash.list_archive())

    with tabs[3]:
        info = dash.get_recipients()
        st.write(info["note"])
        st.json(info["counts"])

    with tabs[4]:
        name = st.selectbox("Log", dash.LOG_FILES)
        limit = st.number_input("Rows", min_value=1, max_value=1000, value=50)
        res = dash.read_log(name, int(limit))
        st.write(res["status"])
        if res["rows"]:
            st.dataframe(res["rows"])


if __name__ == "__main__":
    main()
