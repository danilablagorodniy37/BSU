"""
weather_processor.py - the Data Processing program for CPUF001 Assessment S1.

What it does
------------
The program reads a CSV file of daily weather observations whose path is given
on the command line, derives a set of new values for every day, calculates
summary statistics for the whole period, and writes two output files:

  1. a CSV of the per-day derived values, and
  2. a plain-text report that summarises the processing activity.

Each input row supplies six data values (date, maximum temperature, minimum
temperature, relative humidity, rainfall and wind speed), which is comfortably
more than the three values required by the brief.

Calculations performed
----------------------
  * mean temperature and diurnal range for each day
  * dew point, using the Magnus-Tetens approximation
  * apparent ("feels like") temperature, combining temperature, humidity and
    wind speed
  * a centred rolling mean of temperature over a user-selectable window
  * whole-period statistics: means, extremes, totals and counted days
  * a month-by-month breakdown
  * a least-squares linear trend line fitted to the mean daily temperature

Usage
-----
    python weather_processor.py <input_csv> [options]

Options
-------
    -o, --output   CSV file for the per-day results   (default output/daily_metrics.csv)
    -r, --report   text file for the summary report   (default output/summary_report.txt)
    -w, --window   rolling-average window in days     (default 7)
    -q, --quiet    suppress the summary printed to the screen

Example
-------
    python weather_processor.py data/weather_cleaned.csv -o output/daily_metrics.csv

Exit codes
----------
    0 - processing finished successfully
    1 - the input file was missing, unreadable or contained no usable rows
"""

import argparse
import csv
import math
import os
import sys
from datetime import datetime

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

# Columns the program expects to find in the input file.
REQUIRED_COLUMNS = [
    "date",
    "temp_max_c",
    "temp_min_c",
    "humidity_pct",
    "rainfall_mm",
    "wind_kph",
]

# Columns written to the per-day output file, in order.
OUTPUT_COLUMNS = [
    "date",
    "month",
    "temp_mean_c",
    "temp_range_c",
    "dew_point_c",
    "feels_like_c",
    "rolling_mean_c",
    "is_rain_day",
    "is_frost_day",
]

# Constants for the Magnus-Tetens dew point approximation, valid for the
# temperature range normally seen in a temperate climate.
MAGNUS_A = 17.27
MAGNUS_B = 237.7

# A day is counted as a "rain day" once measured rainfall reaches this depth.
# 0.2 mm is the tipping-bucket resolution of a standard rain gauge, so smaller
# amounts are really just trace readings.
RAIN_DAY_THRESHOLD_MM = 0.2

# Month names used in the report, indexed by month number (1-12).
MONTH_NAMES = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


# --------------------------------------------------------------------------
# Reading the input file
# --------------------------------------------------------------------------

