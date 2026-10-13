@echo off
echo Stopping existing GUI process if running...
taskkill /F /IM GUI.exe >nul 2>&1

echo Building single-file GUI.exe...
dotnet publish GUI_CSharp/GUI.csproj -c Release -r win-x64 --self-contained false -p:PublishSingleFile=true -o publish_out

if %ERRORLEVEL% equ 0 (
    move /Y publish_out\GUI.exe GUI.exe >nul
    if exist "C:\GUI.exe" copy /Y GUI.exe C:\GUI.exe >nul 2>&1
    rmdir /S /Q publish_out >nul 2>&1
    echo.
    echo ========================================
    echo  Build SUCCESS! Output: GUI.exe
    echo ========================================
) else (
    echo.
    echo [ERROR] Build failed! Check errors above.
)
