@echo off
REM ==========================================================================
REM  run_analysis.bat - Windows command line script for the weather pipeline.
REM
REM  This script is the entry point for the whole project. It runs the two
REM  stages of the pipeline in order and stops with a clear message if either
REM  of them fails:
REM
REM      stage 1  clean_data.py        raw observations -> validated CSV
REM      stage 2  weather_processor.py validated CSV    -> metrics + report
REM
REM  Usage:
REM      run_analysis.bat                     uses data\raw_weather_2025.csv
REM      run_analysis.bat <input_csv>         uses the file you supply
REM      run_analysis.bat <input_csv> <days>  also sets the rolling window
REM ==========================================================================

REM Keep the variables created below local to this script, and switch on
REM delayed expansion so values set inside IF blocks can be read back.
setlocal enabledelayedexpansion

REM Work from the folder this script lives in, so the relative paths below are
REM correct no matter which directory the user called the script from.
pushd "%~dp0"

REM ---- Settings -----------------------------------------------------------
REM %~1 removes any surrounding quotes from the first argument. If the user
REM did not supply one, fall back to the sample data shipped with the project.
set "INPUT_FILE=%~1"
if "%INPUT_FILE%"=="" set "INPUT_FILE=data\raw_weather_2025.csv"

set "WINDOW=%~2"
if "%WINDOW%"=="" set "WINDOW=7"

set "CLEAN_FILE=data\weather_cleaned.csv"
set "METRICS_FILE=output\daily_metrics.csv"
set "REPORT_FILE=output\summary_report.txt"

echo ==========================================================
echo  Weather Data Processing Pipeline
echo ==========================================================
echo  Input file     : %INPUT_FILE%
echo  Rolling window : %WINDOW% days
echo.

REM ---- Check the prerequisites before doing any work ----------------------
REM Confirm Python is on the PATH; without it neither stage can run.
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python was not found on the PATH.
    echo        Install Python 3 and tick "Add Python to PATH", then retry.
    goto :failed
)

REM Confirm the input file actually exists before starting stage 1.
if not exist "%INPUT_FILE%" (
    echo ERROR: the input file "%INPUT_FILE%" does not exist.
    goto :failed
)

REM ---- Stage 1: clean the raw data ----------------------------------------
echo [1/2] Cleaning the raw observations...
python clean_data.py "%INPUT_FILE%" "%CLEAN_FILE%"
REM errorlevel 1 is true for any exit code of 1 or more, so this catches the
REM error return values used by both Python scripts.
if errorlevel 1 (
    echo.
    echo ERROR: the cleaning stage failed, so processing has been stopped.
    goto :failed
)
echo.

REM ---- Stage 2: process the cleaned data ----------------------------------
REM The executable built by build_exe.bat is preferred when it is present,
REM because it runs without needing Python installed; otherwise the script
REM falls back to running the .py file through the interpreter.
echo [2/2] Processing the cleaned data...
if exist "dist\weather_processor.exe" (
    echo       using the packaged executable
    dist\weather_processor.exe "%CLEAN_FILE%" -o "%METRICS_FILE%" -r "%REPORT_FILE%" -w %WINDOW% -q
) else (
    echo       using the Python source
    python weather_processor.py "%CLEAN_FILE%" -o "%METRICS_FILE%" -r "%REPORT_FILE%" -w %WINDOW% -q
)
if errorlevel 1 (
    echo.
    echo ERROR: the processing stage failed.
    goto :failed
)

REM ---- Finished -----------------------------------------------------------
echo.
echo ==========================================================
echo  Pipeline finished successfully
echo ==========================================================
echo  Cleaned data : %CLEAN_FILE%
echo  Daily metrics: %METRICS_FILE%
echo  Report       : %REPORT_FILE%
echo.
echo  --- report preview ---------------------------------------
REM Show the first 20 lines of the report so the user gets immediate feedback
REM without having to open the file.
powershell -NoProfile -Command "Get-Content '%REPORT_FILE%' -TotalCount 20"
echo  ----------------------------------------------------------

popd
endlocal
exit /b 0

:failed
REM A single exit path for every error keeps the messages consistent and
REM makes sure the working directory is always restored.
echo.
popd
endlocal
exit /b 1
