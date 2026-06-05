from __future__ import annotations

import os
import sys
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials


APP_DIR = Path(__file__).resolve().parent
CREDENTIALS_FILE = APP_DIR / "credentials.json"
SHEET_ID_FILE = APP_DIR / "sheet_id.txt"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def main() -> int:
    sheet_id = (
        sys.argv[1].strip()
        if len(sys.argv) > 1
        else os.environ.get("TIRE_ANNOTATION_GOOGLE_SHEET_ID", "").strip()
    )
    if not sheet_id and SHEET_ID_FILE.exists():
        sheet_id = SHEET_ID_FILE.read_text().strip()
    if "/spreadsheets/d/" in sheet_id:
        sheet_id = sheet_id.split("/spreadsheets/d/", 1)[1].split("/", 1)[0]

    if not sheet_id:
        print("Missing sheet ID. Pass it as an argument or create sheet_id.txt.")
        return 2
    if not CREDENTIALS_FILE.exists():
        print(f"Missing credentials file: {CREDENTIALS_FILE}")
        return 2

    print(f"credentials: {CREDENTIALS_FILE}")
    print(f"sheet_id: {sheet_id}")
    creds = Credentials.from_service_account_file(str(CREDENTIALS_FILE), scopes=SCOPES)
    print(f"client_email: {creds.service_account_email}")
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(sheet_id)
    sheet = spreadsheet.sheet1
    print(f"connected: {spreadsheet.title} / {sheet.title}")
    values = sheet.get_all_values()
    print(f"rows: {len(values)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
