import os
from flask import Flask, render_template, request
from sqlalchemy import create_engine, text
import pandas as pd
from datetime import datetime, date, timedelta
from dateutil.relativedelta import relativedelta


app = Flask(__name__)

# --- SQL Server connection info ---
server = 'tankfluid-db.cxc6esakoxki.us-east-2.rds.amazonaws.com'
database = 'FluidData'
username = 'sqladmin'
password = 'LAS2025!'
driver = 'ODBC Driver 17 for SQL Server'

connection_string = f"mssql+pyodbc://{username}:{password}@{server}/{database}?driver={driver.replace(' ', '+')}"
engine = create_engine(connection_string)

# This function gets the current month to default the loads and then allows for ad-hoc date ranges
def get_date_range(request_args):
    today = date.today()
    start_date = request_args.get("start_date")
    end_date = request_args.get("end_date")
    offset = request_args.get("month_offset", 0, type=int)

    if not start_date and not end_date:
        # Default to current month ± offset
        first_day = (today.replace(day=1) + relativedelta(months=offset))
        last_day = (first_day + relativedelta(months=1)) - relativedelta(days=1)
        return first_day, last_day

    # If user picks dates, use them
    if start_date:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
    else:
        start = today.replace(day=1)

    if end_date:
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
    else:
        end = today

    return start, end

# --- This is the missing get_data function ---
def get_data(query, params=None):
    df = pd.read_sql(text(query), engine, params=params)
    return list(df.columns), df.values.tolist()

@app.route("/")
def dashboard():
    return render_template("index.html")


@app.route("/transactions")
def transactions():
    # Get the month_offset from query parameters, default to 0
    month_offset = int(request.args.get("month_offset", 0))
    product = request.args.get("product")

    # Calculate the start and end datetime for the selected month based on offset
    today = datetime.today()
    first_of_month = datetime(today.year, today.month, 1)
    selected_start = first_of_month + relativedelta(months=month_offset)
    selected_end = selected_start + relativedelta(months=1) - timedelta(seconds=1)

    # Format to string for SQL query
    start = selected_start.strftime("%Y-%m-%d %H:%M:%S")
    end = selected_end.strftime("%Y-%m-%d %H:%M:%S")

    # Build query and params
    query = "SELECT * FROM Transactions WHERE [TransactionEndTime] BETWEEN :start AND :end"
    params = {"start": start, "end": end}

    if product:
        query += " AND Product LIKE :product"
        params["product"] = f"%{product}%"

    query += " ORDER BY [TransactionEndTime] DESC"

    headers, rows = get_data(query, params)

    # Create a readable date range string for display
    date_range_str = f"{selected_start.strftime('%b %d, %Y')} - {selected_end.strftime('%b %d, %Y')}"

    return render_template(
        "transactions.html",
        headers=headers,
        rows=rows,
        month_offset=month_offset,
        date_range=date_range_str,
        product=product
    )


@app.route("/tanklevels")
def tanklevels():
    # Get month_offset from query string, default to 0
    month_offset = int(request.args.get('month_offset', 0))

    # Calculate start and end of month based on offset
    today = datetime.today()
    first_of_month = datetime(today.year, today.month, 1)
    selected_start = first_of_month + relativedelta(months=month_offset)
    selected_end = selected_start + relativedelta(months=1) - timedelta(seconds=1)

    # Use selected_start and selected_end as your date range
    start, end = selected_start.strftime("%Y-%m-%d %H:%M:%S"), selected_end.strftime("%Y-%m-%d %H:%M:%S")

    tank = request.args.get("tank")

    query = "SELECT * FROM TankLevels WHERE [ReadingTimestamp] BETWEEN :start AND :end"
    params = {"start": start, "end": end}

    if tank:
        query += " AND TankName LIKE :tank"
        params["tank"] = f"%{tank}%"

    query += " ORDER BY [ReadingTimestamp] DESC"

    headers, rows = get_data(query, params)
    # Pass month_offset and formatted date range to the template
    date_range_str = f"{selected_start.strftime('%b %d, %Y')} - {selected_end.strftime('%b %d, %Y')}"

    return render_template("tanklevels.html", headers=headers, rows=rows, month_offset=month_offset, date_range=date_range_str)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
