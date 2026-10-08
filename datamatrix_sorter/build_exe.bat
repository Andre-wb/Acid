@echo off
rem Сборка одного .exe (запускать на Windows, нужен Python 3.9+)
python -m venv venv
call venv\Scripts\activate
pip install -r requirements.txt
pyinstaller --onefile --windowed --name CHZ_Sverka --collect-all zxingcpp app.py
echo.
echo Готово: dist\CHZ_Sverka.exe
pause
