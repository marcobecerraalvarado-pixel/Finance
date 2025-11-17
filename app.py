# app.py
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from google.cloud import bigquery
from google.oauth2 import service_account
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
import json
from st_aggrid import AgGrid, GridOptionsBuilder

# -------------------------
# CONFIG / CONSTANTS
# -------------------------
st.set_page_config(layout="wide", page_title="Budget Planner", initial_sidebar_state="collapsed")

# BigQuery dataset/table names will be taken from secrets, with defaults
BQ_PROJECT = st.secrets.get("BQ_PROJECT", None)
BQ_DATASET = st.secrets.get("BQ_DATASET", None)
PLANNING_TABLE = st.secrets.get("PLANNING_TABLE", "planning_model_table")
CALC_TABLE = st.secrets.get("CALC_TABLE", "Calc")
TRANSACTIONS_TABLE = st.secrets.get("TRANSACTIONS_TABLE", "Transactions")

# -------------------------
# BigQuery client bootstrap
# -------------------------
@st.cache_resource(ttl=3600)
def get_bq_client():
    if "gcp_service_account" not in st.secrets:
        st.error("Add your service account JSON to Streamlit secrets under 'gcp_service_account'.")
        st.stop()
    try:
        sa_info = json.loads(st.secrets["gcp_service_account"])
    except Exception as e:
        st.error("Invalid JSON in 'gcp_service_account' secret: " + str(e))
        st.stop()
    credentials = service_account.Credentials.from_service_account_info(sa_info)
    project = sa_info.get("project_id", BQ_PROJECT)
    client = bigquery.Client(credentials=credentials, project=project)
    return client

bq_client = get_bq_client()

# Helper to run query and return df
@st.cache_data(ttl=60)
def query_to_df(query: str, job_config=None) -> pd.DataFrame:
    job = bq_client.query(query, job_config=job_config)
    df = job.result().to_dataframe(create_bqstorage_client=False)
    return df

# Update Plan in BigQuery (uses Year, Month_Number, MainCategory, Category)
def update_plan_in_bq(year, month_number, main_cat, category, new_plan):
    table_full = f"`{bq_client.project}.{BQ_DATASET}.{PLANNING_TABLE}`"
    # UPDATE
    update_sql = f"""
    UPDATE {table_full}
    SET Plan = @new_plan
    WHERE Year = @year
      AND Month_Number = @month_number
      AND MainCategory = @main_cat
      AND Category = @category
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("new_plan", "FLOAT64", float(new_plan)),
            bigquery.ScalarQueryParameter("year", "INT64", int(year)),
            bigquery.ScalarQueryParameter("month_number", "INT64", int(month_number)),
            bigquery.ScalarQueryParameter("main_cat", "STRING", str(main_cat)),
            bigquery.ScalarQueryParameter("category", "STRING", str(category)),
        ]
    )
    bq_client.query(update_sql, job_config=job_config).result()

    # Check row existence
    check_sql = f"""
    SELECT COUNT(1) as cnt
    FROM {table_full}
    WHERE Year = @year
      AND Month_Number = @month_number
      AND MainCategory = @main_cat
      AND Category = @category
    """
    check_job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("year", "INT64", int(year)),
            bigquery.ScalarQueryParameter("month_number", "INT64", int(month_number)),
            bigquery.ScalarQueryParameter("main_cat", "STRING", str(main_cat)),
            bigquery.ScalarQueryParameter("category", "STRING", str(category)),
        ]
    )
    cnt = bq_client.query(check_sql, job_config=check_job_config).result().to_dataframe().iloc[0]["cnt"]
    if int(cnt) == 0:
        insert_sql = f"""
        INSERT {table_full} (Year, Month, Month_Number, MainCategory, Category, Plan, Real)
        VALUES (@year, @month_text, @month_number, @main_cat, @category, @new_plan, 0)
        """
        # For Month (text) we create a simple month label
        month_text = date(int(year), int(month_number), 1).strftime("%b %Y")
        ins_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("year","INT64",int(year)),
                bigquery.ScalarQueryParameter("month_text","STRING", month_text),
                bigquery.ScalarQueryParameter("month_number","INT64",int(month_number)),
                bigquery.ScalarQueryParameter("main_cat","STRING",str(main_cat)),
                bigquery.ScalarQueryParameter("category","STRING",str(category)),
                bigquery.ScalarQueryParameter("new_plan","FLOAT64",float(new_plan)),
            ]
        )
        bq_client.query(insert_sql, job_config=ins_config).result()
    return True

# -------------------------
# Data loaders
# -------------------------
def load_calc(start_date, end_date):
    table = f"`{bq_client.project}.{BQ_DATASET}.{CALC_TABLE}`"
    sql = f"""
    SELECT date, estimated_value_acum, real_value_acum, incomes_acum, real_value
    FROM {table}
    WHERE date BETWEEN @start_date AND @end_date
    ORDER BY date
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("start_date","DATE", start_date),
            bigquery.ScalarQueryParameter("end_date","DATE", end_date),
        ]
    )
    return query_to_df(sql, job_config=job_config)

