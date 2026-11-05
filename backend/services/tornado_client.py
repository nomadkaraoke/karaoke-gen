"""
TornadoAPI client: downloads YouTube audio through a hosted media API.

Tornado doesn't depend on our cookies or IP, unlike flacfetch, so YouTubeDownloadService tries it
first. Docs: https://docs.tornadoapi.io

Three things that are easy to get wrong:

- Only ever send a canonical ``watch?v=<id>`` URL. A URL carrying a real playlist id downloads and
  bills the whole playlist, returning ``batch_id`` rather than ``job_id``.
- Request ``format: "webm", max_resolution: "360"`` and strip the video, because that returns the source's
  Opus stream, packet-identical to what flacfetch delivers. ``audio_only`` isn't reliable for this: on our
  plan the same request returned Opus or 128k AAC from one day to the next (checked 2026-11-03/05). It
  costs no more: audio-only and sub-720p video both bill the same full-source 720p reference.
- Delivered files are only kept for 24 hours and count toward a storage limit, so copy them
  straight away and delete them. The delivery URL is signed: never send it the API key or log it.
"""
import asyncio
import logging
import os
import random
import re
import subprocess
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional
from urllib.parse import urlsplit

import httpx

from backend.config import get_settings

logger = logging.getLogger(__name__)


