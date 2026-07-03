# AI Business Automation Platform

## Python environment

- Python: 3.10.x
- Create and activate a virtual environment before installing dependencies:
  - Windows PowerShell: `.venv\Scripts\Activate.ps1`
  - Windows Command Prompt: `.venv\Scripts\activate.bat`
- Install dependencies:
  - `pip install -r requirements.txt`
- Run the API locally:
  - `uvicorn app.main:app --reload`

## Project layout

The repository includes the application package, supporting folders for orchestration, services, utilities, tests, logs, and migrations.

## Environment configuration

Copy `.env.example` to `.env` and update values before running the app.
