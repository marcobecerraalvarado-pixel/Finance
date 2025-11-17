# app.py
import streamlit as st
from google.cloud import bigquery
import pandas as pd
import altair as alt
from datetime import datetime

# ---------------------------------------------------------
# 1. AUTHENTICATION
# ---------------------------------------------------------
VALID_USERNAME = "maba"
VALID_PASSWORD = "16.06"

def login_screen():
    st.title("🔐 Login to Budget App")

    username = st.text_input("Username")
    password = st.text_input("Password", type="password")

    if st.button("Login"):
        if username == VALID_USERNAME and password == VALID_PASSWORD:
            st.session_state["logged_in"] = True
            st.rerun()
        else:
            st.error("Incorrect username or password.")

def require_login():
    if "logged_in" not in st.session_state:
        st.session_state["logged_in"] = False

    if not st.session_state["logged_in"]:
        login_screen()
        st.stop()

# ---------------------------------------------------------
# 2. BIGQUERY CLIENT
# ---------------------------------------------------------
@st.cache_resource
def get_bq():
    return bigquery.Client()

bq = get_bq()

# ---------------------------------------------------------
# QUERY HELPERS
# ---------------------------------------------------------
def load_calc(start_date, end_date):
    query = f"""
        SELECT *
        FROM `your_project.your_dataset.Calc`
        WHERE Date >= '{start_date}' AND Date <= '{end_date}'
        ORDER BY Date
    """
    return bq.query(query).to_dataframe()

def load_planning():
    query = """
        SELECT *
        FROM `your_project.your_dataset.Planning_Model_Table`
        ORDER BY MainCategory, Category
    """
    return bq.query(query).to_dataframe()

def load_transactions(start_date, end_date):
    query = f"""
        SELECT *
        FROM `your_project.your_dataset.Transactions`
        WHERE Date >= '{start_date}' AND Date <= '{end_date}'
        ORDER BY Date DESC
    """
    return bq.query(query).to_dataframe()

def update_plan_value(main, category, year, month, new_plan):
    query = f"""
        UPDATE `your_project.your_dataset.Planning_Model_Table`
        SET Plan = {new_plan}
        WHERE MainCategory = '{main}'
          AND Category = '{category}'
          AND Year = {year}
          AND Month = '{month}'
    """
    bq.query(query).result()

# ---------------------------------------------------------
# START APP (AUTH)
# ---------------------------------------------------------
require_login()

st.set_page_config(page_title="Budget Dashboard", layout="wide")

st.sidebar.title("📊 Navigation")
page = st.sidebar.radio("Go to", ["Overview", "Budget Planner", "Logout"])

if page == "Logout":
    st.session_state["logged_in"] = False
    st.rerun()

# ---------------------------------------------------------
# PAGE 1: OVERVIEW
# ---------------------------------------------------------

if page == "Overview":
    st.title("📊 Overview Dashboard")

    # Date filter
    date_range = st.date_input("Select date range", [datetime(2025,11,1), datetime(2025,11,30)])
    start_date, end_date = date_range
    
    calc_df = load_calc(start_date, end_date)
    plan_df = load_planning()
    trans_df = load_transactions(start_date, end_date)

    # Scorecards
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("📅 This Month")
        monthly_plan = plan_df["Plan"].sum()
        monthly_real = plan_df["Real"].sum()
        st.metric("Month Total", f"{monthly_real}", f"{monthly_real - monthly_plan}")

    with col2:
        st.subheader("📆 Year To Date")
        ytd_plan = plan_df["Plan"].sum()
        ytd_real = plan_df["Real"].sum()
        st.metric("YTD Total", f"{ytd_real}", f"{ytd_real - ytd_plan}")

    # Chart
    st.subheader("📈 Financial Evolution")

    chart = alt.Chart(calc_df).mark_line().encode(
        x="Date:T",
        y=alt.Y("Estimated_value_acum:Q", title="Amount"),
        color=alt.value("#4A90E2")
    ) + \
    alt.Chart(calc_df).mark_line().encode(
        x="Date:T",
        y="Real_value_acum:Q",
        color=alt.value("#00C2FF")
    ) + \
    alt.Chart(calc_df).mark_line().encode(
        x="Date:T",
        y="Incomes_acum:Q",
        color=alt.value("#33CC66")
    ) + \
    alt.Chart(calc_df).mark_bar(opacity=0.3).encode(
        x="Date:T",
        y="Real_value:Q"
    )

    st.altair_chart(chart, use_container_width=True)

    # Tables
    left, right = st.columns([1.2, 1])
    with left:
        st.subheader("📂 Planned vs Real by Category")
        st.dataframe(plan_df)

    with right:
        st.subheader("🧾 Transactions")
        st.dataframe(trans_df)

# ---------------------------------------------------------
# PAGE 2: BUDGET PLANNER
# ---------------------------------------------------------

if page == "Budget Planner":
    st.title("💰 Budget Planner")

    plan_df = load_planning()
    
    months = plan_df["Month"].unique()
    selected_month = st.selectbox("Select Month", months)

    df = plan_df[plan_df["Month"] == selected_month]

    main_categories = df["MainCategory"].unique()

    for main in main_categories:
        block = df[df["MainCategory"] == main]

        with st.expander(f"📦 {main}", expanded=True):
            for _, row in block.iterrows():
                c1, c2, c3 = st.columns([2,1,1])
                with c1:
                    st.write(row["Category"])
                with c2:
                    new_plan = st.number_input(
                        "Plan",
                        value=float(row["Plan"]),
                        key=f"{main}_{row['Category']}"
                    )
                with c3:
                    delta = row["Real"] - row["Plan"]
                    st.write(f"Δ {delta}")

                if new_plan != row["Plan"]:
                    update_plan_value(main, row["Category"], row["Year"], row["Month"], new_plan)
                    st.success("Updated!")
                    st.rerun()

