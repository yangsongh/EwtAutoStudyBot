@echo off

:: 解除文件占用
taskkill /im EwtAutoStudyBot.exe /f >nul 2>&1

pyinstaller --distpath build --workpath %PYINSTALLER_CACHE_DIR% --noconfirm EwtAutoStudyBot.spec
