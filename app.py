from flask import Flask, render_template
from sqlalchemy import create_engine
import pandas as pd

app = Flask(__name__)

# SQL Server connection info (update with your actual details)
server = 'tankfluid-db.cxc6esakoxki.us-east-2.rds.amazonaws.com'
database = 'FluidData'
username = 'sqladmin'
password = 'LAS2025!'
driver = 'ODBC Driver 17 for SQL Server'

# SQLAlchemy connection string
connection_string = f"mssql+pyodbc://{username}:{password}@{server}/{database}?driver={driver.replace(' ', '+')}"
engine = create_engine(connection_string)

# Generic data fetch function
def get_data(query):
    df = pd.read_sql(query, engine)
    headers = df.columns.tolist()
    rows = df.values.tolist()
    return headers, rows

@app.route("/")
def index():
    headers, rows = get_data("SELECT * FROM Transactions ORDER BY TransactionStartTime DESC")
    return render_template("index.html", headers=headers, rows=rows, title="Transaction Table")

@app.route("/tanklevels")
def tank_levels():
    headers, rows = get_data("SELECT * FROM TankLevels ORDER BY ReadingTimestamp DESC")
    return render_template("index.html", headers=headers, rows=rows, title="Tank Level Readings")

if __name__ == "__main__":
    app.run(debug=True)
