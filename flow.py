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

st.title("ActiveCampaign Completed Flow Executions Extractor")
st.markdown("Paste Flow URLs or IDs (one per line)")

input_text = st.text_area("Flows", height=200)

# Session state
if "results_data" not in st.session_state:
    st.session_state.results_data = None
if "df" not in st.session_state:
    st.session_state.df = None


# === UTILITIES ===

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

    session = requests.Session()
    retries = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=[500, 502, 503, 503, 504],
        allowed_methods=["GET"]
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))

    while True:
        try:
            res = session.get(url, headers=headers, params=params, timeout=15)
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
            time.sleep(0.3)

        except requests.exceptions.RequestException as e:
            st.warning(f"Error fetching page {page} for flow {flow_id}: {e}")
            break

    return all_results


def extract_completed_executions(flow_id, results):
    completed_rows = []

    for r in results:
        if r.get("completed", 0) == 1:
            completed_rows.append({
                "flow_id": flow_id,
                "execution_id": r.get("id"),
                "created_on": r.get("created_on"),
                "num_contacts": r.get("num_contacts", 0),
                "flow_version": r.get("flow_version"),
                "status_last_change_on": r.get("status_last_change_on")
            })

    return completed_rows


def send_in_batches(data, batch_size=1000):
    total = len(data)
    batches = [data[i:i + batch_size] for i in range(0, total, batch_size)]

    for idx, batch in enumerate(batches):
        payload = {
            "batch_number": idx + 1,
            "total_batches": len(batches),
            "records": batch
        }

        try:
            resp = requests.post(WEBHOOK_URL, json=payload, timeout=30)

            if not resp.ok:
                st.error(f"Batch {idx + 1} failed: {resp.status_code}")
                return False

        except requests.exceptions.RequestException as e:
            st.error(f"Batch {idx + 1} error: {e}")
            return False

        st.progress((idx + 1) / len(batches))

    return True


# === RUN ANALYSIS ===

if st.button("Extract Completed Executions"):

    flows = [extract_id(x) for x in input_text.splitlines() if x.strip()]

    if not flows:
        st.warning("Enter at least one flow")
        st.stop()

    all_completed = []
    progress = st.progress(0)

    for i, flow_id in enumerate(flows):
        with st.spinner(f"Fetching {flow_id}..."):
            results = fetch_all_pages(flow_id)
            completed_rows = extract_completed_executions(flow_id, results)
            all_completed.extend(completed_rows)

        progress.progress((i + 1) / len(flows))

    # Sort chronologically (important for downstream systems)
    all_completed.sort(key=lambda x: x["created_on"] or "")

    st.session_state.results_data = all_completed
    df = pd.DataFrame(all_completed)
    st.session_state.df = df

    st.success(f"Extracted {len(all_completed)} completed executions ✅")

    if not df.empty:
        st.write("Preview (first 100 rows)")
        st.dataframe(df.head(100), use_container_width=True)

        st.download_button(
            "Download CSV",
            df.to_csv(index=False),
            file_name="completed_executions.csv"
        )


# === SEND TO WEBHOOK ===

if st.session_state.results_data is not None and st.button("Send to Webhook"):

    with st.spinner("Sending batches to webhook..."):
        success = send_in_batches(st.session_state.results_data)

        if success:
            st.success("All batches sent successfully ✅")
