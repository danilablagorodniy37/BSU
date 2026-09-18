#!/usr/bin/env bash
# ===========================================================================
#  run_analysis.sh - Linux/macOS command line script for the weather pipeline.
#
#  This is the POSIX shell equivalent of run_analysis.bat, provided so the
#  project can be run on a machine that has no Windows command prompt. It
#  performs exactly the same two stages:
#
#      stage 1  clean_data.py        raw observations -> validated CSV
#      stage 2  weather_processor.py validated CSV    -> metrics + report
#
#  Usage:
#      ./run_analysis.sh                     uses data/raw_weather_2025.csv
#      ./run_analysis.sh <input_csv>         uses the file you supply
#      ./run_analysis.sh <input_csv> <days>  also sets the rolling window
# ===========================================================================

# -e stops the script on the first failing command, -u treats an unset
# variable as an error, and pipefail makes a failure anywhere in a pipeline
# fail the whole pipeline. Together they stop the script running on with data
# that was never produced.
set -euo pipefail

# Work from the folder holding this script so the relative paths below resolve
# correctly whatever directory the user called it from.
cd "$(dirname "$0")"

# ---- Settings -------------------------------------------------------------
# ${1:-default} means "use argument 1 if it was given, otherwise the default".
INPUT_FILE="${1:-data/raw_weather_2025.csv}"
WINDOW="${2:-7}"
CLEAN_FILE="data/weather_cleaned.csv"
METRICS_FILE="output/daily_metrics.csv"
REPORT_FILE="output/summary_report.txt"

echo "=========================================================="
echo " Weather Data Processing Pipeline"
echo "=========================================================="
echo " Input file     : ${INPUT_FILE}"
echo " Rolling window : ${WINDOW} days"
echo

# ---- Check the prerequisites before doing any work ------------------------
# Distributions differ over whether the interpreter is called python3 or
# python, so look for both rather than assuming one of them.
if command -v python3 >/dev/null 2>&1; then
    PYTHON="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON="python"
else
    echo "ERROR: no Python interpreter was found on the PATH." >&2
    exit 1
fi

if [ ! -f "${INPUT_FILE}" ]; then
    echo "ERROR: the input file '${INPUT_FILE}' does not exist." >&2
    exit 1
fi

# ---- Stage 1: clean the raw data ------------------------------------------
echo "[1/2] Cleaning the raw observations..."
# The exit status is tested explicitly so the script can print its own
# explanation before stopping, rather than just dying silently under set -e.
if ! "${PYTHON}" clean_data.py "${INPUT_FILE}" "${CLEAN_FILE}"; then
    echo >&2
    echo "ERROR: the cleaning stage failed, so processing has been stopped." >&2
    exit 1
fi
echo

# ---- Stage 2: process the cleaned data ------------------------------------
echo "[2/2] Processing the cleaned data..."
if ! "${PYTHON}" weather_processor.py "${CLEAN_FILE}" \
        -o "${METRICS_FILE}" -r "${REPORT_FILE}" -w "${WINDOW}" -q; then
    echo >&2
    echo "ERROR: the processing stage failed." >&2
    exit 1
fi

# ---- Finished -------------------------------------------------------------
echo
echo "=========================================================="
echo " Pipeline finished successfully"
echo "=========================================================="
echo " Cleaned data : ${CLEAN_FILE}"
echo " Daily metrics: ${METRICS_FILE}"
echo " Report       : ${REPORT_FILE}"
echo
echo " --- report preview ---------------------------------------"
head -n 20 "${REPORT_FILE}"
echo " ----------------------------------------------------------"
