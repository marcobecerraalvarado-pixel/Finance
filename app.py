
# app.py
import streamlit as st
import pandas as pd
import altair as alt
from datetime import date, datetime, timedelta
import calendar
import time
import json
from typing import List, Dict

# --------------------------
# PAGE / APP CONFIG
# --------------------------
st.set_page_config(page_title="Budget Dashboard", layout="wide", initial_sidebar_state="collapsed")

# --------------------------
# BIGQUERY IMPORT (optional)
# --------------------------
try:
    from google.cloud import bigquery
    from google.oauth2 import service_account
    BIGQUERY_AVAILABLE = True
except Exception:
    BIGQUERY_AVAILABLE = False

# --------------------------
# APP CREDENTIALS (LOGIN)
# --------------------------
DEFAULT_USER = "example"
DEFAULT_PASS = "example"

try:
    VALID_USER = st.secrets["app_credentials"]["VALID_USER"]
    VALID_PASS = st.secrets.get("app_credentials", {}).get("VALID_PASS", DEFAULT_PASS)
except Exception:
    VALID_USER = DEFAULT_USER
    VALID_PASS = DEFAULT_PASS
    st.session_state.setdefault("login_note_shown", False)
    if not st.session_state["login_note_shown"]:
        st.info("Using built-in credentials. Username: maba Password: 16.06 (you can set st.secrets['app_credentials'])")
        st.session_state["login_note_shown"] = True

# --------------------------
# UTIL: safe rerun compatibility
# --------------------------
def safe_rerun():
    """A robust rerun helper that avoids deprecated experimental_rerun usage."""
    try:
        # st.query_params() is a quick no-op to flush session
        st.query_params()
    except Exception:
        pass
    st.experimental_rerun() if hasattr(st, "experimental_rerun") else st.rerun()

# --------------------------
# MOCK DATA GENERATORS
# --------------------------
def generate_mock_planning_data():
    current_year = date.today().year
    months = ['January','February','March','April','May','June','July','August','September','October','November','December']
    rows = []
    main_map = {
        'Cards & Payments': ['CC American Express','CC Master','CC Visa'],
        'Finance & Incomes': ['Incomes','Other Incomes'],
        'Food': ['Butcher','Food Delivery','Green Store','Market','Supermarket','Work Food'],
        'Health & Wellness': ['Books','Health','Medication','Personal'],
        'Home & Utilities': ['Electricity','Furniture','Gardener','Gas','Internet','Maintenance','Rent','Water'],
        'Leisure': ['Dinner','Outing','Subscriptions','Vacations'],
        'Mobility': ['Mobile','Public Transportation','Taxi']
    }
    for y in range(current_year-2, current_year+2):
        for m_idx, m in enumerate(months, start=1):
            for main, cats in main_map.items():
                for c in cats:
                    if 'Incomes' in main or 'Incomes' in c:
                        plan_base = 1000 + (m_idx*10) + (abs(hash((y,m,c)))%300)
                    else:
                        plan_base = -(100 + (m_idx*5) + (abs(hash((y,m,c)))%150))
                    plan = round(plan_base, 2)
                    if abs(hash((c,y,m)))%10 == 0:
                        real = 0.0
                    else:
                        real = round(plan * (0.9 + (abs(hash((c,y,m)))%21)/100), 2)
                    rows.append({
                        "Year": int(y),
                        "Month": m,
                        "Month_Number": int(m_idx),
                        "MainCategory": main,
                        "Category": c,
                        "Plan": float(plan),
                        "Real": float(real),
                        "Relative": None
                    })
    df = pd.DataFrame(rows)
    return df

def generate_mock_calc_data():
    today = date.today()
    start = today.replace(day=1) - timedelta(days=60)
    dates = pd.date_range(start=start, end=today, freq='D')
    incomes_start = 50000
    real_start = 45000
    estimated_start = 51000
    incomes_acum = [incomes_start + i * 200 for i in range(len(dates))]
    real_value_acum = [real_start + i * 150 for i in range(len(dates))]
    estimated_value_acum = [estimated_start + i * 180 for i in range(len(dates))]
    df = pd.DataFrame({
        'Calc_date': dates.date,
        'Incomes_acum': incomes_acum,
        'Real_value_acum': real_value_acum,
        'Estimated_value_acum': estimated_value_acum,
        'Real_value': [50 + (i%5)*10 + ((i*3)%100) for i in range(len(dates))]
    })
    return df

