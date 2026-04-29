@echo off
REM AISMART — 以管理員身分執行 Python 腳本
REM 用法：將此 .bat 拖曳到目標 .py 檔案上，或直接執行並輸入腳本路徑
REM 不需要手動開啟管理員命令提示字元

cd /d "%~dp0"

if "%~1"=="" (
    echo 用法：將 .py 檔案拖曳到此 .bat 上
    echo 或：run_as_admin.bat scripts\test_el_compile.py
    pause
    exit /b 1
)

python "%~1" %2 %3 %4 %5
pause
