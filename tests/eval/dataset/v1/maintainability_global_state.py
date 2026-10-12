"""Request counter for the metrics endpoint."""

request_count = 0


def record_request():
    global request_count
    request_count += 1
    return request_count