def generate_mock_transactions_data():
    today = date.today()
    rows = []
    descs = ['Coffee','Rent','Electric','Salary','Netflix','Gas','Gift','Transfer','Delivery','Market','Supermarket','Butcher']
    cats = ['Food','Home & Utilities','Home & Utilities','Finance & Incomes','Leisure','Mobility','Leisure','Finance & Incomes','Food','Food','Food','Food']
    main_map_for_cat = {
        'Food': 'Food',
        'Home & Utilities': 'Home & Utilities',
        'Finance & Incomes': 'Finance & Incomes',
        'Leisure': 'Leisure',
        'Mobility': 'Mobility'
    }
    for i in range(180):
        idx = i % len(descs)
        d = today - timedelta(days=i)
        id_val = i + 1
        desc = descs[idx]
        cat = cats[idx]
        maincat = main_map_for_cat.get(cat, cat)
        income_val = 5000.0 if 'Salary' in desc else 0.0
        expense_val = 20.0 + (i%7)*5.0 + (i//10)*10.0 if income_val == 0 else 0.0
        payment = 'Card' if i%3==0 else 'Cash'
        card = 'Visa' if payment=='Card' else ''
        bank = 'Bank A' if i%5==0 else 'Bank B'
        currency = 'USD'
        rows.append({
            'ID': int(id_val),
            'Date': d.date(),
            'Currency': currency,
            'Description': desc,
            'Payment_Method': payment,
            'Card': card,
            'Bank': bank,
            'Category': cat,
            'MainCategory': maincat,
            'Expense': float(expense_val),
            'Income': float(income_val)
        })
    return pd.DataFrame(rows)

# --------------------------
# APP STATE: cached clients
# --------------------------
@st.cache_resource
def get_bigquery_client():
    if not BIGQUERY_AVAILABLE:
        return None
    if "google_cloud" not in st.secrets:
        return None
    try:
        creds = service_account.Credentials.from_service_account_info(st.secrets["google_cloud"])
        client = bigquery.Client(credentials=creds, project=creds.project_id)
        return client
    except Exception as e:
        st.error("BigQuery client init error (using MOCK): " + str(e))
        return None

bq_client = get_bigquery_client()

def get_dataset_name():
    return st.secrets.get("google_cloud", {}).get("dataset", "your_dataset_here")

# --------------------------
# LOADERS (BQ or MOCK)
# --------------------------
@st.cache_data(ttl=600)
def load_planning_table():
    table = "Planning_Model_Table"
    if bq_client:
        dataset = get_dataset_name()
        q = f"SELECT * FROM `{bq_client.project}.{dataset}.{table}` ORDER BY Year, Month_Number, MainCategory, Category"
        try:
            df = bq_client.query(q).to_dataframe()
            df['Year'] = df['Year'].astype(int)
            return df
        except Exception as e:
            st.warning(f"BigQuery read failed, using MOCK. ({e})")
    if "mock_planning_data" not in st.session_state:
        st.session_state["mock_planning_data"] = generate_mock_planning_data()
    return st.session_state["mock_planning_data"].copy()

@st.cache_data(ttl=600)
def load_calc_table(start_date, end_date):
    table = "Calc"
    if bq_client:
        dataset = get_dataset_name()
        q = f"""
        SELECT Calc_date, Incomes_acum, Estimated_value_acum, Real_value_acum, Real_value
        FROM `{bq_client.project}.{dataset}.{table}`
        WHERE Calc_date BETWEEN '{start_date}' AND '{end_date}'
        ORDER BY Calc_date
        """
        try:
            df = bq_client.query(q).to_dataframe()
            df['Calc_date'] = pd.to_datetime(df['Calc_date']).dt.date
            return df
        except Exception as e:
            st.warning("BigQuery read Calc failed, using MOCK. " + str(e))
    df = generate_mock_calc_data()
    df = df[(df['Calc_date'] >= start_date) & (df['Calc_date'] <= end_date)].copy()
    return df

@st.cache_data(ttl=600)
def load_transactions_table(start_date, end_date):
    table = "Transactions"
    if bq_client:
        dataset = get_dataset_name()
        q = f"""
        SELECT ID, Date, Currency, Description, Payment_Method, Card, Bank, Category, MainCategory, Expense, Income
        FROM `{bq_client.project}.{dataset}.{table}`
        WHERE Date BETWEEN '{start_date}' AND '{end_date}'
        ORDER BY Date DESC
        """
        try:
            df = bq_client.query(q).to_dataframe()
            df['Date'] = pd.to_datetime(df['Date']).dt.date
            return df
        except Exception as e:
            st.warning("BigQuery read Transactions failed, using MOCK. " + str(e))
    if "mock_transactions_data" not in st.session_state:
        st.session_state["mock_transactions_data"] = generate_mock_transactions_data()
    df = st.session_state["mock_transactions_data"]
    df_filtered = df[(df['Date'] >= start_date) & (df['Date'] <= end_date)].copy()
    return df_filtered

# --------------------------
# UPDATE HELPERS
# --------------------------
def update_bq_plan(main, category, year, month, new_plan):
    dataset = get_dataset_name()
    table = "Planning_Model_Table"
    if bq_client:
        q = f"""
        UPDATE `{bq_client.project}.{dataset}.{table}`
        SET Plan = @new_plan
        WHERE MainCategory = @main AND Category = @category AND Year = @year AND Month = @month
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("new_plan", "INT64", new_plan),
                bigquery.ScalarQueryParameter("main", "STRING", main),
                bigquery.ScalarQueryParameter("category", "STRING", category),
                bigquery.ScalarQueryParameter("year", "INT64", int(year)),
                bigquery.ScalarQueryParameter("month", "STRING", month),
            ]
        )
        try:
            query_job = bq_client.query(q, job_config=job_config)
            query_job.result()
            # clear cache
            load_planning_table.clear()
            return True, None
        except Exception as e:
            return False, str(e)
    else:
        df = st.session_state.get("mock_planning_data")
        if df is None:
            return False, "No mock planning data in session."
        mask = (df['MainCategory'] == main) & (df['Category'] == category) & (df['Year'] == int(year)) & (df['Month'] == month)
        if mask.any():
            df.loc[mask, 'Plan'] = new_plan
            st.session_state["mock_planning_data"] = df
            load_planning_table.clear()
            return True, None
        else:
            return False, "Row not found in mock planning dataset."

def update_bq_transaction_row(row: Dict):
    """
    row: dictionary containing ID and the updated fields to write back to BQ.
    Required: ID present.
    """
    dataset = get_dataset_name()
    table = "Transactions"
    if 'ID' not in row:
        return False, "Missing ID"
    if bq_client:
        q = f"""
        UPDATE `{bq_client.project}.{dataset}.{table}`
        SET Date = @date,
            Currency = @currency,
            Description = @description,
            Payment_Method = @payment_method,
            Card = @card,
            Bank = @bank,
            Category = @category,
            MainCategory = @maincategory,
            Expense = @expense,
            Income = @income
        WHERE ID = @id
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("date", "TIMESTAMP", row.get("Date")),
                bigquery.ScalarQueryParameter("currency", "STRING", row.get("Currency")),
                bigquery.ScalarQueryParameter("description", "STRING", row.get("Description")),
                bigquery.ScalarQueryParameter("payment_method", "STRING", row.get("Payment_Method")),
                bigquery.ScalarQueryParameter("card", "STRING", row.get("Card")),
                bigquery.ScalarQueryParameter("bank", "STRING", row.get("Bank")),
                bigquery.ScalarQueryParameter("category", "STRING", row.get("Category")),
                bigquery.ScalarQueryParameter("maincategory", "STRING", row.get("MainCategory")),
                bigquery.ScalarQueryParameter("expense", "FLOAT64", float(row.get("Expense") or 0.0)),
                bigquery.ScalarQueryParameter("income", "FLOAT64", float(row.get("Income") or 0.0)),
                bigquery.ScalarQueryParameter("id", "INT64", int(row.get("ID"))),
            ]
        )
        try:
            job = bq_client.query(q, job_config=job_config)
            job.result()
            # Clear transactions cache
            load_transactions_table.clear()
            return True, None
        except Exception as e:
            return False, str(e)
    else:
        df = st.session_state.get("mock_transactions_data")
        if df is None:
            return False, "No mock transactions in session."
        mask = df['ID'] == int(row['ID'])
        if not mask.any():
            return False, "ID not found in mock transactions."
        # Update fields in mock df
        for col in ['Date','Currency','Description','Payment_Method','Card','Bank','Category','MainCategory','Expense','Income']:
            if col in row:
                df.loc[mask, col] = row[col]
        st.session_state["mock_transactions_data"] = df
        load_transactions_table.clear()
        return True, None

