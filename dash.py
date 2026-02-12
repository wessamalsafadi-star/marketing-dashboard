import streamlit as st
import requests
import pandas as pd
import time
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import os
from dotenv import load_dotenv

load_dotenv()
# === CONFIG ===
API_TOKEN = os.getenv("API_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
BASE_URL = "https://bhomes.api-us1.com/api/3/channel/whatsapp/flow-execution"

headers = {
    "Api-Token": API_TOKEN,
    "accept": "application/json"
}

st.title("ActiveCampaign Flow Analytics")
st.markdown("Paste Flow URLs or IDs (one per line)")

input_text = st.text_area("Flows", height=200)

# Store results in session state to persist between buttons
if "results_data" not in st.session_state:
    st.session_state.results_data = None
if "total_metrics" not in st.session_state:
    st.session_state.total_metrics = None
if "df" not in st.session_state:
    st.session_state.df = None

def extract_id(text):
    text = text.strip()
    if "/" in text:
        return text.rstrip("/").split("/")[-1]
    return text

def fetch_all_pages(flow_id):
    url = BASE_URL
    params = {"flow": flow_id, "page": 1}
    all_results = []
    page = 1

    # Setup session with retries
    session = requests.Session()
    retries = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))

    while True:
        try:
            res = session.get(url, headers=headers, params=params, timeout=10)
            res.raise_for_status()
            data = res.json()

            results = data.get("results", [])
            all_results.extend(results)

            next_page = data.get("next")
            if not next_page:
                break

            url = next_page
            params = None
            page += 1
            time.sleep(0.4)

        except requests.exceptions.RequestException as e:
            st.warning(f"Error fetching page {page} for flow {flow_id}: {e}")
            break

    return all_results, page

def calculate_metrics(results):
    total_contacts = sum(r.get("num_contacts", 0) for r in results)
    total_expired = sum(r.get("expired", 0) for r in results)
    total_running = sum(r.get("running", 0) for r in results)
    total_completed = sum(r.get("completed", 0) for r in results)
    total_canceled = sum(r.get("canceled", 0) for r in results)
    total_failed = sum(r.get("failed", 0) for r in results)

    contacts_sent = total_contacts - total_expired - total_failed
    deliverability = (contacts_sent / total_contacts * 100) if total_contacts else 0
    completion = (total_completed / total_contacts * 100) if total_contacts else 0

    return {
        "executions": len(results),
        "contacts": total_contacts,
        "sent": contacts_sent,
        "completed": total_completed,
        "failed": total_failed,
        "expired": total_expired,
        "deliverability %": round(deliverability, 2),
        "completion %": round(completion, 2)
    }

# === BUTTON: Run Analysis ===
if st.button("Run Analysis"):

    flows = [extract_id(x) for x in input_text.splitlines() if x.strip()]

    if not flows:
        st.warning("Enter at least one flow")
        st.stop()

    results_data = []
    progress = st.progress(0)

    for i, flow_id in enumerate(flows):
        with st.spinner(f"Fetching {flow_id}..."):
            results, pages = fetch_all_pages(flow_id)
            metrics = calculate_metrics(results)
            metrics["flow_id"] = flow_id
            metrics["pages"] = pages
            results_data.append(metrics)

        progress.progress((i + 1) / len(flows))

    # Store results in session_state
    st.session_state.results_data = results_data

    df = pd.DataFrame(results_data)
    st.session_state.df = df

    # Calculate total metrics
    total_metrics = {
        "executions": sum(d["executions"] for d in results_data),
        "contacts": sum(d["contacts"] for d in results_data),
        "sent": sum(d["sent"] for d in results_data),
        "completed": sum(d["completed"] for d in results_data),
        "failed": sum(d["failed"] for d in results_data),
        "expired": sum(d["expired"] for d in results_data),
    }
    total_metrics["deliverability %"] = round((total_metrics["sent"] / total_metrics["contacts"] * 100) 
                                               if total_metrics["contacts"] else 0, 2)
    total_metrics["completion %"] = round((total_metrics["completed"] / total_metrics["contacts"] * 100) 
                                          if total_metrics["contacts"] else 0, 2)
    st.session_state.total_metrics = total_metrics

    st.success("Analysis Complete ✅")
    st.dataframe(df, use_container_width=True)

    st.download_button(
        "Download CSV",
        df.to_csv(index=False),
        file_name="flow_metrics.csv"
    )

# === BUTTON: Send to Webhook ===
if st.session_state.results_data is not None and st.button("Send to Webhook"):
    payload = {
        "flows": st.session_state.results_data,
        "total": st.session_state.total_metrics
    }
    try:
        resp = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        if resp.ok:
            st.success("Payload sent to webhook successfully ✅")
        else:
            st.error(f"Webhook returned error: {resp.status_code}")
    except requests.exceptions.RequestException as e:
        st.error(f"Failed to send webhook: {e}")

