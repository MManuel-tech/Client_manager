@echo off
:: Activate the virtual environment
call C:\Users\DELL\Desktop\client_manager\venv\Scripts\activate.bat

:: Navigate to the app folder
cd C:\Users\DELL\Desktop\client_manager

:: Start the Flask app
start python app.py

:: Wait a few seconds for the server to start
timeout /t 5

:: Open the default browser at the app URL
start http://127.0.0.1:5000

:: Keep the command prompt open
pause
