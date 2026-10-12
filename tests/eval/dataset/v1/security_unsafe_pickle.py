"""Restore a saved session sent by the client."""

import base64
import pickle


def restore_session(cookie_value):
    """Decode the session cookie and return the session dictionary."""
    raw = base64.b64decode(cookie_value)
    return pickle.loads(raw)
