# AI Factory — Personal Etsy Shop Automation Tool

## About this application

AI Factory is a **personal tool built and used solely by its developer** to help manage
listings for a single Etsy shop. It is not a public product, service,
or SaaS platform, and it is not intended for use by any other Etsy seller.

**What it does:**
- Helps draft product titles, descriptions, and tags using AI, based on product details
  provided by the shop owner
- Uses the Etsy Open API v3 to create these listings as **drafts** in the owner's own shop
- All drafts are manually reviewed and published by the shop owner — the app does not
  auto-publish listings


**Etsy API scopes used:**
- `listings_r` — read the owner's own existing listings
- `listings_w` — create and update draft listings in the owner's own shop
- `shops_r` — read the owner's own shop information (e.g. shipping profiles)

This application is developed and operated by a single individual, for their own shop only.

---

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
