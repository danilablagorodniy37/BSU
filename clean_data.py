"""
clean_data.py - pre-processing stage of the weather analysis pipeline.

Purpose
-------
Raw observation files exported from a weather station are rarely tidy. They
contain blank fields, sentinel values left behind by failed sensors, duplicated
rows and occasionally readings that are physically impossible. Feeding that
straight into the statistics program would produce misleading results, so this
script is run first to produce a clean file that the main program can trust.

Usage
-----
    python clean_data.py <input_csv> <output_csv>

Example
-------
    python clean_data.py data/raw_weather_2025.csv data/weather_cleaned.csv

Exit codes
----------
    0 - cleaning finished successfully
    1 - the input file could not be read, or contained no usable rows
"""

import csv
import os
import sys
from datetime import datetime

# The exact column order the rest of the pipeline expects. Keeping this in one
# place means a change to the file format only has to be made in one place.
EXPECTED_COLUMNS = [
    "date",
    "temp_max_c",
    "temp_min_c",
    "humidity_pct",
    "rainfall_mm",
    "wind_kph",
]

# Values a weather station writes when a sensor has failed. They look like
# ordinary numbers, so they must be filtered out explicitly.
SENTINEL_VALUES = {-999.0, -99.9, 9999.0}

# Physically plausible limits. A reading outside these bounds is a fault
# rather than weather, so the whole row is rejected.
VALID_RANGES = {
    "temp_max_c": (-40.0, 55.0),
    "temp_min_c": (-50.0, 45.0),
    "humidity_pct": (0.0, 100.0),
    "rainfall_mm": (0.0, 500.0),
    "wind_kph": (0.0, 250.0),
}


def parse_date(text):
    """Return a date object for an ISO date string, or None if it is invalid.

    Returning None instead of raising an exception lets the caller treat a bad
    date as just another rejected row, which keeps the cleaning loop simple.
    """
    try:
        return datetime.strptime(text.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        return None


def parse_number(text):
    """Convert a CSV field to a float, or return None if it is not usable.

    Blank fields, text such as "n/a" and sentinel codes such as -999 all mean
    "no reading", so they are collapsed into a single None result.
    """
    if text is None:
        return None
    text = text.strip()
    if text == "":
        return None
    try:
        value = float(text)
    except ValueError:
        # The field contained something that is not a number at all.
        return None
    if value in SENTINEL_VALUES:
        return None
    return value


def check_row(row):
    """Validate one CSV row.

    Returns a (cleaned_row, reason) pair. On success cleaned_row is a
    dictionary and reason is None; on failure cleaned_row is None and reason is
    a short string used later to build the rejection summary.
    """
    # 1. The date must be present and well formed, because it identifies the
    #    observation and is used to put the file in chronological order.
    observation_date = parse_date(row.get("date", ""))
    if observation_date is None:
        return None, "invalid or missing date"

    # 2. Every numeric field must convert cleanly and sit inside a sensible
    #    range for that measurement.
    cleaned = {"date": observation_date.isoformat()}
    for column in EXPECTED_COLUMNS[1:]:
        value = parse_number(row.get(column, ""))
        if value is None:
            return None, "missing or non-numeric " + column
        low, high = VALID_RANGES[column]
        if value < low or value > high:
            return None, column + " outside valid range"
        cleaned[column] = value

    # 3. A cross-field check: the day's maximum cannot be below its minimum.
    if cleaned["temp_max_c"] < cleaned["temp_min_c"]:
        return None, "temp_max_c lower than temp_min_c"

    return cleaned, None


def clean_file(input_path, output_path):
    """Read input_path, write the validated rows to output_path, return stats.

    The returned dictionary is printed as a short report so the user can see
    exactly what the cleaning stage decided to do.
    """
    kept = []
    seen_dates = set()
    rejected = 0
    duplicates = 0
    reasons = {}

    with open(input_path, "r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)

        # Guard against being handed a file with the wrong columns entirely.
        if reader.fieldnames is None:
            raise ValueError("the input file is empty")
        missing = [c for c in EXPECTED_COLUMNS if c not in reader.fieldnames]
        if missing:
            raise ValueError("input file is missing column(s): " + ", ".join(missing))

        for row in reader:
            cleaned, reason = check_row(row)
            if cleaned is None:
                rejected += 1
                # Count each kind of problem so the report can summarise them.
                reasons[reason] = reasons.get(reason, 0) + 1
                continue
            # Only the first observation for a given date is kept; a repeat is
            # an export artefact rather than a genuine second measurement.
            if cleaned["date"] in seen_dates:
                duplicates += 1
                continue
            seen_dates.add(cleaned["date"])
            kept.append(cleaned)

    if not kept:
        raise ValueError("no valid rows were found in " + input_path)

    # Sorting by date guarantees the main program can rely on chronological
    # order when it calculates rolling averages and the temperature trend.
    kept.sort(key=lambda record: record["date"])

    # Create the destination folder if the user pointed at one that is missing.
    output_folder = os.path.dirname(os.path.abspath(output_path))
    if not os.path.isdir(output_folder):
        os.makedirs(output_folder)

    with open(output_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXPECTED_COLUMNS)
        writer.writeheader()
        writer.writerows(kept)

    return {
        "kept": len(kept),
        "rejected": rejected,
        "duplicates": duplicates,
        "reasons": reasons,
        "first_date": kept[0]["date"],
        "last_date": kept[-1]["date"],
    }


def main(argv):
    """Handle the command line, run the cleaner and report the outcome."""
    # Exactly two arguments are required, so anything else gets a usage message
    # rather than an unhelpful IndexError.
    if len(argv) != 3:
        print("Usage: python clean_data.py <input_csv> <output_csv>")
        print("Example: python clean_data.py data/raw_weather_2025.csv "
              "data/weather_cleaned.csv")
        return 1

    input_path = argv[1]
    output_path = argv[2]

    if not os.path.isfile(input_path):
        print("ERROR: input file not found: " + input_path)
        return 1

    # File and parsing problems are reported as plain messages so that a user
    # running the batch script never sees a raw Python traceback.
    try:
        stats = clean_file(input_path, output_path)
    except (OSError, ValueError, csv.Error) as error:
        print("ERROR: could not clean " + input_path + ": " + str(error))
        return 1

    print("Cleaning complete")
    print("  input file    : " + input_path)
    print("  output file   : " + output_path)
    print("  rows kept     : " + str(stats["kept"]))
    print("  duplicates    : " + str(stats["duplicates"]))
    print("  rows rejected : " + str(stats["rejected"]))
    for reason in sorted(stats["reasons"]):
        print("      - " + reason + ": " + str(stats["reasons"][reason]))
    print("  date range    : " + stats["first_date"] + " to " + stats["last_date"])
    return 0


# Only run main() when the file is executed directly, so that the module can
# also be imported by a test without anything happening on import.
if __name__ == "__main__":
    sys.exit(main(sys.argv))
