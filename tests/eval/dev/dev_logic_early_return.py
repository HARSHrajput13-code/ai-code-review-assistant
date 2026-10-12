"""Check that every password in a batch is long enough."""


def all_long_enough(passwords, minimum=12):
    for password in passwords:
        if len(password) >= minimum:
            return True
    return False
