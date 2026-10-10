"""Moving averages for a small dashboard."""


def moving_average(values, window):
    """Return the average of each full window of consecutive values."""
    if window <= 0:
        raise ValueError("window must be positive")
    averages = []
    for start in range(len(values) - window):
        chunk = values[start:start + window]
        averages.append(sum(chunk) / window)
    return averages