# --------------------------
# AUTH FLOW
# --------------------------
def login_screen():
    st.title("🔐 Login")
    st.markdown("Please login to access the Budget Dashboard")
    col1, col2, col3 = st.columns([1,2,1])
    with col2:
        user = st.text_input("Username", value="", key="login_user")
        pwd = st.text_input("Password", value="", type="password", key="login_pwd")
        if st.button("Login"):
            if user == VALID_USER and pwd == VALID_PASS:
                st.session_state["logged_in"] = True
                st.success("Login successful")
                safe_rerun()
            else:
                st.error("Invalid credentials")

def require_login():
    if "logged_in" not in st.session_state:
        st.session_state["logged_in"] = False
    if not st.session_state["logged_in"]:
        login_screen()
        st.stop()

# --------------------------
# HELPER: Date Selection (reusable compact Year-Month)
# --------------------------
def render_date_selector(key_prefix, plan_df_full):
    today = date.today()
    months_order = ['January','February','March','April','May','June','July','August','September','October','November','December']
    if plan_df_full.empty:
        st.warning("No planning data available to set range.")
        return None, None
    years = sorted(plan_df_full['Year'].unique(), reverse=True)
    current_month_name = today.strftime('%B')
    if f"{key_prefix}_selected_year" not in st.session_state:
        st.session_state[f"{key_prefix}_selected_year"] = today.year
    if f"{key_prefix}_selected_month" not in st.session_state:
        st.session_state[f"{key_prefix}_selected_month"] = current_month_name
    # compact two-row control for Year-Month
    cols = st.columns([1,1])
    with cols[0]:
        try:
            initial_year_index = years.index(st.session_state[f"{key_prefix}_selected_year"])
        except ValueError:
            initial_year_index = 0
        selected_year = st.selectbox("Year", years, index=initial_year_index, key=f"{key_prefix}_year_select", on_change=lambda: st.session_state.update({f"{key_prefix}_selected_year": st.session_state[f"{key_prefix}_year_select"]}))
        st.session_state[f"{key_prefix}_selected_year"] = selected_year
    with cols[1]:
        months_available = plan_df_full[plan_df_full['Year'] == selected_year]['Month'].unique().tolist()
        months_sorted = [m for m in months_order if m in months_available]
        if not months_sorted:
            st.warning("No months for selected year.")
            return None, None
        try:
            initial_month_index = months_sorted.index(st.session_state[f"{key_prefix}_selected_month"])
        except ValueError:
            initial_month_index = months_sorted.index(current_month_name) if current_month_name in months_sorted else len(months_sorted)-1
        selected_month_name = st.selectbox("Month", months_sorted, index=initial_month_index, key=f"{key_prefix}_month_select", on_change=lambda: st.session_state.update({f"{key_prefix}_selected_month": st.session_state[f"{key_prefix}_month_select"]}))
        st.session_state[f"{key_prefix}_selected_month"] = selected_month_name
    # calculate start/end dates for that month
    selected_month_num = months_order.index(st.session_state[f"{key_prefix}_selected_month"]) + 1
    _, days_in_month = calendar.monthrange(st.session_state[f"{key_prefix}_selected_year"], selected_month_num)
    start_date = date(st.session_state[f"{key_prefix}_selected_year"], selected_month_num, 1)
    end_date = date(st.session_state[f"{key_prefix}_selected_year"], selected_month_num, days_in_month)
    return start_date, end_date

