import streamlit as st

def render(api_client):
    st.header("Status")
    ok, data = api_client.health_check()
    if ok:
        st.success("Backend: UP")
    else:
        st.error("Backend: DOWN")

    st.subheader("Details")
    st.json(data)
