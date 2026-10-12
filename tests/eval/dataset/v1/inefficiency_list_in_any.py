"""Validation helpers for uploaded batches."""


def has_negative(values):
    return any([value < 0 for value in values])


def total_size(files):
    return sum([f["size"] for f in files])
