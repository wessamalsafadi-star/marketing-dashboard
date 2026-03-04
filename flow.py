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
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
BASE_URL = "https://bhomes.api-us1.com/api/3/channel/whatsapp/flow-execution"

headers = {
    "Api-Token": API_TOKEN,
    "accept": "application/json"
}

st.title("ActiveCampaign WhatsApp Flow Executions Extractor")

# === TWO COLUMN INPUT ===
st.markdown("Enter one flow per line. URLs and names must match line-by-line.")
col1, col2 = st.columns(2)
with col1:
    urls_input = st.text_area("Flow URLs or IDs", height=200, placeholder="https://bhomes.activehosted.com/.../flow-id\nflow-id-2")
with col2:
    names_input = st.text_area("Flow Names", height=200, placeholder="Daily Newsletter\nPromo Campaign")

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


def derive_status(r: dict) -> str:
    """Derive real outcome status from sub-fields since top-level status can be misleading."""
    if r.get("completed", 0) == 1:
        return "COMPLETED"
    elif r.get("failed", 0) == 1:
        return "FAILED"
    elif r.get("canceled", 0) == 1:
        return "CANCELED"
    elif r.get("expired", 0) == 1:
        return "EXPIRED"
    elif r.get("running", 0) == 1:
        return "RUNNING"
    else:
        return r.get("status", "UNKNOWN")


def fetch_all_pages(flow_id):
    url = BASE_URL
    params = {"flow": flow_id, "page": 1}
    all_results = []
    page = 1

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


def extract_executions(flow_id, flow_name, results):
    """Extract all executions regardless of status."""
    rows = []
    for r in results:
        rows.append({
            "flow_name": flow_name,
            "execution_id": r.get("id"),
            "created_on": r.get("created_on"),
            "status": derive_status(r),
        })
    return rows


def send_in_batches(data, batch_size=1000):
    total = len(data)
    batches = [data[i:i + batch_size] for i in range(0, total, batch_size)]
    progress = st.progress(0)

    for idx, batch in enumerate(batches):
        payload = {
            "batch_number": idx + 1,
            "total_batches": len(batches),
            "records": batch
        }

        try:
            resp = requests.post(WEBHOOK_URL, json=payload, headers={"Authorization": f"Bearer {WEBHOOK_SECRET}"}, timeout=30)

            if not resp.ok:
                st.error(f"Batch {idx + 1}/{len(batches)} failed: {resp.status_code} — {resp.text}")
                return False
            else:
                st.write(f"✅ Batch {idx + 1}/{len(batches)} sent ({len(batch)} records)")

        except requests.exceptions.RequestException as e:
            st.error(f"Batch {idx + 1} error: {e}")
            return False

        progress.progress((idx + 1) / len(batches))

    return True


# === PARSE INPUTS ===

def parse_inputs(urls_text, names_text):
    urls = [x.strip() for x in urls_text.splitlines() if x.strip()]
    names = [x.strip() for x in names_text.splitlines() if x.strip()]

    if len(urls) != len(names):
        return None, f"Mismatch: {len(urls)} URLs but {len(names)} names. They must match line-by-line."

    flows = [{"id": extract_id(u), "name": n} for u, n in zip(urls, names)]
    return flows, None


# === RUN EXTRACTION ===

if st.button("Extract Executions"):
    flows, err = parse_inputs(urls_input or "", names_input or "")

    if err:
        st.error(err)
        st.stop()

    if not flows:
        st.warning("Enter at least one flow URL and name.")
        st.stop()

    all_executions = []
    progress = st.progress(0)

    for i, flow in enumerate(flows):
        with st.spinner(f"Fetching '{flow['name']}' ({flow['id']})..."):
            results = fetch_all_pages(flow["id"])
            rows = extract_executions(flow["id"], flow["name"], results)
            all_executions.extend(rows)
            st.write(f"✅ {flow['name']}: {len(rows)} executions fetched")

        progress.progress((i + 1) / len(flows))

    # Sort chronologically
    all_executions.sort(key=lambda x: x["created_on"] or "")

    st.session_state.results_data = all_executions
    df = pd.DataFrame(all_executions)
    st.session_state.df = df

    st.success(f"Extracted {len(all_executions)} total executions ✅")

    if not df.empty:
        st.write("Status breakdown:")
        st.dataframe(df["status"].value_counts().reset_index(), use_container_width=True)

        st.write("Preview (first 100 rows):")
        st.dataframe(df.head(100), use_container_width=True)

        st.download_button(
            "Download CSV",
            df.to_csv(index=False),
            file_name="flow_executions.csv"
        )


# === SEND TO WEBHOOK ===

if st.session_state.results_data is not None:
    st.divider()
    st.write(f"**{len(st.session_state.results_data)} records ready to send**")

    if st.button("Send to Webhook"):
        with st.spinner("Sending batches to webhook..."):
            success = send_in_batches(st.session_state.results_data)

            if success:
                st.success("All batches sent successfully ✅")
