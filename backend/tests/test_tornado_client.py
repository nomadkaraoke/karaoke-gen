"""Tests for TornadoClient. Network is mocked; nothing here submits real work."""
import json
import logging
import shutil
import subprocess

import httpx
import pytest

from backend.services.tornado_client import (
    _demux_audio,
    TornadoAccountError,
    TornadoClient,
    TornadoError,
)

VIDEO_ID = "dQw4w9WgXcQ"
DELIVERY_URL = "https://files.tornadoapi.io/videos/abc.webm?verify=signed"


class FakeTornado:
    """Scripted Tornado API. Each list is consumed in order; the last entry repeats."""

    def __init__(self, create=None, status=None, delivery=None, metadata=None):
        self.create = create or [httpx.Response(201, json={"job_id": "job-1"})]
        self.status = status or [
            httpx.Response(200, json={"status": "Completed", "s3_url": DELIVERY_URL, "file_size": 5, "billable_bytes": 9})
        ]
        self.delivery = delivery or httpx.Response(200, content=b"audio")
        self.metadata = metadata or httpx.Response(200, json={"title": "A Song"})
        self.requests = []

    @staticmethod
    def _next(items):
        item = items.pop(0) if len(items) > 1 else items[0]
        if isinstance(item, Exception):
            raise item
        return item

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path, method = request.url.path, request.method
        if request.url.host == "files.tornadoapi.io":
            return self.delivery
        if method == "POST" and path == "/jobs":
            return self._next(self.create)
        if method == "POST" and path == "/metadata":
            return self.metadata
        if method == "GET" and path.startswith("/jobs/"):
            return self._next(self.status)
        if method == "DELETE" or path.endswith("/cancel"):
            return httpx.Response(200, json={})
        raise AssertionError(f"Unexpected request: {method} {request.url}")

    def of(self, method, suffix):
        return [r for r in self.requests if r.method == method and r.url.path.endswith(suffix)]


def make_client(fake, deadline=30.0):
    now = {"t": 0.0}

    async def sleep(seconds):
        now["t"] += seconds

    return TornadoClient(
        "test-key",
        deadline_seconds=deadline,
        poll_interval_seconds=3.0,
        transport=httpx.MockTransport(fake),
        sleep=sleep,
        demux=lambda source, dest, muxer: shutil.copy(source, dest),
        clock=lambda: now["t"],
    )


@pytest.mark.asyncio
async def test_download_writes_file_and_reports_size(tmp_path):
    fake = FakeTornado()
    dest = tmp_path / "audio"
    result = await make_client(fake).download_audio(VIDEO_ID, str(dest), submission="gen-1")
    assert dest.read_bytes() == b"audio"
    assert result.tornado_job_id == "job-1"
    assert result.extension == ".webm"
    assert (result.file_size, result.billable_bytes) == (5, 9)


@pytest.mark.asyncio
async def test_requests_the_route_that_returns_source_opus_for_the_canonical_url(tmp_path):
    fake = FakeTornado()
    await make_client(fake).download_audio(VIDEO_ID, str(tmp_path / "a"), submission="gen-1")
    body = json.loads(fake.of("POST", "/jobs")[0].content)
    # Not audio_only: that returned 128k AAC or Opus unpredictably (2026-11-05).
    assert body == {"url": f"https://www.youtube.com/watch?v={VIDEO_ID}", "format": "webm", "max_resolution": "360"}


@pytest.mark.asyncio
async def test_rejects_anything_but_a_video_id_so_a_playlist_url_cant_be_sent(tmp_path):
    fake = FakeTornado()
    with pytest.raises(TornadoError):
        await make_client(fake).download_audio(f"{VIDEO_ID}&list=PLabc", str(tmp_path / "a"), submission="gen-1")
    assert fake.requests == []


@pytest.mark.asyncio
async def test_delivery_url_never_receives_the_api_key(tmp_path):
    fake = FakeTornado()
    await make_client(fake).download_audio(VIDEO_ID, str(tmp_path / "a"), submission="gen-1")
    delivery = [r for r in fake.requests if r.url.host == "files.tornadoapi.io"]
    assert len(delivery) == 1
    assert "x-api-key" not in delivery[0].headers
    assert fake.of("POST", "/jobs")[0].headers["x-api-key"] == "test-key"


@pytest.mark.asyncio
async def test_deletes_the_file_after_fetching_it(tmp_path):
    fake = FakeTornado()
    await make_client(fake).download_audio(VIDEO_ID, str(tmp_path / "a"), submission="gen-1")
    assert len(fake.of("DELETE", "/jobs/job-1/file")) == 1


@pytest.mark.asyncio
async def test_failed_delete_doesnt_fail_the_download(tmp_path):
    fake = FakeTornado()
    original = fake.__call__

    def handler(request):
        if request.method == "DELETE":
            raise httpx.ConnectError("down")
        return original(request)

    client = make_client(fake)
    client._transport = httpx.MockTransport(handler)
    result = await client.download_audio(VIDEO_ID, str(tmp_path / "a"), submission="gen-1")
    assert result.tornado_job_id == "job-1"


