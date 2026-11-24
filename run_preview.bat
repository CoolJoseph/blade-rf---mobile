@echo off
rem Preview the phone app on this PC (simulated radio), in Chrome.
py -3 -m pip install --quiet numpy
start "" http://127.0.0.1:8765/
py -3 "%~dp0android\app\src\main\python\mobile_app.py" --sim
pause
