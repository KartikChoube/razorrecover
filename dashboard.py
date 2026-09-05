"""
RazorRecover Recovery Dashboard

Streamlit-based dashboard for monitoring payment recovery metrics.
Run with: streamlit run dashboard.py
"""
from __future__ import annotations

import streamlit as st
import requests
import pandas as pd

API_BASE = "http://localhost:8000/api/v1"

st.set_page_config(
    page_title="RazorRecover Dashboard",
    page_icon="\U0001f4b3",
    layout="wide",
)

# ── Custom CSS ──────────────────────────────────────────────────────
RAZORPAY_BLUE = "#3395FF"

st.markdown(f"""
<style>
    .block-container {{ padding-top: 1rem; }}

    /* Metric cards: dark text on light blue-gray background */
    div[data-testid="stMetric"] {{
        background-color: #eef4fb;
        border: 1px solid #d0e0f0;
        border-radius: 10px;
        padding: 14px 18px;
    }}
    div[data-testid="stMetric"] label {{
        font-size: 0.85rem !important;
        color: #333333 !important;
        font-weight: 600 !important;
    }}
    div[data-testid="stMetric"] [data-testid="stMetricValue"] {{
        color: #111111 !important;
        font-weight: 700 !important;
    }}
    div[data-testid="stMetric"] [data-testid="stMetricDelta"] {{
        color: #555555 !important;
    }}

    /* Brand badge */
    .rzp-badge {{
        display: inline-block;
        background-color: {RAZORPAY_BLUE};
        color: white;
        padding: 4px 14px;
        border-radius: 20px;
        font-size: 0.78rem;
        font-weight: 600;
        letter-spacing: 0.3px;
        margin-bottom: 4px;
    }}

    /* Sidebar primary button brand color */
    .stButton > button[kind="primary"] {{
        background-color: {RAZORPAY_BLUE} !important;
        border-color: {RAZORPAY_BLUE} !important;
    }}
</style>
""", unsafe_allow_html=True)


# ── API Helpers ─────────────────────────────────────────────────────

BATCH_TIMEOUT = 120   # long-running: classify, intervene
QUICK_TIMEOUT = 30    # retries, metrics compute