@pytest.mark.asyncio
async def test_post_timeout_retries_with_the_same_idempotency_key(tmp_path):
    fake = FakeTornado(create=[httpx.ReadTimeout("slow"), httpx.Response(201, json={"job_id": "job-1"})])
    await make_client(fake).download_audio(VIDEO_ID, str(tmp_path / "a"), submission="gen-1")
    posts = fake.of("POST", "/jobs")
    assert len(posts) == 2
    assert posts[0].headers["x-idempotency-key"] == posts[1].headers["x-idempotency-key"]
    assert "gen-1" in posts[0].headers["x-idempotency-key"]


@pytest.mark.asyncio
async def test_each_download_call_gets_its_own_idempotency_key(tmp_path):
    fake = FakeTornado()
    client = make_client(fake)
    await client.download_audio(VIDEO_ID, str(tmp_path / "a"), submission="gen-1")
    await client.download_audio(VIDEO_ID, str(tmp_path / "b"), submission="gen-1")
    keys = {r.headers["x-idempotency-key"] for r in fake.of("POST", "/jobs")}
    assert len(keys) == 2


@pytest.mark.asyncio
async def test_429_honours_retry_after_then_succeeds(tmp_path):
    slept = []
    fake = FakeTornado(
        create=[httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(201, json={"job_id": "job-1"})]
    )
    client = make_client(fake)
    real_sleep = client._sleep

    async def sleep(seconds):
        slept.append(seconds)
        await real_sleep(seconds)

    client._sleep = sleep
    await client.download_audio(VIDEO_ID, str(tmp_path / "a"), submission="gen-1")
    assert slept[0] == 7.0


@pytest.mark.asyncio
async def test_gives_up_creating_after_repeated_5xx(tmp_path):
    fake = FakeTornado(create=[httpx.Response(503)])
    with pytest.raises(TornadoError, match="503"):
        await make_client(fake).download_audio(VIDEO_ID, str(tmp_path / "a"), submission="gen-1")
    assert len(fake.of("POST", "/jobs")) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [401, 402, 403])
async def test_account_errors_are_distinct_and_not_retried(tmp_path, code):
    fake = FakeTornado(create=[httpx.Response(code)])
    with pytest.raises(TornadoAccountError):
        await make_client(fake).download_audio(VIDEO_ID, str(tmp_path / "a"), submission="gen-1")
    assert len(fake.of("POST", "/jobs")) == 1


@pytest.mark.asyncio
async def test_validation_error_is_not_retried(tmp_path):
    fake = FakeTornado(create=[httpx.Response(422, json={"error": "bad"})])
    with pytest.raises(TornadoError):
        await make_client(fake).download_audio(VIDEO_ID, str(tmp_path / "a"), submission="gen-1")
    assert len(fake.of("POST", "/jobs")) == 1


@pytest.mark.asyncio
async def test_batch_response_fails_rather_than_being_treated_as_a_job(tmp_path):
    fake = FakeTornado(create=[httpx.Response(201, json={"batch_id": "b-1", "total_episodes": 40})])
    with pytest.raises(TornadoError, match="batch"):
        await make_client(fake).download_audio(VIDEO_ID, str(tmp_path / "a"), submission="gen-1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response", [httpx.Response(201, content=b"not json"), httpx.Response(201, json={}), httpx.Response(201, json=[1])]
)
async def test_malformed_create_response(tmp_path, response):
    fake = FakeTornado(create=[response])
    with pytest.raises(TornadoError):
        await make_client(fake).download_audio(VIDEO_ID, str(tmp_path / "a"), submission="gen-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["Failed", "Warning", "Skipped", "Cancelled", "CancelledByAdmin"])
async def test_terminal_statuses_other_than_completed_fail_with_the_reason(tmp_path, status):
    fake = FakeTornado(
        status=[httpx.Response(200, json={"status": status, "error": "video unavailable", "error_type": "warning"})]
    )
    with pytest.raises(TornadoError, match="video unavailable"):
        await make_client(fake).download_audio(VIDEO_ID, str(tmp_path / "a"), submission="gen-1")
    assert not (tmp_path / "a").exists()


@pytest.mark.asyncio
async def test_polls_until_completed_surviving_failed_reads(tmp_path):
    fake = FakeTornado(
        status=[
            httpx.Response(200, json={"status": "Pending"}),
            httpx.ConnectError("blip"),
            httpx.Response(502),
            httpx.Response(200, content=b"not json"),
            httpx.Response(200, json=["not an object"]),
            httpx.Response(200, json={"status": "Processing"}),
            httpx.Response(200, json={"status": "Completed", "s3_url": DELIVERY_URL}),
        ]
    )
    await make_client(fake).download_audio(VIDEO_ID, str(tmp_path / "a"), submission="gen-1")
    assert (tmp_path / "a").read_bytes() == b"audio"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["Processing", "SomethingNew"])
async def test_deadline_stops_polling_and_cancels_the_job(tmp_path, status):
    fake = FakeTornado(status=[httpx.Response(200, json={"status": status})])
    with pytest.raises(TornadoError, match="timed out"):
        await make_client(fake, deadline=9.0).download_audio(VIDEO_ID, str(tmp_path / "a"), submission="gen-1")
    assert len(fake.of("POST", "/jobs/job-1/cancel")) == 1


