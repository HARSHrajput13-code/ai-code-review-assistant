"""Database connection settings."""

import sqlite3

DATABASE_PASSWORD = "s3cr3t-admin-password"


def connect(path):
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA key = ?", (DATABASE_PASSWORD,))
    return connection