def read_observations(input_path):
    """Read the input CSV and return a list of observation dictionaries.

    Rows that cannot be converted are skipped rather than crashing the program,
    and the number skipped is returned so the report can mention it. This means
    the program still produces useful output if it is pointed at a raw file
    that has not been through clean_data.py first.

    Returns a (observations, skipped_count) pair.
    """
    observations = []
    skipped = 0

    with open(input_path, "r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)

        # Fail early and clearly if the file is not the expected format.
        if reader.fieldnames is None:
            raise ValueError("the file is empty")
        missing = [c for c in REQUIRED_COLUMNS if c not in reader.fieldnames]
        if missing:
            raise ValueError("missing required column(s): " + ", ".join(missing))

        for row in reader:
            try:
                observation = {
                    "date": datetime.strptime(row["date"].strip(), "%Y-%m-%d").date(),
                    "temp_max_c": float(row["temp_max_c"]),
                    "temp_min_c": float(row["temp_min_c"]),
                    "humidity_pct": float(row["humidity_pct"]),
                    "rainfall_mm": float(row["rainfall_mm"]),
                    "wind_kph": float(row["wind_kph"]),
                }
            except (ValueError, TypeError, AttributeError):
                # A blank, malformed or non-numeric field lands here.
                skipped += 1
                continue
            observations.append(observation)

    # Sort by date so that the rolling average and the trend line are
    # calculated over a genuine time series, whatever order the file was in.
    observations.sort(key=lambda record: record["date"])
    return observations, skipped


# --------------------------------------------------------------------------
# Calculations
# --------------------------------------------------------------------------

def calculate_dew_point(temperature_c, humidity_pct):
    """Return the dew point in degrees Celsius (Magnus-Tetens approximation).

    The dew point is the temperature the air would have to fall to before the
    water vapour it holds would condense, so it is a better measure of how
    humid the air actually feels than relative humidity on its own.
    """
    # Relative humidity is used inside a logarithm, so a zero reading would
    # raise a maths error. Clamping to a very small value keeps the formula
    # defined without noticeably changing a realistic result.
    humidity = max(humidity_pct, 0.1)
    alpha = ((MAGNUS_A * temperature_c) / (MAGNUS_B + temperature_c)
             + math.log(humidity / 100.0))
    return (MAGNUS_B * alpha) / (MAGNUS_A - alpha)


def calculate_feels_like(temperature_c, humidity_pct, wind_kph):
    """Return the apparent ("feels like") temperature in degrees Celsius.

    This uses the Australian Bureau of Meteorology apparent-temperature
    formula, which adjusts the measured temperature upwards for humid air and
    downwards for wind. It is used here because, unlike wind chill or heat
    index, a single formula covers the whole range of a temperate climate.
    """
    wind_ms = wind_kph / 3.6  # the formula expects metres per second
    # Water vapour pressure in hectopascals, derived from relative humidity.
    vapour_pressure = ((humidity_pct / 100.0) * 6.105
                       * math.exp((MAGNUS_A * temperature_c)
                                  / (MAGNUS_B + temperature_c)))
    return temperature_c + (0.33 * vapour_pressure) - (0.70 * wind_ms) - 4.00


def calculate_rolling_mean(values, window):
    """Return a centred rolling mean of values over the given window size.

    Near the start and end of the series there are not enough neighbours for a
    full window, so the average is taken over however many values do exist.
    That keeps the output the same length as the input, which makes the CSV
    much easier to read in a spreadsheet.
    """
    smoothed = []
    half = window // 2
    for index in range(len(values)):
        start = max(0, index - half)
        end = min(len(values), index + half + 1)
        window_values = values[start:end]
        smoothed.append(sum(window_values) / len(window_values))
    return smoothed


def calculate_linear_trend(values):
    """Fit a straight line to values by least squares and return its slope.

    The slope is expressed as a change per step (here, per day). Working out
    the trend this way avoids simply comparing the first and last readings,
    which on a single noisy day could be very misleading.
    """
    count = len(values)
    if count < 2:
        return 0.0

    # x is simply the position in the series: 0, 1, 2, ...
    mean_x = (count - 1) / 2.0
    mean_y = sum(values) / count

    numerator = 0.0
    denominator = 0.0
    for index in range(count):
        x_difference = index - mean_x
        numerator += x_difference * (values[index] - mean_y)
        denominator += x_difference * x_difference

    # denominator is only zero when every x is identical, which cannot happen
    # for two or more points, but the guard keeps the function safe to reuse.
    if denominator == 0:
        return 0.0
    return numerator / denominator


def build_daily_metrics(observations, window):
    """Turn raw observations into the per-day derived values.

    Each returned dictionary matches OUTPUT_COLUMNS, so writing the CSV later
    needs no further rearranging.
    """
    # The rolling mean needs the whole temperature series at once, so the mean
    # temperature for every day is calculated first.
    mean_temperatures = []
    for observation in observations:
        mean_temperatures.append(
            (observation["temp_max_c"] + observation["temp_min_c"]) / 2.0)

    rolling_means = calculate_rolling_mean(mean_temperatures, window)

    daily = []
    for index, observation in enumerate(observations):
        mean_temp = mean_temperatures[index]
        daily.append({
            "date": observation["date"].isoformat(),
            "month": observation["date"].strftime("%Y-%m"),
            "temp_mean_c": round(mean_temp, 2),
            # The diurnal range shows how far the temperature swung that day.
            "temp_range_c": round(observation["temp_max_c"]
                                  - observation["temp_min_c"], 2),
            "dew_point_c": round(
                calculate_dew_point(mean_temp, observation["humidity_pct"]), 2),
            "feels_like_c": round(
                calculate_feels_like(mean_temp, observation["humidity_pct"],
                                     observation["wind_kph"]), 2),
            "rolling_mean_c": round(rolling_means[index], 2),
            # Selection: each day is classified against a threshold.
            "is_rain_day": int(observation["rainfall_mm"] >= RAIN_DAY_THRESHOLD_MM),
            "is_frost_day": int(observation["temp_min_c"] < 0.0),
        })
    return daily


def summarise(observations, daily):
    """Calculate the whole-period statistics used by the report."""
    mean_temps = [record["temp_mean_c"] for record in daily]
    rainfall = [observation["rainfall_mm"] for observation in observations]
    humidity = [observation["humidity_pct"] for observation in observations]

    # The warmest and coldest days are found by asking max() and min() to
    # compare the observations by a single field.
    warmest = max(observations, key=lambda record: record["temp_max_c"])
    coldest = min(observations, key=lambda record: record["temp_min_c"])
    wettest = max(observations, key=lambda record: record["rainfall_mm"])
    windiest = max(observations, key=lambda record: record["wind_kph"])

    # The daily slope is scaled to a per-month figure, which is a far easier
    # number to interpret than a fraction of a degree per day.
    daily_slope = calculate_linear_trend(mean_temps)

    return {
        "days": len(daily),
        "first_date": observations[0]["date"].isoformat(),
        "last_date": observations[-1]["date"].isoformat(),
        "mean_temp": sum(mean_temps) / len(mean_temps),
        "mean_humidity": sum(humidity) / len(humidity),
        "total_rainfall": sum(rainfall),
        "mean_rainfall": sum(rainfall) / len(rainfall),
        "rain_days": sum(record["is_rain_day"] for record in daily),
        "frost_days": sum(record["is_frost_day"] for record in daily),
        "warmest_date": warmest["date"].isoformat(),
        "warmest_value": warmest["temp_max_c"],
        "coldest_date": coldest["date"].isoformat(),
        "coldest_value": coldest["temp_min_c"],
        "wettest_date": wettest["date"].isoformat(),
        "wettest_value": wettest["rainfall_mm"],
        "windiest_date": windiest["date"].isoformat(),
        "windiest_value": windiest["wind_kph"],
        "trend_per_month": daily_slope * 30.44,  # average days in a month
    }


def summarise_by_month(observations, daily):
    """Group the data by calendar month and return one summary row per month.

    A dictionary is used to collect the values for each month as the data is
    read once, which is far more efficient than searching the whole list again
    for every month in the file.
    """
    buckets = {}
    for index, observation in enumerate(observations):
        key = daily[index]["month"]
        if key not in buckets:
            buckets[key] = {"temps": [], "rain": 0.0, "rain_days": 0}
        buckets[key]["temps"].append(daily[index]["temp_mean_c"])
        buckets[key]["rain"] += observation["rainfall_mm"]
        buckets[key]["rain_days"] += daily[index]["is_rain_day"]

    rows = []
    for key in sorted(buckets):
        bucket = buckets[key]
        month_number = int(key.split("-")[1])
        rows.append({
            "month": key,
            "name": MONTH_NAMES[month_number],
            "days": len(bucket["temps"]),
            "mean_temp": sum(bucket["temps"]) / len(bucket["temps"]),
            "min_temp": min(bucket["temps"]),
            "max_temp": max(bucket["temps"]),
            "rainfall": bucket["rain"],
            "rain_days": bucket["rain_days"],
        })
    return rows


# --------------------------------------------------------------------------
# Writing the output files
# --------------------------------------------------------------------------

def ensure_folder(path):
    """Create the folder that path sits in, if it does not already exist."""
    folder = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(folder):
        os.makedirs(folder)


def write_daily_csv(daily, output_path):
    """Write the per-day derived values to a CSV file."""
    ensure_folder(output_path)
    with open(output_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(daily)


def format_report(summary, monthly, context):
    """Build the text of the summary report and return it as one string.

    Keeping the formatting separate from the file writing means the same text
    can also be printed to the screen without being generated twice.
    """
    lines = []
    lines.append("=" * 68)
    lines.append("WEATHER DATA PROCESSING REPORT")
    lines.append("=" * 68)
    lines.append("Generated        : " + context["generated"])
    lines.append("Input file       : " + context["input_path"])
    lines.append("Daily metrics    : " + context["output_path"])
    lines.append("Rows read        : " + str(summary["days"] + context["skipped"]))
    lines.append("Rows processed   : " + str(summary["days"]))
    lines.append("Rows skipped     : " + str(context["skipped"]))
    lines.append("Period covered   : " + summary["first_date"]
                 + " to " + summary["last_date"])
    lines.append("Rolling window   : " + str(context["window"]) + " days")
    lines.append("")

    lines.append("-" * 68)
    lines.append("SUMMARY STATISTICS")
    lines.append("-" * 68)
    lines.append("Mean temperature      : {0:8.2f} C".format(summary["mean_temp"]))
    lines.append("Mean humidity         : {0:8.2f} %".format(summary["mean_humidity"]))
    lines.append("Total rainfall        : {0:8.1f} mm".format(summary["total_rainfall"]))
    lines.append("Mean daily rainfall   : {0:8.2f} mm".format(summary["mean_rainfall"]))
    lines.append("Rain days             : {0:8d} of {1} ({2:.1f}%)".format(
        summary["rain_days"], summary["days"],
        100.0 * summary["rain_days"] / summary["days"]))
    lines.append("Frost days            : {0:8d} of {1} ({2:.1f}%)".format(
        summary["frost_days"], summary["days"],
        100.0 * summary["frost_days"] / summary["days"]))
    lines.append("Temperature trend     : {0:+8.2f} C per month".format(
        summary["trend_per_month"]))
    lines.append("")

    lines.append("-" * 68)
    lines.append("NOTABLE DAYS")
    lines.append("-" * 68)
    lines.append("Warmest  : {0}  {1:6.1f} C maximum".format(
        summary["warmest_date"], summary["warmest_value"]))
    lines.append("Coldest  : {0}  {1:6.1f} C minimum".format(
        summary["coldest_date"], summary["coldest_value"]))
    lines.append("Wettest  : {0}  {1:6.1f} mm rainfall".format(
        summary["wettest_date"], summary["wettest_value"]))
    lines.append("Windiest : {0}  {1:6.1f} kph wind".format(
        summary["windiest_date"], summary["windiest_value"]))
    lines.append("")

    lines.append("-" * 68)
    lines.append("MONTHLY BREAKDOWN")
    lines.append("-" * 68)
    lines.append("{0:<11}{1:>6}{2:>10}{3:>9}{4:>9}{5:>11}{6:>7}".format(
        "Month", "Days", "Mean C", "Min C", "Max C", "Rain mm", "Wet"))
    for row in monthly:
        lines.append("{0:<11}{1:>6}{2:>10.2f}{3:>9.2f}{4:>9.2f}{5:>11.1f}{6:>7}".format(
            row["name"], row["days"], row["mean_temp"], row["min_temp"],
            row["max_temp"], row["rainfall"], row["rain_days"]))
    lines.append("")
    lines.append("=" * 68)
    lines.append("End of report")
    lines.append("=" * 68)

    return "\n".join(lines)


def write_report(text, report_path):
    """Write the report text to disk."""
    ensure_folder(report_path)
    with open(report_path, "w", encoding="utf-8") as handle:
        handle.write(text + "\n")


# --------------------------------------------------------------------------
# Command line handling
# --------------------------------------------------------------------------

def parse_arguments(argv):
    """Define and read the command line arguments.

    argparse is used rather than reading sys.argv by hand because it validates
    the arguments, converts the window size to an integer and produces a
    proper --help message at no extra cost.
    """
    parser = argparse.ArgumentParser(
        prog="weather_processor.py",
        description="Process a CSV of daily weather observations into "
                    "per-day metrics and a summary report.")
    parser.add_argument("input", help="path to the input CSV file")
    parser.add_argument("-o", "--output", default="output/daily_metrics.csv",
                        help="path for the per-day CSV "
                             "(default: output/daily_metrics.csv)")
    parser.add_argument("-r", "--report", default="output/summary_report.txt",
                        help="path for the text report "
                             "(default: output/summary_report.txt)")
    parser.add_argument("-w", "--window", type=int, default=7,
                        help="rolling average window in days (default: 7)")
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="do not print the report to the screen")
    return parser.parse_args(argv[1:])


def main(argv):
    """Run the whole pipeline and return the process exit code."""
    arguments = parse_arguments(argv)

    # Validate the arguments before touching any files, so that a mistake is
    # reported immediately and nothing is half written.
    if arguments.window < 1:
        print("ERROR: the rolling window must be at least 1 day")
        return 1
    if not os.path.isfile(arguments.input):
        print("ERROR: input file not found: " + arguments.input)
        return 1

    # Every foreseeable file or data problem is caught here and reported as a
    # readable message, so the user of the batch script never sees a traceback.
    try:
        observations, skipped = read_observations(arguments.input)
    except (OSError, ValueError, csv.Error) as error:
        print("ERROR: could not read " + arguments.input + ": " + str(error))
        return 1

    if not observations:
        print("ERROR: no usable rows were found in " + arguments.input)
        print("       Try running clean_data.py on the file first.")
        return 1

    # ---- the three processing stages: derive, summarise, write -------------
    daily = build_daily_metrics(observations, arguments.window)
    summary = summarise(observations, daily)
    monthly = summarise_by_month(observations, daily)

    context = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "input_path": arguments.input,
        "output_path": arguments.output,
        "skipped": skipped,
        "window": arguments.window,
    }
    report_text = format_report(summary, monthly, context)

    try:
        write_daily_csv(daily, arguments.output)
        write_report(report_text, arguments.report)
    except OSError as error:
        print("ERROR: could not write the output files: " + str(error))
        return 1

    if not arguments.quiet:
        print(report_text)
    print("")
    print("Written " + str(len(daily)) + " rows to " + arguments.output)
    print("Written report to " + arguments.report)
    if skipped:
        print("Note: " + str(skipped) + " unreadable row(s) were skipped.")
    return 0


# Only run main() when this file is executed directly, so the functions above
# can also be imported and tested individually.
if __name__ == "__main__":
    sys.exit(main(sys.argv))