@pytest.mark.asyncio
async def test_account_error_while_polling_stops_immediately(tmp_path):
    fake = FakeTornado(status=[httpx.Response(401)])
    with pytest.raises(TornadoAccountError):
        await make_client(fake).download_audio(VIDEO_ID, str(tmp_path / "a"), submission="gen-1")
    assert len(fake.of("GET", "/jobs/job-1")) == 1


@pytest.mark.asyncio
async def test_completed_without_a_delivery_url_fails(tmp_path):
    fake = FakeTornado(status=[httpx.Response(200, json={"status": "Completed"})])
    with pytest.raises(TornadoError, match="delivery URL"):
        await make_client(fake).download_audio(VIDEO_ID, str(tmp_path / "a"), submission="gen-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("delivery", [httpx.Response(403), httpx.Response(200, content=b"")])
async def test_bad_delivery_leaves_no_partial_file(tmp_path, delivery):
    fake = FakeTornado(delivery=delivery)
    with pytest.raises(TornadoError):
        await make_client(fake).download_audio(VIDEO_ID, str(tmp_path / "a"), submission="gen-1")
    assert not (tmp_path / "a").exists()


@pytest.mark.asyncio
async def test_error_messages_dont_expose_the_signed_delivery_url(tmp_path):
    fake = FakeTornado(delivery=httpx.Response(403))
    with pytest.raises(TornadoError) as e:
        await make_client(fake).download_audio(VIDEO_ID, str(tmp_path / "a"), submission="gen-1")
    assert "verify=" not in str(e.value)


@pytest.mark.asyncio
async def test_metadata_success_and_failure():
    assert (await make_client(FakeTornado()).get_metadata(VIDEO_ID))["title"] == "A Song"
    with pytest.raises(TornadoError):
        await make_client(FakeTornado(metadata=httpx.Response(400, json={"error": "x"}))).get_metadata(VIDEO_ID)
    with pytest.raises(TornadoAccountError):
        await make_client(FakeTornado(metadata=httpx.Response(401))).get_metadata(VIDEO_ID)


@pytest.mark.parametrize(
    "url,extension,muxer",
    [
        ("https://files.tornadoapi.io/videos/abc.webm?verify=x", ".webm", "webm"),
        ("https://fsn1.your-objectstorage.com/tornado/videos/abc.opus?X-Amz-Signature=x", ".ogg", "ogg"),
        ("https://files.tornadoapi.io/videos/abc.mp4", ".m4a", "ipod"),
        ("https://files.tornadoapi.io/videos/abc.m4a", ".m4a", "ipod"),
        ("https://files.tornadoapi.io/videos/abc", ".webm", "webm"),
    ],
)
def test_audio_container_comes_from_the_delivery_path(url, extension, muxer):
    assert TornadoClient._audio_container(url) == (extension, muxer)


@pytest.mark.asyncio
async def test_source_file_is_removed_after_extraction(tmp_path):
    await make_client(FakeTornado()).download_audio(VIDEO_ID, str(tmp_path / "a"), submission="gen-1")
    assert [p.name for p in tmp_path.iterdir()] == ["a"]


@pytest.mark.asyncio
async def test_failed_extraction_fails_the_download_and_keeps_tornados_file(tmp_path):
    fake = FakeTornado()
    client = make_client(fake)

    def demux(source, dest, muxer):
        raise TornadoError("no audio track")

    client._demux = demux
    with pytest.raises(TornadoError, match="no audio track"):
        await client.download_audio(VIDEO_ID, str(tmp_path / "a"), submission="gen-1")
    assert fake.of("DELETE", "/jobs/job-1/file") == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_signed_delivery_urls_stay_out_of_the_logs(tmp_path, caplog):
    with caplog.at_level(logging.INFO, logger="httpx"):
        await make_client(FakeTornado()).download_audio(VIDEO_ID, str(tmp_path / "a"), submission="gen-1")
    assert "HTTP Request" in caplog.text  # other requests still log
    assert "verify=" not in caplog.text


def _packet_md5(path):
    return subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a", "-c", "copy", "-f", "md5", "-"],
        capture_output=True, text=True, check=True,
    ).stdout


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_demux_keeps_the_audio_packets_and_drops_the_video(tmp_path):
    source = tmp_path / "source.mkv"
    made = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=duration=1", "-f", "lavfi", "-i",
         "color=size=64x64:duration=1", "-c:a", "aac", "-c:v", "mpeg4", str(source)],
        capture_output=True,
    )
    if made.returncode:
        pytest.skip("this ffmpeg can't make the fixture")
    dest = tmp_path / "audio"
    _demux_audio(str(source), str(dest), "ipod")
    streams = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(dest)],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert streams == ["audio"]
    assert _packet_md5(dest) == _packet_md5(source)


def test_demux_failure_is_a_tornado_error_and_leaves_nothing(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"not media")
    dest = tmp_path / "audio"
    with pytest.raises(TornadoError):
        _demux_audio(str(source), str(dest), "webm")
    assert not dest.exists()
