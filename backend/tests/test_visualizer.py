import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest

from losspeaker.visualizer import (
    CHANNELS,
    FRAME_BYTES,
    HOP_SAMPLES,
    SAMPLE_RATE,
    WINDOW_SAMPLES,
    PCMWindow,
    SpectrumAnalyzer,
    Visualizer,
)


async def test_capture_discards_backlog_before_analysis():
    reader = asyncio.StreamReader()
    old = np.full((48000, CHANNELS), 0.8, dtype="<f4").tobytes()
    newest = np.zeros((WINDOW_SAMPLES, CHANNELS), dtype="<f4").tobytes()
    reader.feed_data(old + newest)
    reader.feed_eof()
    window = PCMWindow()
    with pytest.raises(EOFError):
        await window.drain(reader)
    assert window.ready.is_set()
    assert len(window.data) == WINDOW_SAMPLES * FRAME_BYTES
    assert window.sequence == 48000 + WINDOW_SAMPLES
    assert bytes(window.data) == newest


async def test_capture_preserves_frames_across_partial_reads():
    raw = np.arange((WINDOW_SAMPLES + 8) * CHANNELS, dtype="<f4").tobytes()

    class PartialReader:
        def __init__(self):
            self.offset = 0

        async def read(self, size):
            start = self.offset
            self.offset += 13
            return raw[start : self.offset]

    window = PCMWindow()
    with pytest.raises(EOFError):
        await window.drain(PartialReader())
    assert bytes(window.data) == raw[-WINDOW_SAMPLES * FRAME_BYTES :]
    assert window.sequence == WINDOW_SAMPLES + 8


def test_short_window_keeps_bass_and_silence_is_zero():
    analyzer = SpectrumAnalyzer()
    assert WINDOW_SAMPLES / SAMPLE_RATE < 0.05
    silent = np.zeros((WINDOW_SAMPLES, CHANNELS), dtype="<f4")
    bins, peaks = analyzer.analyze(silent.tobytes())
    assert bins == [0.0] * 64
    assert max(peaks) <= -100
    phase = np.arange(WINDOW_SAMPLES) / SAMPLE_RATE
    bass = np.repeat(
        (0.2 * np.sin(2 * np.pi * 46.875 * phase))[:, None], CHANNELS, axis=1
    ).astype("<f4")
    bins, peaks = analyzer.analyze(bass.tobytes())
    assert max(bins[:16]) > 0.7
    assert len(bins) == 64 and all(0 <= value <= 1 for value in bins)
    assert -15 < peaks[0] < -13


async def test_slow_viewer_gets_latest_audio_and_capture_stops_on_close(monkeypatch):
    reader = asyncio.StreamReader()

    class Process:
        returncode = None
        stdout = reader
        produced = 0

        def terminate(self):
            self.returncode = 0
            reader.feed_eof()

        def kill(self):
            self.terminate()

        async def wait(self):
            return self.returncode

    proc = Process()
    create = AsyncMock(return_value=proc)
    monkeypatch.setattr("asyncio.create_subprocess_exec", create)
    audio = SimpleNamespace(
        settings=SimpleNamespace(demo=False), camilla=AsyncMock(return_value=[])
    )
    visualizer = Visualizer(
        SimpleNamespace(
            audio=audio, state=SimpleNamespace(mode="processed", status="playing")
        )
    )

    async def feed():
        while proc.returncode is None:
            reader.feed_data(np.zeros((HOP_SAMPLES, CHANNELS), dtype="<f4").tobytes())
            proc.produced += HOP_SAMPLES
            await asyncio.sleep(0.005)

    producer = asyncio.create_task(feed())
    frames = visualizer.frames()
    try:
        first = await asyncio.wait_for(anext(frames), 1)
        await asyncio.sleep(0.18)  # A viewer that stops reading must not delay capture.
        latest = await asyncio.wait_for(anext(frames), 1)
        assert latest["sequence"] > first["sequence"] + WINDOW_SAMPLES
        assert proc.produced - latest["sequence"] <= 2 * WINDOW_SAMPLES
        assert latest["analysis_window_ms"] < 50
        assert "--latency=512" in create.call_args.args
    finally:
        await frames.aclose()
        await producer
    assert proc.returncode == 0
    assert visualizer.task is None
    assert not visualizer.listeners