def load_planning_for_month(year, month_number):
    table = f"`{bq_client.project}.{BQ_DATASET}.{PLANNING_TABLE}`"
    sql = f"""
    SELECT Year, Month, Month_Number, MainCategory, Category, Plan, Real
    FROM {table}
    WHERE Year = @year AND Month_Number = @month_number
    ORDER BY MainCategory, Category
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("year","INT64",int(year)),
            bigquery.ScalarQueryParameter("month_number","INT64",int(month_number)),
        ]
    )
    return query_to_df(sql, job_config=job_config)

def load_planning_ytd(year):
    table = f"`{bq_client.project}.{BQ_DATASET}.{PLANNING_TABLE}`"
    sql = f"""
    SELECT Year, SUM(Plan) as Plan, SUM(Real) as Real
    FROM {table}
    WHERE Year = @year
    GROUP BY Year
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("year","INT64",int(year))]
    )
    return query_to_df(sql, job_config=job_config)

def load_transactions(start_date, end_date):
    table = f"`{bq_client.project}.{BQ_DATASET}.{TRANSACTIONS_TABLE}`"
    sql = f"""
    SELECT date, category, description, expense, income
    FROM {table}
    WHERE date BETWEEN @start_date AND @end_date
    ORDER BY date DESC
    LIMIT 1000
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("start_date","DATE", start_date),
                          bigquery.ScalarQueryParameter("end_date","DATE", end_date)]
    )
    return query_to_df(sql, job_config=job_config)

# -------------------------
# UI helpers
# -------------------------
def month_label(year, month_number):
    return f"{year}-{int(month_number):02d}"

# -------------------------
# APP UI
# -------------------------
st.title("📊 Budget & Planning App — Modern Cards (Option B)")

tabs = st.tabs(["Overview", "Budget Planner"])

# Date range picker - default current month
today = date.today()
first_of_month = date(today.year, today.month, 1)
last_of_month = (first_of_month + relativedelta(months=1)) - timedelta(days=1)
col_a, col_b = st.columns([1,1])
with col_a:
    sd = st.date_input("Start date", value=first_of_month, key="sd_updated")
with col_b:
    ed = st.date_input("End date", value=last_of_month, key="ed_updated")

# ---------- Overview ----------
with tabs[0]:
    left_col, right_col = st.columns([1,4], gap="large")

    with left_col:
        st.markdown("### Scorecards")
        # compute active Year/Month_Number from sd
        active_year = sd.year
        active_month_number = sd.month
        try:
            df_plan = load_planning_for_month(active_year, active_month_number)
        except Exception as e:
            st.error("Error loading planning table: " + str(e))
            st.stop()
        sum_plan = float(df_plan['Plan'].sum()) if not df_plan.empty else 0.0
        sum_real = float(df_plan['Real'].sum()) if not df_plan.empty else 0.0

        # YTD
        try:
            df_ytd = load_planning_ytd(active_year)
            ytd_plan = float(df_ytd['Plan'].iloc[0]) if not df_ytd.empty else 0.0
            ytd_real = float(df_ytd['Real'].iloc[0]) if not df_ytd.empty else 0.0
        except Exception:
            ytd_plan = 0.0; ytd_real = 0.0

        # This month card
        delta_month = sum_real - sum_plan
        delta_perc = (delta_month / sum_plan * 100) if sum_plan != 0 else None
        st.markdown(f"""
            <div style="background: linear-gradient(90deg,#7b61ff,#4fc3f7); padding:14px; border-radius:10px; color:white;">
                <h4 style="margin:0;padding:0;">This month</h4>
                <h2 style="margin:0;padding:0;">${sum_real:,.2f}</h2>
                <div style="font-size:13px; opacity:0.95;">Plan: ${sum_plan:,.2f}</div>
            </div>
        """, unsafe_allow_html=True)
        if delta_perc is None:
            st.info("No plan for this month")
        else:
            color = "#0f9d58" if delta_month >= 0 else "#d32f2f"
            st.markdown(f"<div style='padding:6px 0;'>Δ {delta_month:,.2f} — <span style='color:{color}'>{delta_perc:.1f}%</span></div>", unsafe_allow_html=True)

        st.write("")
        # YTD card
        delta_ytd = ytd_real - ytd_plan
        color = "#0f9d58" if delta_ytd >= 0 else "#d32f2f"
        st.markdown(f"""
            <div style="background: linear-gradient(90deg,#ff7ab6,#ffb86b); padding:14px; border-radius:10px; color:white;">
                <h4 style="margin:0;padding:0;">Year to date</h4>
                <h2 style="margin:0;padding:0;">${ytd_real:,.2f}</h2>
                <div style="font-size:13px; opacity:0.95;">Plan (YTD): ${ytd_plan:,.2f}</div>
            </div>
            <div style="padding-top:6px;">Δ <span style='color:{color}'>${delta_ytd:,.2f}</span></div>
        """, unsafe_allow_html=True)

    with right_col:
        st.markdown("### Combined chart (Calc)")
        try:
            df_calc = load_calc(sd, ed)
        except Exception as e:
            st.error("Error loading Calc table: " + str(e))
            st.stop()
        if df_calc.empty:
            st.info("No Calc data in range.")
        else:
            fig = go.Figure()
            fig.add_trace(go.Bar(x=df_calc['date'], y=df_calc['real_value'], name="Real_value", marker_color='rgba(120,120,120,0.6)'))
            fig.add_trace(go.Scatter(x=df_calc['date'], y=df_calc['estimated_value_acum'], mode='lines+markers', name='Estimated_value_acum'))
            fig.add_trace(go.Scatter(x=df_calc['date'], y=df_calc['real_value_acum'], mode='lines+markers', name='Real_value_acum'))
            fig.add_trace(go.Scatter(x=df_calc['date'], y=df_calc['incomes_acum'], mode='lines+markers', name='Incomes_acum'))
            fig.update_layout(legend=dict(orientation="h"), margin=dict(t=10,b=10,l=10,r=10), height=420)
            st.plotly_chart(fig, use_container_width=True)

    # Below: left planning by main/category, right transactions table
    left_panel, right_panel = st.columns([2,1], gap="large")
    with left_panel:
        st.markdown("### Planning by MainCategory / Category")
        if df_plan.empty:
            st.info("No planning data for selected month.")
        else:
            # rename columns to consistent lower-case for display
            df_display = df_plan[['MainCategory','Category','Plan','Real']].copy()
            gb = GridOptionsBuilder.from_dataframe(df_display)
            gb.configure_default_column(groupable=True, value=True, enableRowGroup=True)
            gb.configure_column("Plan", type=["numericColumn","numberColumnFilter","customNumericFormat"], precision=2)
            gb.configure_column("Real", type=["numericColumn","numberColumnFilter","customNumericFormat"], precision=2)
            gridOptions = gb.build()
            AgGrid(df_display, gridOptions=gridOptions, fit_columns_on_grid_load=True)

    with right_panel:
        st.markdown("### Transactions (Date, Category, Description, Expense, Income)")
        try:
            df_tx = load_transactions(sd, ed)
        except Exception as e:
            st.error("Error loading transactions: " + str(e))
            st.stop()
        if df_tx.empty:
            st.info("No transactions in range.")
        else:
            st.dataframe(df_tx.head(300).sort_values('date', ascending=False))

# ---------- Budget Planner ----------
with tabs[1]:
    st.markdown("## Budget Planner (Editable)")

    # query available Year & Month_Number combos
    table_full = f"`{bq_client.project}.{BQ_DATASET}.{PLANNING_TABLE}`"
    months_sql = f"""
    SELECT DISTINCT Year, Month_Number
    FROM {table_full}
    ORDER BY Year DESC, Month_Number DESC
    LIMIT 60
    """
    res = query_to_df(months_sql)
    if res.empty:
        sel_year = today.year
        sel_month = today.month
    else:
        res['ym'] = res['Year'].astype(str) + "-" + res['Month_Number'].astype(int).astype(str).str.zfill(2)
        options = res['ym'].tolist()
        selected_ym = st.selectbox("Select month (YYYY-MM)", options=options, index=0)
        sel_year, sel_month = map(int, selected_ym.split("-"))

    df_selected = load_planning_for_month(sel_year, sel_month)
    total_plan = df_selected['Plan'].sum() if not df_selected.empty else 0.0
    total_real = df_selected['Real'].sum() if not df_selected.empty else 0.0

    st.markdown(f"""
    <div style="display:flex; justify-content:space-between; align-items:center;">
      <div style="padding:14px;border-radius:10px;background:linear-gradient(90deg,#6a11cb,#2575fc);color:white;">
        <h4 style="margin:0">Month: {sel_year}-{sel_month:02d}</h4>
        <h2 style="margin:0">${total_plan:,.2f} plan  —  ${total_real:,.2f} real</h2>
      </div>
      <div style="padding:10px">
        <small>Auto-save: changes write to BigQuery</small>
      </div>
    </div>
    """, unsafe_allow_html=True)

    if df_selected.empty:
        st.info("No rows for this month — you can create them by editing inputs (they will be inserted).")
    else:
        grouped = df_selected.groupby("MainCategory", sort=False)
        for main_cat, group in grouped:
            with st.expander(f"{main_cat} — Plan ${group['Plan'].sum():,.2f} — Real ${group['Real'].sum():,.2f}", expanded=False):
                for idx, row in group.iterrows():
                    cat = row['Category']
                    plan_val = float(row['Plan']) if pd.notnull(row['Plan']) else 0.0
                    real_val = float(row['Real']) if pd.notnull(row['Real']) else 0.0
                    key = f"inp_{sel_year}_{sel_month}_{main_cat}_{cat}"
                    cols = st.columns([3,1,1])
                    cols[0].markdown(f"**{cat}**  \nReal: ${real_val:,.2f}")
                    new_plan = cols[2].number_input("", min_value=0.0, value=float(plan_val), key=key, step=1.0, format="%.2f")
                    # detect change via session_state sentinel
                    sess_key = key + "_synced"
                    previous = st.session_state.get(sess_key, plan_val)
                    if new_plan != previous:
                        try:
                            update_plan_in_bq(sel_year, sel_month, main_cat, cat, new_plan)
                            st.session_state[sess_key] = new_plan
                            st.success(f"Saved {cat}: ${new_plan:,.2f}", key=f"ok_{key}")
                        except Exception as e:
                            st.error("Save error: " + str(e))
                            st.session_state[sess_key] = previous

    st.markdown("---")
    st.markdown("### Recent months (last 12)")
    hist_sql = f"""
    SELECT Year, Month_Number, SUM(Plan) as Plan, SUM(Real) as Real
    FROM {table_full}
    GROUP BY Year, Month_Number
    ORDER BY Year DESC, Month_Number DESC
    LIMIT 12
    """
    hist_frame = query_to_df(hist_sql)
    if not hist_frame.empty:
        for i, r in hist_frame.iterrows():
            yr = int(r['Year']); mo = int(r['Month_Number'])
            plan = float(r['Plan']); real = float(r['Real'])
            diff = real - plan
            color = "#d1ffd6" if diff >= 0 else "#ffdede"
            arrow = "▲" if diff >= 0 else "▼"
            st.markdown(f"<div style='background:{color};padding:8px;border-radius:8px;margin-bottom:6px;'>"
                        f"<b>{yr}-{mo:02d}</b> — Plan ${plan:,.2f} — Real ${real:,.2f} — <b>{arrow} {diff:,.2f}</b></div>", unsafe_allow_html=True)
    else:
        st.info("No history yet.")