# HELPER for get_month_year (used in detail view)
def get_month_year(y, m_num, delta):
    months_order = ['January','February','March','April','May','June','July','August','September','October','November','December']
    total_months = y * 12 + m_num + delta
    new_y = (total_months - 1) // 12
    new_m_num = (total_months - 1) % 12 + 1
    return new_y, months_order[new_m_num-1], new_m_num

# Calculate moving averages (using Sort_Key)
def calculate_category_averages(df, selected_year, selected_month, category):
    months_order = ['January','February','March','April','May','June','July','August','September','October','November','December']
    months_map = {m: i for i, m in enumerate(months_order, 1)}
    selected_m_num = months_map[selected_month]
    def get_sort_key(y, m_num):
        return y * 100 + m_num
    end_key = get_sort_key(selected_year, selected_m_num)
    category_df = df[df['Category'] == category].copy()
    def get_average(N_months):
        start_delta = -(N_months - 1)
        start_y, _, start_m_num = get_month_year(selected_year, selected_m_num, start_delta)
        start_key = get_sort_key(start_y, start_m_num)
        df_window = category_df[(category_df['Sort_Key'] >= start_key) & (category_df['Sort_Key'] <= end_key)]
        return df_window['Real'].mean() if not df_window.empty else 0.0
    avg_3 = get_average(3)
    avg_6 = get_average(6)
    avg_12 = get_average(12)
    return abs(avg_3 or 0.0), abs(avg_6 or 0.0), abs(avg_12 or 0.0)

# --------------------------
# APP UI & FLOW
# --------------------------
require_login()

# session initialized containers
if "pending_changes" not in st.session_state:
    st.session_state["pending_changes"] = []
if "detail_view" not in st.session_state:
    st.session_state["detail_view"] = None

# Sidebar navigation
with st.sidebar:
    st.markdown("## Budget App")
    page = st.radio("Navigation", ["Overview", "Budget Planner", "Transactions", "Logout"])

if page == "Logout":
    st.session_state["logged_in"] = False
    safe_rerun()

