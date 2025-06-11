import os
from flask import Flask, render_template, request
from sqlalchemy import create_engine, text
import pandas as pd
from datetime import datetime, date
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
    start, end = get_date_range(request.args)
    product = request.args.get("product")

    query = "SELECT * FROM Transactions WHERE [Timestamp] BETWEEN :start AND :end"
    params = {"start": start, "end": end}

    if product:
        query += " AND Product LIKE :product"
        params["product"] = f"%{product}%"

    query += " ORDER BY [Timestamp] DESC"

    headers, rows = get_data(query, params)
    return render_template("transactions.html", headers=headers, rows=rows, start=start, end=end)


@app.route("/tank-levels")
def tank_levels():
    start, end = get_date_range(request.args)
    tank = request.args.get("tank")

    query = "SELECT * FROM TankLevels WHERE [ReadingTimestamp] BETWEEN :start AND :end"
    params = {"start": start, "end": end}

    if tank:
        query += " AND TankName LIKE :tank"
        params["tank"] = f"%{tank}%"

    query += " ORDER BY [ReadingTimestamp] DESC"

    headers, rows = get_data(query, params)
    return render_template("tank_levels.html", headers=headers, rows=rows, start=start, end=end)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
