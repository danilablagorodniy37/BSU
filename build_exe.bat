@echo off
REM ==========================================================================
REM  build_exe.bat - package weather_processor.py as a standalone executable.
REM
REM  The brief asks for the program to be exported as an executable file.
REM  PyInstaller is used to bundle the script and the Python runtime into a
REM  single .exe that runs on a machine with no Python installed.
REM
REM  The finished program is written to dist\weather_processor.exe, which is
REM  exactly where run_analysis.bat looks for it.
REM
REM  Usage:
REM      build_exe.bat
REM ==========================================================================

setlocal
pushd "%~dp0"

echo ==========================================================
echo  Building weather_processor.exe
echo ==========================================================

REM PyInstaller is a separate package, so check it is installed before trying
REM to use it and tell the user exactly how to fix it if it is not.
python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: PyInstaller is not installed.
    echo        Install it with:  pip install pyinstaller
    popd
    endlocal
    exit /b 1
)

REM --onefile   bundle everything into a single .exe
REM --console   keep the console window, since this is a command line tool
REM --clean     discard any cached files from a previous build
REM --name      set the name of the finished executable
python -m PyInstaller --onefile --console --clean --name weather_processor weather_processor.py
if errorlevel 1 (
    echo.
    echo ERROR: the build failed. See the PyInstaller output above.
    popd
    endlocal
    exit /b 1
)

echo.
echo ==========================================================
echo  Build complete: dist\weather_processor.exe
echo ==========================================================
echo  Test it with:
echo      dist\weather_processor.exe data\weather_cleaned.csv
echo.

popd
endlocal
exit /b 0