# -------------------------
# OVERVIEW PAGE
# -------------------------
if page == "Overview":
    st.markdown("# Overview")
    plan_df_full = load_planning_table()
    start_date, end_date = render_date_selector("overview", plan_df_full)
    if not start_date or not end_date:
        st.stop()
    calc_df = load_calc_table(start_date, end_date)
    trans_df = load_transactions_table(start_date, end_date)
    st.markdown("### Scorecards")
    filter_month_name = start_date.strftime('%B')
    filter_year = start_date.year
    plan_df = plan_df_full[(plan_df_full['Month'] == filter_month_name) & (plan_df_full['Year'] == filter_year)]
    ytd_df = plan_df_full[(plan_df_full['Year'] == filter_year) & (plan_df_full['Month_Number'] <= start_date.month)]
    def compute_net(df):
        inc = df[df['MainCategory'].str.contains('Income', case=False, na=False)]['Real'].sum()
        exp = df[~df['MainCategory'].str.contains('Income', case=False, na=False)]['Real'].sum()
        plan_inc = df[df['MainCategory'].str.contains('Income', case=False, na=False)]['Plan'].sum()
        plan_exp = df[~df['MainCategory'].str.contains('Income', case=False, na=False)]['Plan'].sum()
        return inc + exp, plan_inc + plan_exp, inc, plan_inc, exp, plan_exp
    monthly_net_real, monthly_net_plan, inc_real, inc_plan, exp_real, exp_plan = compute_net(plan_df)
    ytd_net_real, ytd_net_plan, _, _, _, _ = compute_net(ytd_df)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Monthly Net (Real)", f"${monthly_net_real:,.2f}", f"Plan ${monthly_net_plan:,.2f}")
    c2.metric("Monthly Income (Real)", f"${inc_real:,.2f}", f"Plan ${inc_plan:,.2f}")
    c3.metric("Monthly Expense (Real)", f"${abs(exp_real):,.2f}", f"Plan ${abs(exp_plan):,.2f}")
    c4.metric("YTD Net (Real)", f"${ytd_net_real:,.2f}", f"Plan ${ytd_net_plan:,.2f}")
    st.markdown("---")
    # Combined chart
    st.markdown("### Daily Expense Tracker")
    if not calc_df.empty and all(col in calc_df.columns for col in ['Calc_date','Incomes_acum','Estimated_value_acum','Real_value_acum','Real_value']):
        melt_columns = {
            'Estimated_value_acum': 'Plan',
            'Incomes_acum': 'Incomes',
            'Real_value_acum': 'Actual_accum'
        }
        dfm = calc_df.melt(id_vars=['Calc_date'], value_vars=['Estimated_value_acum','Incomes_acum','Real_value_acum'], var_name='Series', value_name='Amount')
        dfm['Series'] = dfm['Series'].replace(melt_columns)
        # rename Actual_accum to Actual in legend mapping, but use a different name for clarity
        dfm['Series'] = dfm['Series'].replace({'Actual_accum': 'Actual (Accumulated)'})
        lines = alt.Chart(dfm).mark_line(point=True).encode(
            x=alt.X('Calc_date:T', title='Date'),
            y=alt.Y('Amount:Q', title='Accumulated Amount ($)', axis=alt.Axis(titleColor='#4c78a8')),
            color=alt.Color('Series:N', legend=alt.Legend(title='Series'), scale=alt.Scale()),
            tooltip=[alt.Tooltip('Calc_date:T', title='Date'), alt.Tooltip('Series:N'), alt.Tooltip('Amount:Q', format='$,.2f')]
        )
        # Bars: daily Real_value displayed on right axis and named "Actual"
        bars = alt.Chart(calc_df).mark_bar(opacity=0.5).encode(
            x=alt.X('Calc_date:T'),
            y=alt.Y('Real_value:Q', title='Daily Actual ($)', axis=alt.Axis(orient='right', titleColor='#f59e0b')),
            tooltip=[alt.Tooltip('Calc_date:T', title='Date'), alt.Tooltip('Real_value:Q', title='Daily Actual', format='$,.2f')],
            color=alt.value('#f59e0b')
        )
        # Combine with independent Y
        chart = (lines + bars).resolve_scale(y='independent').properties(title="Daily Expense Tracker").interactive()
        st.altair_chart(chart, use_container_width=True)
    else:
        st.info("Calc table missing required columns; showing mock chart.")
        calc_mock = generate_mock_calc_data()
        dfm = calc_mock.melt(id_vars=['Calc_date'], value_vars=['Estimated_value_acum','Incomes_acum','Real_value_acum'], var_name='Series', value_name='Amount')
        dfm['Series'] = dfm['Series'].replace({'Estimated_value_acum':'Plan','Incomes_acum':'Incomes','Real_value_acum':'Actual (Accumulated)'})
        lines = alt.Chart(dfm).mark_line(point=True).encode(x='Calc_date:T', y='Amount:Q', color='Series:N', tooltip=['Calc_date:T','Series:N','Amount:Q']).properties(height=300).interactive()
        st.altair_chart(lines, use_container_width=True)
    st.markdown("---")
    left, right = st.columns([1.2, 1])
    with left:
        st.subheader("Plan vs Real by category")
        df_display = plan_df[plan_df['MainCategory'] != 'Finance & Incomes'].copy()
        if df_display.empty:
            st.info("No planning rows for selected month")
        else:
            df_display['Delta'] = abs(df_display['Real']) - abs(df_display['Plan'])
            df_display = df_display[['MainCategory','Category','Plan','Real','Delta']].rename(columns={'MainCategory':'Main','Category':'Category','Plan':'Plan ($)','Real':'Real ($)','Delta':'Delta ($)'})
            st.dataframe(df_display, use_container_width=True, hide_index=True)
    with right:
        st.subheader("Transactions")
        if trans_df.empty:
            st.info("No transactions in range")
        else:
            st.dataframe(trans_df[['ID','Date','MainCategory','Category','Description','Expense','Income']].rename(columns={'Expense':'Expense ($)','Income':'Income ($)'}), use_container_width=True, hide_index=True)

