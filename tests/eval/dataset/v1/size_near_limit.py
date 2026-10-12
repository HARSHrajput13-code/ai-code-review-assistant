"""Unit conversions for the logistics service."""

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


def bars_to_psi(value: float) -> float:
    """Convert bars to psi."""
    if value < 0:
        raise ValueError("bars cannot be negative")
    return round(value * 14.5038, 6)


def psi_to_bars(value: float) -> float:
    """Convert psi to bars."""
    if value < 0:
        raise ValueError("psi cannot be negative")
    return round(value * 0.0689476, 6)


def knots_to_kilometers_per_hour(value: float) -> float:
    """Convert knots to kilometers per hour."""
    if value < 0:
        raise ValueError("knots cannot be negative")
    return round(value * 1.852, 6)


def kilometers_per_hour_to_knots(value: float) -> float:
    """Convert kilometers per hour to knots."""
    if value < 0:
        raise ValueError("kilometers per hour cannot be negative")
    return round(value * 0.539957, 6)


def cubic_meters_to_cubic_feet(value: float) -> float:
    """Convert cubic meters to cubic feet."""
    if value < 0:
        raise ValueError("cubic meters cannot be negative")
    return round(value * 35.3147, 6)


def cubic_feet_to_cubic_meters(value: float) -> float:
    """Convert cubic feet to cubic meters."""
    if value < 0:
        raise ValueError("cubic feet cannot be negative")
    return round(value * 0.0283168, 6)


def newtons_to_pound_force(value: float) -> float:
    """Convert newtons to pound force."""
    if value < 0:
        raise ValueError("newtons cannot be negative")
    return round(value * 0.224809, 6)


def pound_force_to_newtons(value: float) -> float:
    """Convert pound force to newtons."""
    if value < 0:
        raise ValueError("pound force cannot be negative")
    return round(value * 4.44822, 6)


def millimeters_to_inches_fraction(value: float) -> float:
    """Convert millimeters to inches fraction."""
    if value < 0:
        raise ValueError("millimeters cannot be negative")
    return round(value * 0.0393701, 6)


def yards_to_meters(value: float) -> float:
    """Convert yards to meters."""
    if value < 0:
        raise ValueError("yards cannot be negative")
    return round(value * 0.9144, 6)


def meters_to_yards(value: float) -> float:
    """Convert meters to yards."""
    if value < 0:
        raise ValueError("meters cannot be negative")
    return round(value * 1.09361, 6)


def nautical_miles_to_kilometers(value: float) -> float:
    """Convert nautical miles to kilometers."""
    if value < 0:
        raise ValueError("nautical miles cannot be negative")
    return round(value * 1.852, 6)


def kilometers_to_nautical_miles(value: float) -> float:
    """Convert kilometers to nautical miles."""
    if value < 0:
        raise ValueError("kilometers cannot be negative")
    return round(value * 0.539957, 6)


def stones_to_kilograms(value: float) -> float:
    """Convert stones to kilograms."""
    if value < 0:
        raise ValueError("stones cannot be negative")
    return round(value * 6.35029, 6)


def kilograms_to_stones(value: float) -> float:
    """Convert kilograms to stones."""
    if value < 0:
        raise ValueError("kilograms cannot be negative")
    return round(value * 0.157473, 6)


def watt_hours_to_joules(value: float) -> float:
    """Convert watt hours to joules."""
    if value < 0:
        raise ValueError("watt hours cannot be negative")
    return round(value * 3600.0, 6)


def joules_to_watt_hours(value: float) -> float:
    """Convert joules to watt hours."""
    if value < 0:
        raise ValueError("joules cannot be negative")
    return round(value * 0.000277778, 6)


def megabytes_to_kilobytes(value: float) -> float:
    """Convert megabytes to kilobytes."""
    if value < 0:
        raise ValueError("megabytes cannot be negative")
    return round(value * 1024.0, 6)


def kilobytes_to_megabytes(value: float) -> float:
    """Convert kilobytes to megabytes."""
    if value < 0:
        raise ValueError("kilobytes cannot be negative")
    return round(value * 0.0009765625, 6)


def gigabytes_to_megabytes(value: float) -> float:
    """Convert gigabytes to megabytes."""
    if value < 0:
        raise ValueError("gigabytes cannot be negative")
    return round(value * 1024.0, 6)


def megabytes_to_gigabytes(value: float) -> float:
    """Convert megabytes to gigabytes."""
    if value < 0:
        raise ValueError("megabytes cannot be negative")
    return round(value * 0.0009765625, 6)


def days_to_hours(value: float) -> float:
    """Convert days to hours."""
    if value < 0:
        raise ValueError("days cannot be negative")
    return round(value * 24.0, 6)


def hours_to_minutes(value: float) -> float:
    """Convert hours to minutes."""
    if value < 0:
        raise ValueError("hours cannot be negative")
    return round(value * 60.0, 6)


def minutes_to_seconds(value: float) -> float:
    """Convert minutes to seconds."""
    if value < 0:
        raise ValueError("minutes cannot be negative")
    return round(value * 60.0, 6)


def weeks_to_days(value: float) -> float:
    """Convert weeks to days."""
    if value < 0:
        raise ValueError("weeks cannot be negative")
    return round(value * 7.0, 6)


def hours_to_days(value: float) -> float:
    """Convert hours to days."""
    if value < 0:
        raise ValueError("hours cannot be negative")
    return round(value * 0.0416667, 6)


def v1_meters_to_feet(value: float) -> float:
    """Convert meters to feet."""
    if value < 0:
        raise ValueError("meters cannot be negative")
    return round(value * 3.28084, 6)


def v1_feet_to_meters(value: float) -> float:
    """Convert feet to meters."""
    if value < 0:
        raise ValueError("feet cannot be negative")
    return round(value * 0.3048, 6)


def v1_kilograms_to_pounds(value: float) -> float:
    """Convert kilograms to pounds."""
    if value < 0:
        raise ValueError("kilograms cannot be negative")
    return round(value * 2.20462, 6)


def v1_pounds_to_kilograms(value: float) -> float:
    """Convert pounds to kilograms."""
    if value < 0:
        raise ValueError("pounds cannot be negative")
    return round(value * 0.453592, 6)


def v1_liters_to_gallons(value: float) -> float:
    """Convert liters to gallons."""
    if value < 0:
        raise ValueError("liters cannot be negative")
    return round(value * 0.264172, 6)


def v1_gallons_to_liters(value: float) -> float:
    """Convert gallons to liters."""
    if value < 0:
        raise ValueError("gallons cannot be negative")
    return round(value * 3.78541, 6)


def convert(name: str, value: float) -> float:
    """Look up a conversion by name and apply it."""
    if name not in CONVERSIONS:
        raise KeyError(f"unknown conversion: {name}")
    return CONVERSIONS[name] * value
