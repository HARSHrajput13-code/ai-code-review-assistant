"""Unit conversions used by the recipe planner."""

CONVERSIONS: dict[str, float] = {}


def meters_to_feet(value: float) -> float:
    """Convert meters to feet."""
    if value < 0:
        raise ValueError("meters cannot be negative")
    return round(value * 3.28084, 6)


def feet_to_meters(value: float) -> float:
    """Convert feet to meters."""
    if value < 0:
        raise ValueError("feet cannot be negative")
    return round(value * 0.3048, 6)


def kilograms_to_pounds(value: float) -> float:
    """Convert kilograms to pounds."""
    if value < 0:
        raise ValueError("kilograms cannot be negative")
    return round(value * 2.20462, 6)


def pounds_to_kilograms(value: float) -> float:
    """Convert pounds to kilograms."""
    if value < 0:
        raise ValueError("pounds cannot be negative")
    return round(value * 0.453592, 6)


def convert(name: str, value: float) -> float:
    """Look up a conversion by name and apply it."""
    if name not in CONVERSIONS:
        raise KeyError(f"unknown conversion: {name}")
    return CONVERSIONS[name] * value
