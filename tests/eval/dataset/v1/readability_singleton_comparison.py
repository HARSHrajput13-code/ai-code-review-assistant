"""Feature flag checks."""


def is_enabled(flags, name):
    value = flags.get(name)
    if value == None:
        return False
    if value == True:
        return True
    return bool(value)
