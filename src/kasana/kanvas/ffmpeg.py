"""FFmpeg process lifecycle for ephemeral fragmented-MP4 browser delivery."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress


class FFmpegError(RuntimeError):
    """FFmpeg could not start or did not produce a valid stream."""


class FragmentedMp4Stream:
    """One running FFmpeg process whose stdout is the HTTP response body."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self._process = process

    async def chunks(self) -> AsyncIterator[bytes]:
        stdout = self._process.stdout
        stderr = self._process.stderr
        if stdout is None or stderr is None:
            raise FFmpegError("FFmpeg did not expose streaming output.")
        stderr_reader = asyncio.create_task(stderr.read())
        try:
            while chunk := await stdout.read(64 * 1024):
                yield chunk
            error_output = (await stderr_reader).decode("utf-8", errors="replace").strip()
            return_code = await self._process.wait()
            if return_code != 0:
                detail = error_output or f"FFmpeg exited with status {return_code}."
                raise FFmpegError(detail)
        finally:
            if not stderr_reader.done():
                stderr_reader.cancel()
                with suppress(asyncio.CancelledError):
                    await stderr_reader
            await self.close()

    async def close(self) -> None:
        if self._process.returncode is not None:
            return
        self._process.terminate()
        try:
            await asyncio.wait_for(self._process.wait(), timeout=2)
        except TimeoutError:
            self._process.kill()
            await self._process.wait()


async def start_fragmented_mp4(
    executable: str,
    input_url: str,
    *,
    audio_stream_index: int,
    transcode_audio: bool,
) -> FragmentedMp4Stream:
    """Start copy/remux delivery without writing an intermediate media file."""

    command = [
        executable,
        "-v",
        "error",
        "-nostdin",
        "-i",
        input_url,
        "-map",
        "0:v:0",
        "-map",
        f"0:a:{audio_stream_index}",
        "-sn",
        "-c:v",
        "copy",
        "-c:a",
        "aac" if transcode_audio else "copy",
        "-movflags",
        "frag_keyframe+empty_moov+default_base_moof",
        "-f",
        "mp4",
        "pipe:1",
    ]
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as error:
        raise FFmpegError(f"Unable to start FFmpeg: {error}") from error
    return FragmentedMp4Stream(process)