# -------------------------
# TRANSACTIONS PAGE
# -------------------------
elif page == "Transactions":
    st.markdown("# Transactions Summary")
    plan_df_full = load_planning_table()
    start_date, end_date = render_date_selector("transactions", plan_df_full)
    if not start_date or not end_date:
        st.stop()
    st.markdown(f"### Data for: {start_date.strftime('%B %Y')}")
    trans_df = load_transactions_table(start_date, end_date)
    if trans_df.empty:
        st.info("No transactions found for the selected period.")
        st.stop()
    # Ensure MainCategory present (if not, derive from Category using planning table)
    if 'MainCategory' not in trans_df.columns or trans_df['MainCategory'].isnull().all():
        # try to map category->maincategory from planning
        mapping = plan_df_full.set_index('Category')['MainCategory'].to_dict()
        trans_df['MainCategory'] = trans_df['Category'].map(mapping).fillna(trans_df.get('MainCategory','Unknown'))
    # Aggregate by MainCategory (sum of Expense)
    expense_data = trans_df.groupby('MainCategory', dropna=False)['Expense'].sum().reset_index().rename(columns={'Expense':'TotalRealExpense'})
    total_expense = expense_data['TotalRealExpense'].sum()
    expense_data['Percentage'] = expense_data['TotalRealExpense'] / (total_expense if total_expense != 0 else 1)
    st.markdown("---")
    st.subheader("Real Expenses by Main Category")
    # show a grid of scorecards (wrap columns)
    max_cols = 4
    cols = st.columns(max_cols)
    for i, row in expense_data.iterrows():
        col_idx = i % max_cols
        pct_label = f"{row['Percentage']*100:.1f}%"
        cols[col_idx].metric(label=row['MainCategory'], value=f"${row['TotalRealExpense']:,.2f}", delta=pct_label)
    st.markdown("---")
    st.subheader("Breakdown by Main Category (expand to see categories)")
    main_groups = trans_df.groupby('MainCategory')
    for main, group in main_groups:
        # Main group sum is correct if Expense is numeric
        main_total = group['Expense'].sum() 
        
        # Use the calculated total with f-string formatting for the expander header
        with st.expander(f"{main} — Total ${main_total:,.2f}", expanded=False):
            
            # Calculate the breakdown sum (which should now be correct numbers)
            breakdown = (
                group.groupby('Category', dropna=True)['Expense']
                .sum()
                .reset_index()
                .rename(columns={'Expense':'TotalExpense'})
            )
            
            # FIX: Correct the format string passed to .style.format()
            st.table(
                breakdown.style.format({"TotalExpense": "${:,.2f}"}) # Use the correct format specifier string
            )
    st.markdown("---")
    st.subheader("Transactions Table (editable)")
    # Reorder columns for display/edit
    display_cols = ['ID','Date','MainCategory','Category','Description','Expense','Income','Payment_Method','Card','Bank','Currency']
    for col in display_cols:
        if col not in trans_df.columns:
            trans_df[col] = None
    trans_edit_df = trans_df[display_cols].copy().reset_index(drop=True)
    # Use st.data_editor if available (Streamlit >=1.24). Fallback to st.experimental_data_editor if necessary.
    try:
        edited = st.data_editor(trans_edit_df, num_rows="dynamic", use_container_width=True)
    except Exception:
        edited = st.experimental_data_editor(trans_edit_df, num_rows="dynamic", use_container_width=True)
    # Compare edited vs original to gather changes
    if 'transactions_pending' not in st.session_state:
        st.session_state['transactions_pending'] = {}
    # when user clicks confirm button, write changed rows
    col_confirm, col_clear = st.columns([1,1])
    with col_confirm:
        if st.button("Confirm edited transactions (apply to DB)"):
            # iterate rows and detect differences
            changes = []
            for i, row in edited.iterrows():
                orig_row = trans_edit_df.loc[i].to_dict()
                new_row = row.to_dict()
                # Normalize types and blank -> None
                # If difference, and ID present, prepare change dict
                if int(new_row.get('ID') or -1) != int(orig_row.get('ID') or -1):
                    # If ID changed (should not), skip and show error
                    st.error(f"Row {i}: ID should not be changed. Skipping row.")
                    continue
                diffs = {}
                for col in display_cols:
                    orig_val = orig_row.get(col)
                    new_val = new_row.get(col)
                    # normalize datetime/date
                    if col == 'Date' and pd.notna(new_val):
                        if isinstance(new_val, str):
                            try:
                                new_val_dt = pd.to_datetime(new_val).to_pydatetime()
                                # BigQuery expects TIMESTAMP; we'll pass string and let parametrization handle
                                new_val = new_val_dt
                            except Exception:
                                pass
                    # For floats/ints:
                    if col in ['Expense','Income'] and pd.isna(new_val):
                        new_val = 0.0
                    if pd.isna(orig_val) and pd.isna(new_val):
                        changed = False
                    else:
                        changed = orig_val != new_val
                    if changed:
                        diffs[col] = new_val
                if diffs:
                    # Prepare full row for update (use edited values but fill missing with orig)
                    update_row = orig_row.copy()
                    update_row.update(new_row)
                    # enforce business rule: if Expense changed >0 then Income = 0, and vice versa
                    try:
                        exp_v = float(update_row.get('Expense') or 0.0)
                        inc_v = float(update_row.get('Income') or 0.0)
                    except Exception:
                        exp_v = update_row.get('Expense') or 0.0
                        inc_v = update_row.get('Income') or 0.0
                    if exp_v and exp_v > 0:
                        update_row['Income'] = 0.0
                    elif inc_v and inc_v > 0:
                        update_row['Expense'] = 0.0
                    changes.append(update_row)
            if not changes:
                st.info("No changes detected.")
            else:
                successes = []
                failures = []
                for r in changes:
                    ok, err = update_bq_transaction_row(r)
                    if ok:
                        successes.append(r['ID'])
                    else:
                        failures.append({'ID': r.get('ID'), 'error': err})
                st.success(f"Applied {len(successes)} updates.")
                if failures:
                    st.error(f"{len(failures)} failed. Details: {json.dumps(failures, default=str)}")
                # reload transactions data after write
                load_transactions_table.clear()
                safe_rerun()
    with col_clear:
        if st.button("Discard local edits (reload)"):
            load_transactions_table.clear()
            safe_rerun()