class _NoSignedUrls(logging.Filter):
    """httpx logs every request's full URL at INFO, which would put delivery signatures in our logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return "X-Amz-Signature" not in message and "verify=" not in message


logging.getLogger("httpx").addFilter(_NoSignedUrls())

BASE_URL = "https://api.tornadoapi.io"

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_NONTERMINAL = {"Pending", "Processing"}
_TERMINAL = {"Completed", "Failed", "Warning", "Skipped", "Cancelled", "CancelledByAdmin"}

_CREATE_ATTEMPTS = 3
_MAX_RETRY_AFTER_SECONDS = 30.0
_MAX_FILE_BYTES = 2 * 1024 * 1024 * 1024  # includes a 360p video track; anything this big isn't a song
_DEMUX_TIMEOUT_SECONDS = 120
# Delivered container -> (extension for the audio we keep, ffmpeg muxer). Audio is never re-encoded.
_AUDIO_CONTAINERS = {
    ".webm": (".webm", "webm"),
    ".mkv": (".webm", "webm"),
    ".opus": (".ogg", "ogg"),  # .ogg: gen's upload and review allow-lists reject .opus
    ".ogg": (".ogg", "ogg"),
    ".mp4": (".m4a", "ipod"),
    ".m4a": (".m4a", "ipod"),
}


class TornadoError(Exception):
    """A download through Tornado failed. The caller can try another route."""


class TornadoAccountError(TornadoError):
    """The key or account was rejected, so every download will fail until a person fixes it."""


@dataclass
class TornadoDownload:
    tornado_job_id: str
    extension: str
    file_size: Optional[int]
    billable_bytes: Optional[int]


class TornadoClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BASE_URL,
        deadline_seconds: float = 180.0,
        poll_interval_seconds: float = 3.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        demux: Optional[Callable[[str, str, str], None]] = None,
        clock: Callable[[], float] = lambda: asyncio.get_running_loop().time(),
    ):
        self._api_key = api_key
        self._base_url = base_url
        self._deadline_seconds = deadline_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._transport = transport
        self._sleep = sleep
        self._demux = demux or _demux_audio
        self._clock = clock

    def _http(self, timeout: float = 30.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self._transport)

    def _headers(self) -> Dict[str, str]:
        return {"x-api-key": self._api_key, "Content-Type": "application/json"}

    @staticmethod
    def _watch_url(video_id: str) -> str:
        if not _VIDEO_ID.match(video_id or ""):
            raise TornadoError(f"Not a YouTube video id: {video_id!r}")
        return f"https://www.youtube.com/watch?v={video_id}"

    async def get_metadata(self, video_id: str) -> Dict[str, Any]:
        """Video metadata, without downloading. Raises TornadoError if the lookup fails."""
        url = self._watch_url(video_id)
        try:
            async with self._http(timeout=15.0) as client:
                resp = await client.post(
                    f"{self._base_url}/metadata", headers=self._headers(), json={"url": url}
                )
        except httpx.HTTPError as e:
            raise TornadoError(f"Metadata request failed: {type(e).__name__}") from e
        self._raise_for_account(resp)
        if resp.status_code != 200:
            raise TornadoError(f"Metadata lookup returned {resp.status_code}")
        data = self._json_object(resp)
        if data is None:
            raise TornadoError("Metadata response was not a JSON object")
        return data

    async def download_audio(self, video_id: str, dest_path: str, submission: str) -> TornadoDownload:
        """Download a video's audio to ``dest_path``.

        ``submission`` identifies this logical request (our job id). Raises TornadoError on any
        failure, including a job that doesn't finish before the deadline.
        """
        url = self._watch_url(video_id)
        # Unique per call, stable across this call's retries: a POST that timed out may have created
        # a job, and the key lets the retry get that job back instead of paying for a second one.
        idempotency_key = f"nomad-{submission}-{video_id}-{uuid.uuid4().hex}"
        job_id = await self._create_job(url, idempotency_key)
        logger.info(f"Tornado job {job_id} created for video_id={video_id}, submission={submission}")

        job = await self._wait_for_job(job_id)
        delivery_url = job.get("s3_url")
        if not delivery_url:
            raise TornadoError(f"Tornado job {job_id} completed without a delivery URL")

        extension, muxer = self._audio_container(delivery_url)
        source_path = f"{dest_path}.source"
        try:
            await self._fetch_file(delivery_url, source_path)
            await asyncio.to_thread(self._demux, source_path, dest_path, muxer)
        finally:
            self._remove_partial(source_path)
        await self._delete_file(job_id)
        return TornadoDownload(
            tornado_job_id=job_id,
            extension=extension,
            file_size=job.get("file_size"),
            billable_bytes=job.get("billable_bytes"),
        )

    @staticmethod
    def _json_object(resp: httpx.Response) -> Optional[Dict[str, Any]]:
        try:
            data = resp.json()
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _raise_for_account(resp: httpx.Response) -> None:
        if resp.status_code in (401, 402, 403):
            raise TornadoAccountError(f"Tornado rejected the account: HTTP {resp.status_code}")

    async def _create_job(self, url: str, idempotency_key: str) -> str:
        body = {"url": url, "format": "webm", "max_resolution": "360"}
        headers = {**self._headers(), "x-idempotency-key": idempotency_key}
        last_problem = "no attempt made"
        retry_after: Optional[str] = None
        for attempt in range(_CREATE_ATTEMPTS):
            if attempt:
                await self._sleep(self._retry_delay(attempt, retry_after))
            retry_after = None
            try:
                async with self._http() as client:
                    resp = await client.post(f"{self._base_url}/jobs", headers=headers, json=body)
            except httpx.HTTPError as e:
                last_problem = type(e).__name__
                continue

            self._raise_for_account(resp)
            if resp.status_code == 429 or resp.status_code >= 500:
                last_problem = f"HTTP {resp.status_code}"
                retry_after = resp.headers.get("Retry-After")
                continue
            if resp.status_code >= 400:
                raise TornadoError(f"Tornado rejected the job: HTTP {resp.status_code}")

            data = self._json_object(resp)
            if data is None:
                raise TornadoError("Job response was not a JSON object")
            if data.get("batch_id"):
                # Should be impossible with a canonical URL, and it would bill a whole playlist.
                logger.error(f"Tornado created batch {data['batch_id']} for {url}; cancel it in the dashboard")
                raise TornadoError("Tornado created a batch for a single video")
            job_id = data.get("job_id")
            if not job_id:
                raise TornadoError("Job response had no job_id")
            return job_id
        raise TornadoError(f"Could not create a Tornado job: {last_problem}")

    @staticmethod
    def _retry_delay(attempt: int, retry_after: Optional[str]) -> float:
        try:
            if retry_after is not None:
                return min(max(float(retry_after), 0.0), _MAX_RETRY_AFTER_SECONDS)
        except ValueError:
            pass
        return float(2**attempt) + random.uniform(0, 1)

    async def _wait_for_job(self, job_id: str) -> Dict[str, Any]:
        deadline = self._clock() + self._deadline_seconds
        last_status = "unknown"
        while self._clock() < deadline:
            status_code, job = await self._get_job(job_id)
            if job is not None:
                last_status = str(job.get("status"))
                if last_status == "Completed":
                    return job
                if last_status in _TERMINAL:
                    raise TornadoError(
                        f"Tornado job {job_id} ended {last_status} "
                        f"({job.get('error_type')}): {job.get('error')}"
                    )
                if last_status not in _NONTERMINAL:
                    logger.warning(f"Tornado job {job_id} has an unknown status: {last_status}")
            elif status_code is not None:
                last_status = f"HTTP {status_code}"
            await self._sleep(self._poll_interval_seconds)

        await self._cancel(job_id)
        raise TornadoError(f"Tornado job {job_id} timed out after {self._deadline_seconds:.0f}s ({last_status})")

    async def _get_job(self, job_id: str) -> "tuple[Optional[int], Optional[Dict[str, Any]]]":
        """One status read. A failed read returns no job, so the loop tries again until the deadline."""
        try:
            async with self._http() as client:
                resp = await client.get(f"{self._base_url}/jobs/{job_id}", headers=self._headers())
        except httpx.HTTPError:
            return None, None
        self._raise_for_account(resp)
        if resp.status_code != 200:
            return resp.status_code, None
        return resp.status_code, self._json_object(resp)

    async def _fetch_file(self, delivery_url: str, dest_path: str) -> None:
        """Stream the delivered file to disk. Never sends the API key; a partial file is removed."""
        written = 0
        try:
            async with httpx.AsyncClient(timeout=120.0, transport=self._transport, follow_redirects=True) as client:
                async with client.stream("GET", delivery_url) as resp:
                    if resp.status_code != 200:
                        raise TornadoError(f"Delivery URL returned {resp.status_code}")
                    with open(dest_path, "wb") as f:
                        async for chunk in resp.aiter_bytes():
                            written += len(chunk)
                            if written > _MAX_FILE_BYTES:
                                raise TornadoError("Delivered file is larger than expected")
                            f.write(chunk)
            if not written:
                raise TornadoError("Delivered file was empty")
        except httpx.HTTPError as e:
            self._remove_partial(dest_path)
            raise TornadoError(f"Fetching the delivered file failed: {type(e).__name__}") from e
        except OSError as e:
            self._remove_partial(dest_path)
            raise TornadoError(f"Writing the delivered file failed: {type(e).__name__}") from e
        except TornadoError:
            self._remove_partial(dest_path)
            raise

    @staticmethod
    def _remove_partial(path: str) -> None:
        try:
            os.remove(path)
        except OSError:
            pass

    async def _delete_file(self, job_id: str) -> None:
        """Best effort: it expires in 24 hours anyway."""
        try:
            async with self._http() as client:
                resp = await client.delete(f"{self._base_url}/jobs/{job_id}/file", headers=self._headers())
            if resp.status_code != 200:
                logger.info(f"Could not delete Tornado file for job {job_id}: HTTP {resp.status_code}")
        except httpx.HTTPError as e:
            logger.info(f"Could not delete Tornado file for job {job_id}: {type(e).__name__}")

    async def _cancel(self, job_id: str) -> None:
        """Best effort: stop paying for a job we've given up on."""
        try:
            async with self._http() as client:
                await client.post(f"{self._base_url}/jobs/{job_id}/cancel", headers=self._headers())
        except httpx.HTTPError as e:
            logger.info(f"Could not cancel Tornado job {job_id}: {type(e).__name__}")

    @staticmethod
    def _audio_container(delivery_url: str) -> "tuple[str, str]":
        ext = os.path.splitext(urlsplit(delivery_url).path)[1].lower()
        return _AUDIO_CONTAINERS.get(ext, _AUDIO_CONTAINERS[".webm"])


def _demux_audio(source_path: str, dest_path: str, muxer: str) -> None:
    """Keep only the audio track, without re-encoding it."""
    try:
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", source_path,
             "-map", "0:a:0", "-c:a", "copy", "-f", muxer, dest_path],
            check=True, capture_output=True, text=True, timeout=_DEMUX_TIMEOUT_SECONDS,
        )
        if not os.path.getsize(dest_path):
            raise TornadoError("ffmpeg produced an empty audio file")
    except (OSError, subprocess.SubprocessError) as e:
        try:
            os.remove(dest_path)
        except OSError:
            pass
        stderr = getattr(e, "stderr", None) or ""
        raise TornadoError(f"Could not extract the audio track: {type(e).__name__} {stderr[:200]}") from e


_client: Optional[TornadoClient] = None


def get_tornado_client() -> Optional[TornadoClient]:
    """The shared client, or None when Tornado isn't configured or is switched off."""
    global _client
    settings = get_settings()
    if not settings.tornado_api_key or settings.youtube_download_provider != "tornado":
        return None
    if _client is None:
        _client = TornadoClient(settings.tornado_api_key)
    return _client


def reset_tornado_client() -> None:
    global _client
    _client = None
