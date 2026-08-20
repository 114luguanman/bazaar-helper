@echo off
rem 静默启动（无黑框）：用 pythonw.exe 运行，界面由主程序弹出
cd /d %~dp0
start "" "C:\Users\hu120\AppData\Local\Programs\Python\Python313\pythonw.exe" "%~dp0run_app.py"
exit
