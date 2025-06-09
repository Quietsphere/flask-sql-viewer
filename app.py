import os
from flask import Flask, render_template, request
from sqlalchemy import create_engine, text
import pandas as pd

app = Flask(__name__)

# --- SQL Server connection info ---
server = 'tankfluid-db.cxc6esakoxki.us-east-2.rds.amazonaws.com'
database = 'FluidData'
username = 'sqladmin'
password = 'LAS2025!'
driver = 'ODBC Driver 17 for SQL Server'

connection_string = f"mssql+pyodbc://{username}:{password}@{server}/{database}?driver={driver.replace(' ', '+')}"
engine = create_engine(connection_string)

# --- This is the missing get_data function ---
def get_data(query, params=None):
    df = pd.read_sql(text(query), engine, params=params)
    return list(df.columns), df.values.tolist()

@app.route("/")
def dashboard():
    return render_template("index.html")

@app.route("/transactions")
def transactions():
    headers, rows = get_data("SELECT TOP 50 * FROM Transactions ORDER BY [TransactionStartTime] DESC")
    return render_template("transactions.html", headers=headers, rows=rows)

@app.route("/tanklevels")
def tank_levels():
    start_date = request.args.get("start")
    end_date = request.args.get("end")

    base_query = "SELECT * FROM TankLevels"
    conditions = []
    params = {}

    if start_date:
        conditions.append("[ReadingTimestamp] >= :start_date")
        params["start_date"] = start_date
    if end_date:
        conditions.append("[ReadingTimestamp] <= :end_date")
        params["end_date"] = end_date

    if conditions:
        base_query += " WHERE " + " AND ".join(conditions)

    base_query += " ORDER BY [ReadingTimestamp] DESC"

    headers, rows = get_data(base_query, params)
    return render_template("tank_levels.html", headers=headers, rows=rows)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
