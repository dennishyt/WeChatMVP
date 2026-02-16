@echo off
setlocal

set BACKUP_ROOT=C:\Users\houyutong\Documents\WeChat Files\wxid_4i112lzyz6wp12\BackupFiles\iphone_49d3f4057bb51cef8bc2591a764e0641
set OUTPUT_DIR=C:\WeChatMvp\output

if not exist "%OUTPUT_DIR%" (
  mkdir "%OUTPUT_DIR%"
)

python "%~dp0wechat_byd_mvp.py" --backup-root "%BACKUP_ROOT%" --output-dir "%OUTPUT_DIR%"

echo.
echo Done. Press any key to exit.
pause >nul