# -------------------------
# BUDGET PLANNER PAGE
# -------------------------
elif page == "Budget Planner":
    st.markdown("# Budget Planner")
    plan_df_full = load_planning_table()
    months_order = ['January','February','March','April','May','June','July','August','September','October','November','December']
    months_map = {m: i for i, m in enumerate(months_order, 1)}
    if 'Month_Number' not in plan_df_full.columns:
        plan_df_full['Month_Number'] = plan_df_full['Month'].map(months_map)
    plan_df_full['Sort_Key'] = plan_df_full['Year'] * 100 + plan_df_full['Month_Number']
    plan_df_full = plan_df_full.sort_values('Sort_Key').reset_index(drop=True)
    years = sorted(plan_df_full['Year'].unique(), reverse=True)
    today = date.today()
    default_year_idx = years.index(today.year) if today.year in years else 0
    if 'planner_selected_year' not in st.session_state:
        st.session_state['planner_selected_year'] = years[default_year_idx]
    if 'planner_selected_month' not in st.session_state:
        st.session_state['planner_selected_month'] = today.strftime('%B')
    col_y, col_m = st.columns(2)
    with col_y:
        try:
            initial_year_index = years.index(st.session_state['planner_selected_year'])
        except ValueError:
            initial_year_index = default_year_idx
        selected_year = st.selectbox("Year", years, index=initial_year_index, key="planner_year_select", on_change=lambda: st.session_state.update({'planner_selected_year': st.session_state.planner_year_select}))
        st.session_state['planner_selected_year'] = selected_year
    months_available = plan_df_full[plan_df_full['Year'] == selected_year]['Month'].unique().tolist()
    months_sorted = [m for m in months_order if m in months_available]
    if not months_sorted:
        st.warning("No months for selected year")
        st.stop()
    try:
        initial_month_index = months_sorted.index(st.session_state['planner_selected_month'])
    except ValueError:
        initial_month_index = months_sorted.index(today.strftime('%B')) if today.strftime('%B') in months_sorted else len(months_sorted)-1
    with col_m:
        selected_month = st.selectbox("Month", months_sorted, index=initial_month_index, key="planner_month_select", on_change=lambda: st.session_state.update({'planner_selected_month': st.session_state.planner_month_select}))
        st.session_state['planner_selected_month'] = selected_month
    # Detail view
    if st.session_state.get("detail_view"):
        detail = st.session_state["detail_view"]
        st.markdown(f"## Detail View: {detail['MainCategory']} - {detail['Category']}")
        st.caption("Showing 6 months historical (Real) and 5 months future (Plan) based on your selection.")
        selected_m_num = months_map[detail['Month']]
        selected_y = detail['Year']
        start_y, start_m_name, start_m_num = get_month_year(selected_y, selected_m_num, -6)
        end_y, end_m_name, end_m_num = get_month_year(selected_y, selected_m_num, 5)
        start_key = start_y * 100 + start_m_num
        end_key = end_y * 100 + end_m_num
        detail_df = plan_df_full[(plan_df_full['Category'] == detail['Category']) & (plan_df_full['Sort_Key'] >= start_key) & (plan_df_full['Sort_Key'] <= end_key)].copy()
        if not detail_df.empty:
            detail_df['Date_Label'] = detail_df.apply(lambda row: f"{row['Month'][:3]} {str(row['Year'])[-2:]}", axis=1)
            df_melt = detail_df.melt(id_vars=['Date_Label','Sort_Key'], value_vars=['Real','Plan'], var_name='Type', value_name='Amount')
            current_sort_key = selected_y * 100 + selected_m_num
            current_month_label = detail_df[detail_df['Sort_Key'] == current_sort_key]['Date_Label'].iloc[0] if current_sort_key in detail_df['Sort_Key'].values else None
            domain_labels = df_melt.sort_values('Sort_Key')['Date_Label'].unique().tolist()
            base = alt.Chart(df_melt).encode(
                x=alt.X('Date_Label:O', sort=domain_labels, title="Month"),
                y=alt.Y('Amount:Q', title="Amount ($)"),
                tooltip=['Date_Label:O', 'Type:N', alt.Tooltip('Amount:Q', format='$,.2f')]
            ).properties(title=f"12-Month Trend: {detail['Category']} (Real vs. Plan)")
            lines = base.mark_line(point=True).encode(color=alt.Color('Type:N', title="Value Type")).interactive()
            if current_month_label:
                rule_df = pd.DataFrame([{'Date_Label': current_month_label}])
                rule = alt.Chart(rule_df).mark_rule(color='red', strokeDash=[5,5], size=2).encode(x=alt.X('Date_Label:O', sort=domain_labels))
                st.altair_chart((lines + rule).properties(height=400), use_container_width=True)
            else:
                st.altair_chart(lines.properties(height=400), use_container_width=True)
        else:
            st.warning("No data found for this category in the 12-month window.")
        if st.button("Close Detail View", key="close_detail"):
            st.session_state["detail_view"] = None
            safe_rerun()
    else:
        df = plan_df_full[(plan_df_full['Year'] == selected_year) & (plan_df_full['Month'] == selected_month)].sort_values(['MainCategory','Category'])
        main_categories = df['MainCategory'].unique().tolist()
        st.markdown("### Edit plans (values queued until you press Confirm)")
        col_confirm, col_discard, _ = st.columns([1,1,8])
        with col_confirm:
            if st.button("Confirm changes (apply to DB)"):
                if not st.session_state["pending_changes"]:
                    st.info("No pending changes.")
                else:
                    successes = []
                    failures = []
                    for change in st.session_state["pending_changes"]:
                        ok, err = update_bq_plan(change['MainCategory'], change['Category'], change['Year'], change['Month'], change['NewPlan'])
                        if ok:
                            successes.append(change)
                        else:
                            failures.append((change, err))
                    if successes:
                        load_planning_table.clear()
                    st.success(f"{len(successes)} applied, {len(failures)} failed.")
                    if failures:
                        st.error("Failures: " + json.dumps([{'row':f[0], 'error':f[1]} for f in failures], default=str))
                    st.session_state["pending_changes"] = []
                    safe_rerun()
        with col_discard:
            if st.button("Discard all changes"):
                st.session_state["pending_changes"] = []
                st.info("All pending changes discarded.")
                safe_rerun()
        for main in main_categories:
            st.markdown(f"#### {main}")
            block = df[df['MainCategory'] == main]
            cols_header = st.columns([1.5, 0.8, 0.8, 0.8, 0.8, 0.3])
            cols_header[0].markdown("**Category**")
            cols_header[1].markdown("**L3 Avg ($)**")
            cols_header[2].markdown("**L6 Avg ($)**")
            cols_header[3].markdown("**L12 Avg ($)**")
            cols_header[4].markdown("**Plan (Edit)**")
            cols_header[5].markdown("**Trend**")
            for i, row in block.iterrows():
                category = row['Category']
                current_plan = row['Plan']
                current_real = row['Real']
                avg_3, avg_6, avg_12 = calculate_category_averages(plan_df_full, selected_year, selected_month, category)
                pending = next((item for item in st.session_state["pending_changes"] if item['Category'] == category and item['Year'] == selected_year and item['Month'] == selected_month), None)
                display_plan = pending['NewPlan'] if pending else current_plan
                with st.container():
                    c1, c2, c3, c4, c5, c6 = st.columns([1.5, 0.8, 0.8, 0.8, 0.8, 0.3])
                    c1.text(category)
                    c2.text(f"{avg_3:,.2f}")
                    c3.text(f"{avg_6:,.2f}")
                    c4.text(f"{avg_12:,.2f}")
                    key = f"input_{selected_year}_{selected_month}_{main}_{category}"
                    new_plan = c5.number_input("Plan", value=display_plan, key=key, label_visibility="collapsed")
                    with c6:
                        if st.button("📈", key=f"detail_btn_{selected_year}_{selected_month}_{category}", help="View 12-month trend"):
                            st.session_state['detail_view'] = {
                                "MainCategory": main,
                                "Category": category,
                                "Year": selected_year,
                                "Month": selected_month
                            }
                            safe_rerun()
                    if new_plan != current_plan:
                        new_change = {"MainCategory": main, "Category": category, "Year": selected_year, "Month": selected_month, "OldPlan": current_plan, "NewPlan": new_plan}
                        pending_index = next((i for i, item in enumerate(st.session_state["pending_changes"]) if item['Category'] == category and item['Year'] == selected_year and item['Month'] == selected_month), -1)
                        if new_plan == current_plan:
                            if pending_index != -1:
                                st.session_state["pending_changes"].pop(pending_index)
                        elif pending_index != -1:
                            st.session_state["pending_changes"][pending_index]['NewPlan'] = new_plan
                        else:
                            st.session_state["pending_changes"].append(new_change)
            st.markdown("---")

# End of file
