"""Bot API HTTP transport — for Tier 1 operations (no api_id/api_hash needed).

This is the lightweight HTTP client for when MTProto isn't available.
It can:
    - sendMessage / sendDocument / sendPhoto
    - forwardMessage (WITH forward header)
    - copyMessage (returns only new msg_id, no metadata)
    - editMessageCaption / editMessageText / editMessageMedia
    - deleteMessage / deleteMessages
    - getFile (download files < 20 MB)
    - getMe (bot validation)

It CANNOT:
    - Read channel history (no getMessage / getHistory)
    - Copy via file_id without header
    - Handle files > 50 MB upload / 20 MB download
    - Extract metadata from messages

All methods return parsed JSON dicts (the "result" field from Bot API response).
Raises BotAPIError on failure.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

# Bot API base URL
BOT_API_BASE = "https://api.telegram.org/bot{token}/{method}"

# Default timeout for Bot API calls
DEFAULT_TIMEOUT = 60


class BotAPIError(Exception):
    """Bot API returned an error."""
    def __init__(self, method: str, error_code: int, description: str, parameters: dict | None = None):
        self.method = method
        self.error_code = error_code
        self.description = description
        self.parameters = parameters or {}
        super().__init__(f"Bot API {method} failed: [{error_code}] {description}")


class FloodWaitBotAPIError(BotAPIError):
    """Bot API returned FloodWait (429)."""
    def __init__(self, method: str, retry_after: int):
        self.retry_after = retry_after
        super().__init__(method, 429, f"Too Many Requests (retry after {retry_after}s)",
                        {"retry_after": retry_after})


class BotAPIClient:
    """Lightweight HTTP client for Telegram Bot API.

    Thread-safe (uses requests.Session with connection pooling).
    No async — this is for simple Tier 1 operations.

    Usage:
        client = BotAPIClient("8866132706:AAH_xxx...")
        result = client.send_message(chat_id=-100..., text="Hello")
        client.forward_message(chat_id=-100..., from_chat_id="@yxafile", message_id=123)
    """

    def __init__(self, token: str, timeout: int = DEFAULT_TIMEOUT):
        self.token = token
        self.bot_id = int(token.split(":")[0]) if ":" in token else 0
        self.timeout = timeout
        self.session = requests.Session()
        self.request_count = 0
        self.floodwait_count = 0
        self._last_request_time = 0.0
        self._min_interval = 0.05  # 50ms = ~20 req/s max

    # ------------------------------------------------------------------
    # Low-level call
    # ------------------------------------------------------------------

    def _call(self, method: str, **params: Any) -> dict[str, Any]:
        """Call a Bot API method. Returns the 'result' field on success.

        Raises:
            BotAPIError: on API error
            FloodWaitBotAPIError: on 429 (caller should sleep and retry)
        """
        # Simple throttle (50ms between requests)
        now = time.perf_counter()
        wait = self._min_interval - (now - self._last_request_time)
        if wait > 0:
            time.sleep(wait)
        self._last_request_time = time.perf_counter()

        url = BOT_API_BASE.format(token=self.token, method=method)
        self.request_count += 1

        try:
            resp = self.session.post(url, json=params, timeout=self.timeout)
            data = resp.json()
        except requests.RequestException as e:
            raise BotAPIError(method, -1, f"Network error: {e}") from e
        except ValueError as e:
            raise BotAPIError(method, -1, f"Invalid JSON response: {e}") from e

        if not data.get("ok"):
            error_code = data.get("error_code", -1)
            description = data.get("description", "unknown error")
            parameters = data.get("parameters", {})

            if error_code == 429:
                retry_after = parameters.get("retry_after", 5)
                self.floodwait_count += 1
                raise FloodWaitBotAPIError(method, retry_after)

            raise BotAPIError(method, error_code, description, parameters)

        return data["result"]

    def _call_with_retry(self, method: str, retries: int = 3, **params: Any) -> dict[str, Any]:
        """Call a Bot API method with FloodWait retry."""
        for attempt in range(retries):
            try:
                return self._call(method, **params)
            except FloodWaitBotAPIError as e:
                if attempt < retries - 1:
                    logger.debug(f"Bot API {method} FloodWait {e.retry_after}s, sleeping...")
                    time.sleep(e.retry_after + 1)
                    continue
                raise
        raise RuntimeError("unreachable")

    def _upload_file(self, method: str, file_field: str, file_path: str,
                     data: dict[str, Any], retries: int = 3) -> dict[str, Any]:
        """Upload a file via Bot API (sendDocument/sendPhoto/etc)."""
        url = BOT_API_BASE.format(token=self.token, method=method)
        self.request_count += 1

        for attempt in range(retries):
            try:
                with open(file_path, "rb") as f:
                    files = {file_field: f}
                    resp = self.session.post(url, data=data, files=files, timeout=self.timeout)
                data_resp = resp.json()
            except requests.RequestException as e:
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise BotAPIError(method, -1, f"Network error: {e}") from e

            if not data_resp.get("ok"):
                error_code = data_resp.get("error_code", -1)
                description = data_resp.get("description", "unknown error")
                parameters = data_resp.get("parameters", {})

                if error_code == 429:
                    retry_after = parameters.get("retry_after", 5)
                    self.floodwait_count += 1
                    if attempt < retries - 1:
                        time.sleep(retry_after + 1)
                        continue
                    raise FloodWaitBotAPIError(method, retry_after)

                raise BotAPIError(method, error_code, description, parameters)

            return data_resp["result"]

        raise RuntimeError("unreachable")

    # ------------------------------------------------------------------
    # High-level methods
    # ------------------------------------------------------------------

    def get_me(self) -> dict[str, Any]:
        """Validate the bot token and get bot info."""
        return self._call_with_retry("getMe")

    def send_message(self, chat_id: int | str, text: str,
                     parse_mode: str = "HTML",
                     reply_to_message_id: int | None = None,
                     disable_notification: bool = True) -> dict[str, Any]:
        """Send a text message."""
        params = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_notification": disable_notification,
        }
        if reply_to_message_id:
            params["reply_to_message_id"] = reply_to_message_id
        return self._call_with_retry("sendMessage", **params)

    def send_document(self, chat_id: int | str, document_path: str,
                      caption: str = "",
                      parse_mode: str = "HTML",
                      reply_to_message_id: int | None = None,
                      disable_notification: bool = True) -> dict[str, Any]:
        """Upload a local file as a document."""
        data = {
            "chat_id": chat_id,
            "caption": caption[:1024],  # caption limit
            "parse_mode": parse_mode,
            "disable_notification": disable_notification,
        }
        if reply_to_message_id:
            data["reply_to_message_id"] = reply_to_message_id
        return self._upload_file("sendDocument", "document", document_path, data)

    def send_photo(self, chat_id: int | str, photo_path: str,
                   caption: str = "",
                   parse_mode: str = "HTML",
                   reply_to_message_id: int | None = None,
                   disable_notification: bool = True) -> dict[str, Any]:
        """Upload a local file as a photo."""
        data = {
            "chat_id": chat_id,
            "caption": caption[:1024],
            "parse_mode": parse_mode,
            "disable_notification": disable_notification,
        }
        if reply_to_message_id:
            data["reply_to_message_id"] = reply_to_message_id
        return self._upload_file("sendPhoto", "photo", photo_path, data)

    def forward_message(self, chat_id: int | str, from_chat_id: int | str,
                        message_id: int,
                        disable_notification: bool = True) -> dict[str, Any]:
        """Forward a message (WITH 'Forwarded from' header)."""
        return self._call_with_retry("forwardMessage",
            chat_id=chat_id,
            from_chat_id=from_chat_id,
            message_id=message_id,
            disable_notification=disable_notification,
        )

    def copy_message(self, chat_id: int | str, from_chat_id: int | str,
                     message_id: int,
                     caption: str | None = None,
                     parse_mode: str = "HTML",
                     disable_notification: bool = True) -> int:
        """Copy a message (no forward header). Returns new message_id.

        Note: copyMessage returns only MessageId, NOT the full message.
        Cannot extract filename/size/file_id from the result.
        """
        params = {
            "chat_id": chat_id,
            "from_chat_id": from_chat_id,
            "message_id": message_id,
            "disable_notification": disable_notification,
        }
        if caption is not None:
            params["caption"] = caption[:1024]
            params["parse_mode"] = parse_mode
        result = self._call_with_retry("copyMessage", **params)
        return result.get("message_id", 0)

    def edit_message_text(self, chat_id: int | str, message_id: int,
                          text: str, parse_mode: str = "HTML") -> dict[str, Any]:
        """Edit a text message."""
        return self._call_with_retry("editMessageText",
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode=parse_mode,
        )

    def edit_message_caption(self, chat_id: int | str, message_id: int,
                             caption: str, parse_mode: str = "HTML") -> dict[str, Any]:
        """Edit a message's caption."""
        return self._call_with_retry("editMessageCaption",
            chat_id=chat_id,
            message_id=message_id,
            caption=caption[:1024],
            parse_mode=parse_mode,
        )

    def edit_message_media(self, chat_id: int | str, message_id: int,
                           media_path: str, media_type: str = "document") -> dict[str, Any]:
        """Replace a message's media in-place (preserves message_id).

        This is the KEY method for atomic DB sync — replaces the DB file
        without changing the message_id, so all references stay valid.

        media_type: 'document' | 'photo' | 'video' | 'animation' | 'audio'
        """
        # editMessageMedia uses multipart with media as JSON + attach://
        url = BOT_API_BASE.format(token=self.token, method="editMessageMedia")
        self.request_count += 1

        import json as json_mod
        media_json = json_mod.dumps({
            "type": media_type,
            "media": f"attach://{media_type}",
        })

        data = {
            "chat_id": str(chat_id),
            "message_id": str(message_id),
            "media": media_json,
        }

        try:
            with open(media_path, "rb") as f:
                files = {media_type: f}
                resp = self.session.post(url, data=data, files=files, timeout=self.timeout)
            result = resp.json()
        except requests.RequestException as e:
            raise BotAPIError("editMessageMedia", -1, f"Network error: {e}") from e

        if not result.get("ok"):
            raise BotAPIError("editMessageMedia",
                            result.get("error_code", -1),
                            result.get("description", "unknown error"))
        return result["result"]

    def delete_message(self, chat_id: int | str, message_id: int) -> bool:
        """Delete a single message."""
        self._call_with_retry("deleteMessage",
            chat_id=chat_id,
            message_id=message_id,
        )
        return True

    def delete_messages(self, chat_id: int | str, message_ids: list[int]) -> bool:
        """Delete multiple messages (up to 100 at a time)."""
        for i in range(0, len(message_ids), 100):
            chunk = message_ids[i:i+100]
            self._call_with_retry("deleteMessages",
                chat_id=chat_id,
                message_ids=chunk,
            )
        return True

    def get_file(self, file_id: str) -> dict[str, Any]:
        """Get file info (for downloading files < 20 MB)."""
        return self._call_with_retry("getFile", file_id=file_id)

    def download_file(self, file_id: str, output_path: str) -> str:
        """Download a file via Bot API (only works for files < 20 MB).

        Returns the output path.
        """
        file_info = self.get_file(file_id)
        file_path = file_info.get("file_path", "")
        if not file_path:
            raise BotAPIError("getFile", -1, "No file_path in response")

        url = f"https://api.telegram.org/file/bot{self.token}/{file_path}"
        resp = self.session.get(url, timeout=self.timeout, stream=True)
        resp.raise_for_status()

        with open(output_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        return output_path

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats_summary(self) -> str:
        return f"BotAPI(bot_id={self.bot_id}, reqs={self.request_count}, floodwaits={self.floodwait_count})"

    def close(self) -> None:
        """Close the HTTP session."""
        self.session.close()