def api_get(endpoint: str, params: dict | None = None, timeout: int = 10):
    """GET request to the FastAPI backend."""
    try:
        r = requests.get(f"{API_BASE}{endpoint}", params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.ConnectionError:
        return None
    except requests.exceptions.ReadTimeout:
        st.error(f"\u23f1\ufe0f **Timeout** reading from `{endpoint}` after {timeout}s.")
        return None
    except requests.exceptions.RequestException as e:
        st.error(f"API error: {e}")
        return None


def api_post(endpoint: str, timeout: int = QUICK_TIMEOUT, params: dict | None = None):
    """POST request to the FastAPI backend."""
    try:
        r = requests.post(f"{API_BASE}{endpoint}", params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.ReadTimeout:
        st.error(
            f"\u23f1\ufe0f **Timeout** on `{endpoint}` after {timeout}s. "
            f"The batch is likely still processing server-side. "
            f"Wait a moment and check metrics, or try a smaller batch."
        )
        return None
    except requests.exceptions.ConnectionError:
        st.error(
            "\u274c **Connection refused.** Is the FastAPI server running? "
            "Start it with: `uvicorn app.main:app --reload`"
        )
        return None
    except requests.exceptions.RequestException as e:
        st.error(f"API error on `{endpoint}`: {e}")
        return None


# ── Header ──────────────────────────────────────────────────────────

st.markdown('<span class="rzp-badge">Built for Razorpay AI Buildathon 2026 \u2014 Track 3: AI Revenue Recovery</span>', unsafe_allow_html=True)
st.title("\U0001f6e1\ufe0f RazorRecover")
st.caption("AI-Powered Failed Payment Recovery Agent")
st.divider()


# ── Sidebar: Pipeline Controls ──────────────────────────────────────

def run_all_batches(endpoint: str, timeout: int, step_desc: str, limit: int = 50, max_iter: int = 15) -> int:
    """Loop through endpoint in batches of 50 until all items are processed."""
    total_processed = 0
    status_box = st.empty()
    for batch_num in range(1, max_iter + 1):
        status_box.info(f"{step_desc} \u2014 Batch {batch_num} (processed {total_processed} so far)...")
        res = api_post(endpoint, timeout=timeout, params={"limit": limit})
        if not res:
            break
        cnt = res.get("processed", 0)
        total_processed += cnt
        if cnt < limit or cnt == 0:
            break
    status_box.empty()
    return total_processed


with st.sidebar:
    st.header("\U0001f680 Pipeline Controls")
    st.caption("Run the full recovery pipeline or individual steps.")

    if st.button("\u25b6\ufe0f  Run Full Pipeline", type="primary", use_container_width=True):
        progress = st.progress(0, text="Starting pipeline...")

        # Step 1: Classify all failed transactions
        progress.progress(0.1, text="\U0001f9e0 Classifying failed transactions via Groq API...")
        n_classified = run_all_batches(
            "/classifications/run-batch",
            BATCH_TIMEOUT,
            "\U0001f9e0 Classifying via Groq API",
            limit=50
        )
        st.success(f"Classified {n_classified} transaction(s) \u2714")

        # Step 2: Apply policy engine to all
        progress.progress(0.4, text="\u2696\ufe0f Applying deterministic policy engine...")
        n_intervened = run_all_batches(
            "/interventions/process-batch",
            BATCH_TIMEOUT,
            "\u2696\ufe0f Applying policy engine",
            limit=50
        )
        st.success(f"Decided interventions for {n_intervened} transaction(s) \u2714")

        # Step 3: Execute due retries
        progress.progress(0.7, text="\U0001f504 Processing due retries via Razorpay API...")
        retry_res = api_post("/interventions/process-due-retries", timeout=QUICK_TIMEOUT)
        if retry_res:
            st.success(f"Retried {retry_res.get('processed', 0)} transaction(s) (Succeeded: {retry_res.get('succeeded', 0)}) \u2714")

        # Step 4: Compute recovery metrics
        progress.progress(0.9, text="\U0001f4ca Computing recovery metrics...")
        api_post("/metrics/compute", timeout=QUICK_TIMEOUT)
        progress.progress(1.0, text="Pipeline complete!")
        st.balloons()
        st.rerun()

    st.divider()
    st.subheader("Individual Steps")

    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("Classify", use_container_width=True):
            n_done = run_all_batches(
                "/classifications/run-batch",
                BATCH_TIMEOUT,
                "\U0001f9e0 Classifying via Groq API",
                limit=50
            )
            st.success(f"Classified {n_done} transaction(s)!")
            st.rerun()
    with col_b:
        if st.button("Intervene", use_container_width=True):
            n_done = run_all_batches(
                "/interventions/process-batch",
                BATCH_TIMEOUT,
                "\u2696\ufe0f Applying policy engine",
                limit=50
            )
            st.success(f"Decided {n_done} intervention(s)!")
            st.rerun()

    col_c, col_d = st.columns(2)
    with col_c:
        if st.button("Retries", use_container_width=True):
            with st.spinner("\U0001f504 Retrying via Razorpay API..."):
                r_res = api_post("/interventions/process-due-retries")
                if r_res:
                    st.success(f"Retried {r_res.get('processed', 0)} (Succeeded: {r_res.get('succeeded', 0)})")
                    st.rerun()
    with col_d:
        if st.button("Metrics", use_container_width=True):
            with st.spinner("\U0001f4ca Computing metrics..."):
                if api_post("/metrics/compute"):
                    st.rerun()

    st.divider()
    st.subheader("\U0001f6d1 Stopping Rules")
    st.markdown("""
    - **Max retries**: 3 (low-value) / 2 (mid-value)
    - **Escalate if**: amount > \u20b910,000
    - **Escalate if**: AI confidence < 50%
    - **Expired card**: never retry, always notify
    - **Unknown failure**: always escalate
    """)


# ── Main Content ────────────────────────────────────────────────────

metrics = api_get("/metrics/summary")

if metrics is None:
    st.warning(
        "\u26a0\ufe0f  **Backend API not reachable.** "
        "Start the FastAPI server with `uvicorn app.main:app --reload` "
        "then refresh this page."
    )
    st.stop()

# ── Row 1: Key Metric Cards ─────────────────────────────────────────

total_tx = metrics.get("total_transactions", 0)
total_failed = metrics.get("total_failed", 0)
rev_at_risk = float(metrics.get("revenue_at_risk", 0))
rev_recovered = float(metrics.get("revenue_recovered", 0))
recovery_rate = float(metrics.get("recovery_rate", 0))

c1, c2, c3, c4 = st.columns(4)

with c1:
    st.metric(
        "Failed Transactions",
        f"{total_failed:,}",
        delta=f"of {total_tx:,} total",
        delta_color="off",
    )
with c2:
    st.metric(
        "\u20b9 Revenue at Risk",
        f"\u20b9{rev_at_risk:,.0f}",
    )
with c3:
    st.metric(
        "\u20b9 Revenue Recovered",
        f"\u20b9{rev_recovered:,.0f}",
        delta=f"\u20b9{rev_recovered:,.0f}" if rev_recovered > 0 else None,
        delta_color="normal",
    )
with c4:
    st.metric(
        "Recovery Rate",
        f"{recovery_rate:.1f}%",
        delta=f"{recovery_rate:.1f}%" if recovery_rate > 0 else None,
        delta_color="normal",
    )

st.divider()

# ── Row 2: Charts ───────────────────────────────────────────────────

chart_left, chart_right = st.columns(2)

# -- Failures by Reason --
with chart_left:
    st.subheader("\U0001f4ca Failures by Reason")
    breakdown_reason = metrics.get("breakdown_by_reason", {})
    if breakdown_reason:
        rows = []
        for reason, data in breakdown_reason.items():
            label = reason.replace("_", " ").title()
            rows.append({
                "Reason": label,
                "Count": int(data.get("count", 0)),
                "Amount (\u20b9)": float(data.get("amount", 0.0)),
            })
        df_reason = pd.DataFrame(rows).set_index("Reason")
        tab1, tab2 = st.tabs(["By Count", "By Amount"])
        with tab1:
            st.bar_chart(df_reason["Count"], color=RAZORPAY_BLUE, height=300)
        with tab2:
            st.bar_chart(df_reason["Amount (\u20b9)"], color="#FF9F43", height=300)
    else:
        st.info("\U0001f4ad No classification data yet. Click **Run Full Pipeline** in the sidebar to start.")

# -- Intervention Success Rate --
with chart_right:
    st.subheader("\U0001f3af Intervention Outcomes")
    breakdown_int = metrics.get("breakdown_by_intervention", {})
    if breakdown_int:
        rows = []
        for itype, data in breakdown_int.items():
            label = itype.replace("_", " ").title()
            cnt = int(data.get("count", 0))
            succ = int(data.get("succeeded", 0))
            rate = float(data.get("success_rate", 0.0))
            rows.append({
                "Type": label,
                "Total": cnt,
                "Succeeded": succ,
                "Success Rate (%)": rate,
            })
        df_int = pd.DataFrame(rows).set_index("Type")

        # Show a table with color indicators instead of a potentially broken bar chart
        st.dataframe(
            df_int[["Total", "Succeeded", "Success Rate (%)"]],
            use_container_width=True,
            column_config={
                "Total": st.column_config.NumberColumn("Total", format="%d"),
                "Succeeded": st.column_config.NumberColumn("Succeeded", format="%d"),
                "Success Rate (%)": st.column_config.ProgressColumn(
                    "Success Rate",
                    min_value=0,
                    max_value=100,
                    format="%.1f%%",
                ),
            },
        )

        # Bar chart of totals by type (always works since counts are integers)
        if df_int["Total"].sum() > 0:
            st.bar_chart(df_int[["Succeeded", "Total"]], color=[RAZORPAY_BLUE, "#cccccc"], height=250)
    else:
        st.info("\U0001f4ad No intervention data yet. Click **Run Full Pipeline** in the sidebar to start.")

st.divider()

# ── Row 3: Escalations ─────────────────────────────────────────────

st.subheader("\U0001f6a8 Escalations (Manual Review Required)")

escalations = api_get("/interventions/", params={"limit": 200})

if escalations:
    esc_list = escalations if isinstance(escalations, list) else escalations.get("items", [])
    # Filter to manual_escalation type
    esc_filtered = [
        e for e in esc_list
        if e.get("intervention_type") == "manual_escalation"
    ]
    if esc_filtered:
        df_esc = pd.DataFrame(esc_filtered)
        display_cols = [
            c for c in ["transaction_id", "policy_decision_reason", "status", "created_at"]
            if c in df_esc.columns
        ]
        st.markdown(f"**{len(esc_filtered)}** transaction(s) escalated to human review:")
        st.dataframe(
            df_esc[display_cols] if display_cols else df_esc,
            use_container_width=True,
            hide_index=True,
            column_config={
                "transaction_id": st.column_config.TextColumn("Transaction ID", width="medium"),
                "policy_decision_reason": st.column_config.TextColumn("Escalation Reason", width="large"),
                "status": st.column_config.TextColumn("Status"),
                "created_at": st.column_config.DatetimeColumn("Created", format="DD-MMM-YYYY HH:mm"),
            },
        )
    else:
        st.success("\u2705 No transactions currently require manual escalation.")
else:
    st.info("Could not fetch intervention data.")

st.divider()

# ── Row 4: Audit Log Viewer ─────────────────────────────────────────

st.subheader("\U0001f4dc Audit Trail")
st.caption("Full chronological decision trail \u2014 every classification, policy decision, retry attempt, and escalation is logged.")

# Transaction-level audit lookup
audit_tx_id = st.text_input(
    "Look up audit trail for a specific transaction:",
    placeholder="Paste a transaction_id here...",
    key="audit_lookup",
)

if audit_tx_id and audit_tx_id.strip():
    tx_detail = api_get(f"/transactions/{audit_tx_id.strip()}")
    if tx_detail:
        st.markdown(f"**Transaction** `{audit_tx_id.strip()}` \u2014 "
                     f"Status: **{tx_detail.get('status', 'N/A')}** \u2014 "
                     f"Amount: \u20b9{float(tx_detail.get('amount', 0)):,.2f}")

        # Show classification
        cls_data = tx_detail.get("classification")
        if cls_data:
            st.markdown(f"\U0001f9e0 **Classification**: {cls_data.get('predicted_reason', 'N/A')} "
                         f"(confidence: {float(cls_data.get('confidence_score', 0)):.0%})")

        # Show intervention
        int_data = tx_detail.get("intervention")
        if int_data:
            st.markdown(f"\u2696\ufe0f **Intervention**: {int_data.get('intervention_type', 'N/A')} "
                         f"\u2014 Status: {int_data.get('status', 'N/A')}")
            reason = int_data.get("policy_decision_reason")
            if reason:
                st.markdown(f"> {reason}")
    elif tx_detail is None and audit_tx_id.strip():
        st.warning(f"Transaction `{audit_tx_id.strip()}` not found.")

# Recent audit entries (always shown)
recent_txns = api_get("/transactions/", params={"limit": 10, "status": "failed"})
if recent_txns:
    items = recent_txns.get("items", recent_txns) if isinstance(recent_txns, dict) else recent_txns
    if isinstance(items, list) and items:
        audit_rows = []
        for txn in items[:10]:
            tid = txn.get("transaction_id", "")
            detail = api_get(f"/transactions/{tid}")
            if detail:
                cls_info = detail.get("classification") or {}
                int_info = detail.get("intervention") or {}
                audit_rows.append({
                    "Transaction": tid[:12] + "..." if len(str(tid)) > 12 else tid,
                    "Amount (\u20b9)": float(detail.get("amount", 0)),
                    "Classified As": cls_info.get("predicted_reason", "\u2014"),
                    "Confidence": f"{float(cls_info.get('confidence_score', 0)):.0%}" if cls_info.get("confidence_score") else "\u2014",
                    "Intervention": (int_info.get("intervention_type") or "\u2014").replace("_", " ").title(),
                    "Outcome": (int_info.get("status") or "pending").replace("_", " ").title(),
                })
        if audit_rows:
            st.markdown("**Recent failed transactions \u2014 decision trail:**")
            st.dataframe(
                pd.DataFrame(audit_rows),
                use_container_width=True,
                hide_index=True,
            )

st.divider()

# ── Row 5: Recent Transactions ──────────────────────────────────────

st.subheader("\U0001f4dd Recent Transactions")

recent = api_get("/transactions/", params={"limit": 20})

if recent:
    data = recent.get("items", recent) if isinstance(recent, dict) else recent
    if isinstance(data, list) and data:
        df_tx = pd.DataFrame(data)
        display_cols = [
            c for c in ["transaction_id", "amount", "status", "payment_method", "failure_reason", "created_at"]
            if c in df_tx.columns
        ]
        if display_cols:
            st.dataframe(
                df_tx[display_cols],
                use_container_width=True,
                hide_index=True,
                column_config={
                    "transaction_id": st.column_config.TextColumn("Transaction ID", width="medium"),
                    "amount": st.column_config.NumberColumn("Amount (\u20b9)", format="\u20b9%.2f"),
                    "status": st.column_config.TextColumn("Status"),
                    "payment_method": st.column_config.TextColumn("Method"),
                    "failure_reason": st.column_config.TextColumn("Failure Reason"),
                    "created_at": st.column_config.DatetimeColumn("Created", format="DD-MMM-YYYY HH:mm"),
                },
            )
        else:
            st.dataframe(df_tx, use_container_width=True, hide_index=True)
    else:
        st.info("No transactions found.")
else:
    st.info("Could not fetch recent transactions.")

# ── Footer ──────────────────────────────────────────────────────────

st.divider()
st.caption(
    "RazorRecover \u2022 Razorpay AI Buildathon 2026 \u2022 Track 3: AI Revenue Recovery \u2022 "
    f"Last metrics computed: {metrics.get('computed_at', 'N/A')}"
)
