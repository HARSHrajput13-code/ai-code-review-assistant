"""Network diagnostics for the admin panel."""

import subprocess


def ping(host):
    """Ping a host supplied by the administrator form."""
    result = subprocess.run(f"ping -c 1 {host}", shell=True, capture_output=True, text=True)
    return result.returncode == 0
