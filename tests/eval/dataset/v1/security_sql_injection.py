"""User lookup for the support tool."""

import sqlite3


def find_user(connection: sqlite3.Connection, username: str):
    cursor = connection.cursor()
    cursor.execute(f"SELECT id, email FROM users WHERE name = '{username}'")
    return cursor.fetchone()
