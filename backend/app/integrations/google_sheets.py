from __future__ import annotations

import re
from typing import Any

import httpx

from app.models.integration_connection import IntegrationConnection
from app.services.integrations import IntegrationService


class GoogleSheetsError(Exception):
    pass


def _header(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().casefold()).strip("_")


def normalize_row(headers: list[Any], values: list[Any], row_number: int) -> dict[str, str]:
    data = {_header(h): str(values[i]).strip() if i < len(values) and values[i] is not None else "" for i, h in enumerate(headers) if _header(h)}
    aliases = {
        "phone": "phone_number", "mobile": "phone_number", "mobile_number": "phone_number",
        "tel": "phone_number", "full_name": "name", "contact_name": "name",
    }
    for old, new in aliases.items():
        if data.get(old) and not data.get(new): data[new] = data[old]
    if data.get("name") and not data.get("first_name"):
        parts = data["name"].split(None, 1); data["first_name"] = parts[0]; data["last_name"] = parts[1] if len(parts) > 1 else ""
    data["external_id"] = data.get("id") or data.get("contact_id") or ""
    data["row_number"] = str(row_number)
    return data


class GoogleSheetsAdapter:
    def __init__(self, http_client: httpx.Client | None = None):
        self.http = http_client or httpx.Client(timeout=20.0)

    def _token(self, conn: IntegrationConnection, integration: IntegrationService) -> str:
        credentials = integration.get_decrypted_credentials(conn)
        token = credentials.get("access_token") or credentials.get("token")
        if not token:
            raise GoogleSheetsError("Google OAuth connection is missing an access token.")
        return token

    def read(self, conn: IntegrationConnection, spreadsheet_id: str, sheet_name: str, integration: IntegrationService) -> list[dict[str, str]]:
        if conn.status != "connected": raise GoogleSheetsError("Google OAuth connection is not connected.")
        token = self._token(conn, integration)
        range_name = f"'{sheet_name.replace(chr(39), chr(39) + chr(39))}'"
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{range_name}"
        response = self.http.get(url, headers={"Authorization": f"Bearer {token}"}, params={"majorDimension": "ROWS"})
        if response.status_code >= 400: raise GoogleSheetsError(f"Google Sheets request failed ({response.status_code}).")
        values = response.json().get("values") or []
        if not values: return []
        headers = values[0]
        if not any(_header(h) for h in headers): raise GoogleSheetsError("The sheet header row is empty.")
        return [normalize_row(headers, row, index) for index, row in enumerate(values[1:], start=2)]

    def test(self, conn: IntegrationConnection, spreadsheet_id: str, sheet_name: str, integration: IntegrationService) -> int:
        return len(self.read(conn, spreadsheet_id, sheet_name, integration))
