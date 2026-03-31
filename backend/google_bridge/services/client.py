from __future__ import annotations

from email.message import EmailMessage
from base64 import urlsafe_b64encode
from datetime import timedelta
from urllib.parse import quote

import httpx
from django.utils import timezone as django_timezone

from google_bridge.models import GoogleAccount
from google_bridge.services.http import request_with_retries
from google_bridge.services.oauth import refresh_access_token


class GoogleApiError(RuntimeError):
    pass


class GoogleBridgeClient:
    def __init__(self, account: GoogleAccount):
        self.account = account

    def _access_token(self) -> str:
        if self.account.access_token and not self._token_expired():
            return self.account.access_token
        if not self.account.refresh_token:
            raise GoogleApiError("Google account has no refresh token.")
        refreshed = refresh_access_token(self.account.refresh_token)
        access_token = str(refreshed.get("access_token") or "").strip()
        if not access_token:
            raise GoogleApiError("Google refresh did not return an access token.")
        expires_in = refreshed.get("expires_in")
        expires_at = None
        if isinstance(expires_in, (int, float)):
            expires_at = django_timezone.now() + timedelta(seconds=int(expires_in))
        self.account.set_tokens(access_token=access_token, refresh_token=str(refreshed.get("refresh_token") or ""))
        if expires_at is not None:
            self.account.token_expires_at = expires_at
        self.account.last_synced_at = django_timezone.now()
        self.account.last_error = ""
        self.account.save(update_fields=["access_token_ciphertext", "refresh_token_ciphertext", "token_expires_at", "last_synced_at", "last_error", "updated_at"])
        return access_token

    def _token_expired(self) -> bool:
        if self.account.token_expires_at is None:
            return False
        return self.account.token_expires_at <= django_timezone.now() + timedelta(minutes=2)

    def _request(self, method: str, url: str, *, params: dict | None = None, json: dict | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self._access_token()}"}
        response = request_with_retries(method, url, headers=headers, params=params, json=json)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text.strip() if exc.response is not None else str(exc)
            raise GoogleApiError(detail or "Google API request failed.") from exc
        if not response.content:
            return {}
        try:
            return dict(response.json())
        except ValueError:
            text = response.text.strip()
            if not text:
                return {}
            raise GoogleApiError(text) from None

    def _request_bytes(self, method: str, url: str, *, params: dict | None = None) -> bytes:
        headers = {"Authorization": f"Bearer {self._access_token()}"}
        response = request_with_retries(method, url, headers=headers, params=params)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text.strip() if exc.response is not None else str(exc)
            raise GoogleApiError(detail or "Google API request failed.") from exc
        return bytes(response.content or b"")

    def list_gmail_messages(
        self,
        *,
        query: str = "",
        label_ids: list[str] | None = None,
        max_results: int = 20,
        page_token: str = "",
    ) -> dict:
        params: dict[str, object] = {"maxResults": max(1, min(int(max_results or 20), 100))}
        if query.strip():
            params["q"] = query.strip()
        if label_ids:
            params["labelIds"] = label_ids
        if page_token.strip():
            params["pageToken"] = page_token.strip()
        return self._request("GET", "https://gmail.googleapis.com/gmail/v1/users/me/messages", params=params)

    def get_gmail_message(
        self,
        message_id: str,
        *,
        format: str = "metadata",
        metadata_headers: list[str] | None = None,
    ) -> dict:
        params: dict[str, object] = {"format": str(format or "metadata").strip() or "metadata"}
        if params["format"] == "metadata":
            headers = [str(header).strip() for header in (metadata_headers or ["Subject", "From", "Date"]) if str(header).strip()]
            if headers:
                params["metadataHeaders"] = headers
        return self._request(
            "GET",
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
            params=params,
        )

    def get_gmail_attachment(self, message_id: str, attachment_id: str) -> dict:
        return self._request(
            "GET",
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}/attachments/{attachment_id}",
        )

    def create_gmail_draft(
        self,
        *,
        to: list[str],
        subject: str = "",
        body: str = "",
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        thread_id: str = "",
    ) -> dict:
        payload = {"message": self._build_gmail_message_payload(to=to, subject=subject, body=body, cc=cc, bcc=bcc, thread_id=thread_id)}
        return self._request("POST", "https://gmail.googleapis.com/gmail/v1/users/me/drafts", json=payload)

    def send_gmail_message(
        self,
        *,
        to: list[str],
        subject: str = "",
        body: str = "",
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        thread_id: str = "",
    ) -> dict:
        payload = self._build_gmail_message_payload(to=to, subject=subject, body=body, cc=cc, bcc=bcc, thread_id=thread_id)
        return self._request("POST", "https://gmail.googleapis.com/gmail/v1/users/me/messages/send", json=payload)

    def send_gmail_draft(self, draft_id: str) -> dict:
        return self._request(
            "POST",
            f"https://gmail.googleapis.com/gmail/v1/users/me/drafts/{draft_id}/send",
            json={},
        )

    def trash_gmail_message(self, message_id: str) -> dict:
        return self._request(
            "POST",
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}/trash",
            json={},
        )

    def delete_gmail_message(self, message_id: str) -> dict:
        return self._request(
            "DELETE",
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
            json={},
        )

    def list_gmail_filters(self) -> dict:
        return self._request("GET", "https://gmail.googleapis.com/gmail/v1/users/me/settings/filters")

    def get_gmail_filter(self, filter_id: str) -> dict:
        return self._request(
            "GET",
            f"https://gmail.googleapis.com/gmail/v1/users/me/settings/filters/{filter_id}",
        )

    def create_gmail_filter(self, *, criteria: dict | None = None, action: dict | None = None) -> dict:
        payload: dict[str, object] = {}
        criteria_payload = self._normalize_gmail_filter_payload(criteria)
        action_payload = self._normalize_gmail_filter_payload(action)
        if criteria_payload:
            payload["criteria"] = criteria_payload
        if action_payload:
            payload["action"] = action_payload
        return self._request("POST", "https://gmail.googleapis.com/gmail/v1/users/me/settings/filters", json=payload)

    def delete_gmail_filter(self, filter_id: str) -> dict:
        return self._request(
            "DELETE",
            f"https://gmail.googleapis.com/gmail/v1/users/me/settings/filters/{filter_id}",
        )

    def list_people_connections(
        self,
        *,
        person_fields: str,
        page_size: int = 100,
        page_token: str = "",
        sort_order: str = "",
        request_sync_token: bool = False,
        sync_token: str = "",
        sources: list[str] | None = None,
    ) -> dict:
        params: dict[str, object] = {
            "personFields": str(person_fields or "").strip(),
            "pageSize": max(1, min(int(page_size or 100), 1000)),
        }
        if page_token.strip():
            params["pageToken"] = page_token.strip()
        if sort_order.strip():
            params["sortOrder"] = sort_order.strip()
        if request_sync_token:
            params["requestSyncToken"] = "true"
        if sync_token.strip():
            params["syncToken"] = sync_token.strip()
        if sources:
            params["sources"] = [str(source).strip() for source in sources if str(source).strip()]
        return self._request("GET", "https://people.googleapis.com/v1/people/me/connections", params=params)

    def search_people_contacts(
        self,
        *,
        query: str,
        read_mask: str,
        page_size: int = 10,
        page_token: str = "",
        sources: list[str] | None = None,
    ) -> dict:
        params: dict[str, object] = {
            "query": str(query or "").strip(),
            "readMask": str(read_mask or "").strip(),
            "pageSize": max(1, min(int(page_size or 10), 30)),
        }
        if page_token.strip():
            params["pageToken"] = page_token.strip()
        if sources:
            params["sources"] = [str(source).strip() for source in sources if str(source).strip()]
        return self._request("GET", "https://people.googleapis.com/v1/people:searchContacts", params=params)

    def get_people(self, resource_name: str, *, person_fields: str, sources: list[str] | None = None) -> dict:
        params: dict[str, object] = {
            "personFields": str(person_fields or "").strip(),
        }
        if sources:
            params["sources"] = [str(source).strip() for source in sources if str(source).strip()]
        resource_path = quote(str(resource_name or "").strip(), safe="/")
        if not resource_path:
            raise GoogleApiError("People get requires a resource name.")
        return self._request(
            "GET",
            f"https://people.googleapis.com/v1/{resource_path}",
            params=params,
        )

    def create_people_contact(self, *, person: dict, person_fields: str) -> dict:
        person_fields_value = str(person_fields or "").strip()
        if not person_fields_value:
            raise GoogleApiError("People create requires person_fields.")
        if not isinstance(person, dict):
            raise GoogleApiError("People create requires a person object.")
        params: dict[str, object] = {
            "personFields": person_fields_value,
        }
        payload = dict(person)
        payload.pop("resourceName", None)
        payload.pop("resource_name", None)
        if not payload:
            raise GoogleApiError("People create requires a person payload.")
        return self._request(
            "POST",
            "https://people.googleapis.com/v1/people:createContact",
            params=params,
            json=payload,
        )

    def update_people_contact(
        self,
        resource_name: str,
        *,
        person: dict,
        person_fields: str,
        update_person_fields: str,
    ) -> dict:
        person_fields_value = str(person_fields or "").strip()
        update_person_fields_value = str(update_person_fields or "").strip()
        if not person_fields_value:
            raise GoogleApiError("People update requires person_fields.")
        if not update_person_fields_value:
            raise GoogleApiError("People update requires update_person_fields.")
        if not isinstance(person, dict):
            raise GoogleApiError("People update requires a person object.")
        params: dict[str, object] = {
            "personFields": person_fields_value,
            "updatePersonFields": update_person_fields_value,
        }
        payload = dict(person)
        payload.pop("resource_name", None)
        resource_path = quote(str(resource_name or "").strip(), safe="/")
        if not resource_path:
            raise GoogleApiError("People update requires a resource name.")
        if not payload:
            raise GoogleApiError("People update requires a person payload.")
        payload["resourceName"] = str(resource_name or "").strip()
        return self._request(
            "PATCH",
            f"https://people.googleapis.com/v1/{resource_path}:updateContact",
            params=params,
            json=payload,
        )

    def delete_people_contact(self, resource_name: str) -> dict:
        resource_path = quote(str(resource_name or "").strip(), safe="/")
        if not resource_path:
            raise GoogleApiError("People delete requires a resource name.")
        return self._request(
            "DELETE",
            f"https://people.googleapis.com/v1/{resource_path}:deleteContact",
        )

    def list_drive_files(
        self,
        *,
        q: str = "",
        page_size: int = 20,
        page_token: str = "",
        include_all_drives: bool = True,
    ) -> dict:
        params: dict[str, object] = {
            "pageSize": max(1, min(int(page_size or 20), 1000)),
            "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,createdTime,webViewLink,webContentLink,size,parents)",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true" if include_all_drives else "false",
            "corpora": "allDrives" if include_all_drives else "user",
        }
        if q.strip():
            params["q"] = q.strip()
        if page_token.strip():
            params["pageToken"] = page_token.strip()
        return self._request("GET", "https://www.googleapis.com/drive/v3/files", params=params)

    def get_drive_file(self, file_id: str, *, fields: str = "") -> dict:
        params: dict[str, object] = {"supportsAllDrives": "true"}
        if fields.strip():
            params["fields"] = fields.strip()
        return self._request(
            "GET",
            f"https://www.googleapis.com/drive/v3/files/{file_id}",
            params=params,
        )

    def download_drive_file(self, file_id: str) -> bytes:
        return self._request_bytes(
            "GET",
            f"https://www.googleapis.com/drive/v3/files/{file_id}",
            params={"alt": "media", "supportsAllDrives": "true"},
        )

    def export_drive_file(self, file_id: str, mime_type: str) -> bytes:
        export_mime_type = str(mime_type or "").strip()
        if not export_mime_type:
            raise GoogleApiError("Drive export requires a mime type.")
        return self._request_bytes(
            "GET",
            f"https://www.googleapis.com/drive/v3/files/{file_id}/export",
            params={"mimeType": export_mime_type},
        )

    def get_document(self, document_id: str) -> dict:
        return self._request(
            "GET",
            f"https://docs.googleapis.com/v1/documents/{document_id}",
        )

    def get_spreadsheet(self, spreadsheet_id: str) -> dict:
        return self._request(
            "GET",
            f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}",
        )

    def get_sheet_values(self, spreadsheet_id: str, *, range_name: str = "") -> dict:
        sheet_range = str(range_name or "").strip() or "Sheet1"
        encoded_range = quote(sheet_range, safe="!:$,")
        return self._request(
            "GET",
            f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{encoded_range}",
        )

    def _normalize_gmail_filter_payload(self, value: dict | None) -> dict[str, object]:
        if not value:
            return {}
        if not isinstance(value, dict):
            raise GoogleApiError("Gmail filter criteria and action must be JSON objects.")
        payload: dict[str, object] = {}
        for key, raw_value in value.items():
            normalized_key = str(key or "").strip()
            if not normalized_key:
                continue
            if normalized_key in {"addLabelIds", "removeLabelIds"}:
                if isinstance(raw_value, str):
                    items = [raw_value.strip()] if raw_value.strip() else []
                else:
                    try:
                        iterable = list(raw_value or [])
                    except TypeError:
                        iterable = [raw_value]
                    items = [str(item).strip() for item in iterable if str(item).strip()]
                if items:
                    payload[normalized_key] = items
                continue
            if normalized_key in {"hasAttachment", "excludeChats"}:
                if isinstance(raw_value, bool):
                    payload[normalized_key] = raw_value
                else:
                    payload[normalized_key] = str(raw_value).strip().lower() in {"1", "true", "yes", "on"}
                continue
            if normalized_key == "size":
                try:
                    payload[normalized_key] = int(raw_value)
                except Exception:
                    continue
                continue
            text = str(raw_value or "").strip()
            if text:
                payload[normalized_key] = text
        return payload

    def list_calendar_events(
        self,
        *,
        calendar_id: str = "primary",
        q: str = "",
        time_min: str = "",
        time_max: str = "",
        max_results: int = 20,
    ) -> dict:
        params: dict[str, object] = {
            "maxResults": max(1, min(int(max_results or 20), 100)),
            "singleEvents": "true",
            "orderBy": "startTime",
        }
        if q.strip():
            params["q"] = q.strip()
        if time_min.strip():
            params["timeMin"] = time_min.strip()
        if time_max.strip():
            params["timeMax"] = time_max.strip()
        return self._request("GET", f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events", params=params)

    def list_calendar_list(self) -> dict:
        return self._request("GET", "https://www.googleapis.com/calendar/v3/users/me/calendarList")

    def get_calendar_event(self, *, calendar_id: str = "primary", event_id: str) -> dict:
        return self._request(
            "GET",
            f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/{event_id}",
        )

    def create_calendar_event(
        self,
        *,
        calendar_id: str = "primary",
        summary: str = "",
        description: str = "",
        location: str = "",
        start: dict | None = None,
        end: dict | None = None,
        attendees: list[str] | None = None,
        send_updates: str = "",
    ) -> dict:
        params: dict[str, object] = {}
        if send_updates.strip():
            params["sendUpdates"] = send_updates.strip()
        payload = self._build_calendar_event_payload(
            summary=summary,
            description=description,
            location=location,
            start=start or {},
            end=end or {},
            attendees=attendees,
        )
        return self._request(
            "POST",
            f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events",
            params=params or None,
            json=payload,
        )

    def update_calendar_event(
        self,
        *,
        calendar_id: str = "primary",
        event_id: str,
        summary: str = "",
        description: str = "",
        location: str = "",
        start: dict | None = None,
        end: dict | None = None,
        attendees: list[str] | None = None,
        send_updates: str = "",
    ) -> dict:
        params: dict[str, object] = {}
        if send_updates.strip():
            params["sendUpdates"] = send_updates.strip()
        payload = self._build_calendar_event_payload(
            summary=summary,
            description=description,
            location=location,
            start=start or {},
            end=end or {},
            attendees=attendees,
        )
        return self._request(
            "PATCH",
            f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/{event_id}",
            params=params or None,
            json=payload,
        )

    def delete_calendar_event(
        self,
        *,
        calendar_id: str = "primary",
        event_id: str,
        send_updates: str = "",
    ) -> dict:
        params: dict[str, object] = {}
        if send_updates.strip():
            params["sendUpdates"] = send_updates.strip()
        return self._request(
            "DELETE",
            f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/{event_id}",
            params=params or None,
        )

    def _build_gmail_message_payload(
        self,
        *,
        to: list[str],
        subject: str = "",
        body: str = "",
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        thread_id: str = "",
    ) -> dict[str, str]:
        message = EmailMessage()
        if to:
            message["To"] = ", ".join(str(item).strip() for item in to if str(item).strip())
        if cc:
            message["Cc"] = ", ".join(str(item).strip() for item in cc if str(item).strip())
        if bcc:
            message["Bcc"] = ", ".join(str(item).strip() for item in bcc if str(item).strip())
        if subject:
            message["Subject"] = subject.strip()
        message.set_content(body or "")
        raw = urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")
        payload: dict[str, str] = {"raw": raw}
        if thread_id:
            payload["threadId"] = thread_id.strip()
        return payload

    def _build_calendar_event_payload(
        self,
        *,
        summary: str = "",
        description: str = "",
        location: str = "",
        start: dict,
        end: dict,
        attendees: list[str] | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {}
        if summary.strip():
            payload["summary"] = summary.strip()
        if description.strip():
            payload["description"] = description.strip()
        if location.strip():
            payload["location"] = location.strip()
        if start:
            payload["start"] = dict(start)
        if end:
            payload["end"] = dict(end)
        attendee_items = [str(item).strip() for item in attendees or [] if str(item).strip()]
        if attendee_items:
            payload["attendees"] = [{"email": email} for email in attendee_items]
        return payload
