@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================================
echo   LAS Point Cloud Tool  -  Launcher
echo ============================================================
echo.

rem ============================================================
rem  1. Find Python
rem ============================================================
set "PYEXE="

rem -- py launcher: prefer a version that has prebuilt wheels (3.12 first),
rem    so we never fall back to compiling numpy/scipy from source.
where py >nul 2>&1
if !errorlevel! equ 0 (
    for %%v in (3.12 3.11 3.13 3.10 3) do (
        if not defined PYEXE (
            py -%%v -c "import sys" >nul 2>&1
            if !errorlevel! equ 0 set "PYEXE=py -%%v"
        )
    )
)

rem -- fallback to python, skipping the Microsoft Store stub
if not defined PYEXE (
    where python >nul 2>&1
    if !errorlevel! equ 0 (
        python -c "import sys; print(sys.executable)" 2>nul | find /i "WindowsApps" >nul
        if !errorlevel! neq 0 (
            python -c "import sys" >nul 2>&1 && set "PYEXE=python"
        )
    )
)

rem ============================================================
rem  2. Not found -> install the bundled Python
rem ============================================================
if not defined PYEXE (
    echo Python was not found. Installing the bundled Python...
    echo.

    set "INSTALLER="
    for %%f in (python-3*-amd64.exe) do set "INSTALLER=%%f"
    if not defined INSTALLER (
        echo [ERROR] Python installer not found.
        echo   Please download "python-3.x.x-amd64.exe" from python.org
        echo   and put it into this folder:
        echo   %~dp0
        echo.
        pause
        exit /b 1
    )

    echo Installing: !INSTALLER!
    echo  Please wait. This may take a few minutes.
    "!INSTALLER!" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_launcher=1
    set "RC=!errorlevel!"
    if !RC! neq 0 if !RC! neq 3010 (
        echo [ERROR] Installation failed. code=!RC!
        pause
        exit /b 1
    )

    rem -- PATH is not refreshed yet, so locate python.exe directly
    where py >nul 2>&1 && set "PYEXE=py -3"
    if not defined PYEXE (
        for /d %%d in ("%LocalAppData%\Programs\Python\Python3*") do (
            if exist "%%d\python.exe" set "PYEXE=%%d\python.exe"
        )
    )
    if not defined PYEXE (
        for /d %%d in ("%ProgramFiles%\Python3*") do (
            if exist "%%d\python.exe" set "PYEXE=%%d\python.exe"
        )
    )
    if not defined PYEXE (
        echo Installed, but Python could not be auto-detected.
        echo Please restart this PC, then run this file again.
        pause
        exit /b 1
    )
    echo Python installation finished.
    echo.
)

echo Using Python: !PYEXE!
echo.

rem ============================================================
rem  3. Check / install required libraries
rem ============================================================
!PYEXE! -c "import tkinter, tkinter.ttk; from PIL import ImageTk; import numpy, laspy, ezdxf, scipy.spatial, matplotlib, open3d" >nul 2>&1
if !errorlevel! neq 0 (
    echo Installing required libraries. First run only, a few minutes...
    echo.
    !PYEXE! -m pip install --upgrade pip
    rem -- --prefer-binary: use prebuilt wheels, never compile from source
    !PYEXE! -m pip install --prefer-binary -r requirements.txt
    echo.
    rem -- verify the libraries actually import now
    !PYEXE! -c "import tkinter, tkinter.ttk; from PIL import ImageTk; import numpy, laspy, ezdxf, scipy.spatial, matplotlib" 2>"%~dp0_error_log.txt"
    if !errorlevel! neq 0 (
        for /f "usebackq delims=" %%a in (`!PYEXE! -c "import sys;print(sys.version.split()[0])"`) do set "PYVER=%%a"
        echo [ERROR] Libraries could not be installed.
        echo.
        echo  Detected Python version: !PYVER!
        echo  This usually means this Python version is too NEW and the
        echo  libraries -numpy/scipy- have no prebuilt package for it yet,
        echo  so pip tried to compile them and failed -no C++ compiler-.
        echo.
        echo  FIX: install Python 3.12 from python.org on this PC,
        echo       then run this file again.
        echo  -file: python-3.12.x-amd64.exe-
        echo.
        echo  --- details ---
        type "%~dp0_error_log.txt"
        echo  ---------------
        echo.
        pause
        exit /b 1
    )
    echo Libraries are ready.
    echo.
)

rem ============================================================
rem  4. Launch the app
rem ============================================================
echo Starting the app...
echo.
!PYEXE! gui_app.py 2>"%~dp0_error_log.txt"
set "APPRC=!errorlevel!"
if !APPRC! neq 0 (
    rem -- CMDウィンドウを復元して前面に出す（エラー確認用）--
    !PYEXE! -c "import ctypes; h=ctypes.windll.kernel32.GetConsoleWindow(); ctypes.windll.user32.ShowWindow(h,9); ctypes.windll.user32.SetForegroundWindow(h)" 2>nul
    echo.
    echo [ERROR] The app did not start normally. code=!APPRC!
    echo  --- error details ---
    type "%~dp0_error_log.txt"
    echo  ---------------------
    echo The full message is saved in: _error_log.txt
    echo.
    echo Press any key to close this window...
    pause >nul
)
rem -- 正常終了時はそのまま閉じる（pause なし）--
endlocal
