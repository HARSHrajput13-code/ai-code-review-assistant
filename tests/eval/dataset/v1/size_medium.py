"""Unit conversions for the engineering toolkit."""

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


def liters_to_gallons(value: float) -> float:
    """Convert liters to gallons."""
    if value < 0:
        raise ValueError("liters cannot be negative")
    return round(value * 0.264172, 6)


def gallons_to_liters(value: float) -> float:
    """Convert gallons to liters."""
    if value < 0:
        raise ValueError("gallons cannot be negative")
    return round(value * 3.78541, 6)


def kilometers_to_miles(value: float) -> float:
    """Convert kilometers to miles."""
    if value < 0:
        raise ValueError("kilometers cannot be negative")
    return round(value * 0.621371, 6)


def miles_to_kilometers(value: float) -> float:
    """Convert miles to kilometers."""
    if value < 0:
        raise ValueError("miles cannot be negative")
    return round(value * 1.609344, 6)


def centimeters_to_inches(value: float) -> float:
    """Convert centimeters to inches."""
    if value < 0:
        raise ValueError("centimeters cannot be negative")
    return round(value * 0.393701, 6)


def inches_to_centimeters(value: float) -> float:
    """Convert inches to centimeters."""
    if value < 0:
        raise ValueError("inches cannot be negative")
    return round(value * 2.54, 6)


def grams_to_ounces(value: float) -> float:
    """Convert grams to ounces."""
    if value < 0:
        raise ValueError("grams cannot be negative")
    return round(value * 0.035274, 6)


def ounces_to_grams(value: float) -> float:
    """Convert ounces to grams."""
    if value < 0:
        raise ValueError("ounces cannot be negative")
    return round(value * 28.3495, 6)


def square_meters_to_square_feet(value: float) -> float:
    """Convert square meters to square feet."""
    if value < 0:
        raise ValueError("square meters cannot be negative")
    return round(value * 10.7639, 6)


def square_feet_to_square_meters(value: float) -> float:
    """Convert square feet to square meters."""
    if value < 0:
        raise ValueError("square feet cannot be negative")
    return round(value * 0.092903, 6)


def hectares_to_acres(value: float) -> float:
    """Convert hectares to acres."""
    if value < 0:
        raise ValueError("hectares cannot be negative")
    return round(value * 2.47105, 6)


def acres_to_hectares(value: float) -> float:
    """Convert acres to hectares."""
    if value < 0:
        raise ValueError("acres cannot be negative")
    return round(value * 0.404686, 6)


def milliliters_to_fluid_ounces(value: float) -> float:
    """Convert milliliters to fluid ounces."""
    if value < 0:
        raise ValueError("milliliters cannot be negative")
    return round(value * 0.033814, 6)


def fluid_ounces_to_milliliters(value: float) -> float:
    """Convert fluid ounces to milliliters."""
    if value < 0:
        raise ValueError("fluid ounces cannot be negative")
    return round(value * 29.5735, 6)


def tonnes_to_short_tons(value: float) -> float:
    """Convert tonnes to short tons."""
    if value < 0:
        raise ValueError("tonnes cannot be negative")
    return round(value * 1.10231, 6)


def short_tons_to_tonnes(value: float) -> float:
    """Convert short tons to tonnes."""
    if value < 0:
        raise ValueError("short tons cannot be negative")
    return round(value * 0.907185, 6)


def joules_to_calories(value: float) -> float:
    """Convert joules to calories."""
    if value < 0:
        raise ValueError("joules cannot be negative")
    return round(value * 0.239006, 6)


def calories_to_joules(value: float) -> float:
    """Convert calories to joules."""
    if value < 0:
        raise ValueError("calories cannot be negative")
    return round(value * 4.184, 6)


def kilowatts_to_horsepower(value: float) -> float:
    """Convert kilowatts to horsepower."""
    if value < 0:
        raise ValueError("kilowatts cannot be negative")
    return round(value * 1.34102, 6)


def horsepower_to_kilowatts(value: float) -> float:
    """Convert horsepower to kilowatts."""
    if value < 0:
        raise ValueError("horsepower cannot be negative")
    return round(value * 0.745700, 6)


def convert(name: str, value: float) -> float:
    """Look up a conversion by name and apply it."""
    if name not in CONVERSIONS:
        raise KeyError(f"unknown conversion: {name}")
    return CONVERSIONS[name] * value
