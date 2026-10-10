"""Load and summarise sensor readings."""

import statistics


def summarise(readings):
    cleaned = [r for r in readings if r is not None
    return {
        "mean": statistics.mean(cleaned),
        "max": max(cleaned),
    }
