import asyncio
import contextlib
import time

import numpy as np

SAMPLE_RATE = 48000
CHANNELS = 2
FRAME_BYTES = CHANNELS * 4
WINDOW_SAMPLES = 2048
HOP_SAMPLES = 512
UPDATE_HZ = 30


class PCMWindow:
    """Continuously drain capture; retain only the newest complete FFT window."""

    def __init__(self):
        self.data = bytearray()
        self.ready = asyncio.Event()
        self.updated_at = 0.0
        self.sequence = 0

    async def drain(self, reader):
        pending = bytearray()
        while chunk := await reader.read(HOP_SAMPLES * FRAME_BYTES):
            pending.extend(chunk)
            complete = len(pending) // FRAME_BYTES * FRAME_BYTES
            if not complete:
                continue
            self.data.extend(pending[:complete])
            del pending[:complete]
            del self.data[: max(0, len(self.data) - WINDOW_SAMPLES * FRAME_BYTES)]
            self.sequence += complete // FRAME_BYTES
            self.updated_at = time.monotonic()
            if len(self.data) == WINDOW_SAMPLES * FRAME_BYTES:
                self.ready.set()
        raise EOFError("PCM capture ended")


class SpectrumAnalyzer:
    def __init__(self):
        self.window = np.hanning(WINDOW_SAMPLES)
        self.edges = np.geomspace(1, WINDOW_SAMPLES // 2, 65).astype(int)

    def analyze(self, raw):
        samples = np.frombuffer(raw, dtype="<f4").reshape(-1, CHANNELS)
        fft = np.abs(np.fft.rfft(samples.mean(axis=1) * self.window)) / (
            WINDOW_SAMPLES / 2
        )
        bins = [
            float(
                np.clip(
                    (
                        20 * np.log10(max(float(fft[lo : max(lo + 1, hi)].max()), 1e-6))
                        + 80
                    )
                    / 80,
                    0,
                    1,
                )
            )
            for lo, hi in zip(self.edges[:-1], self.edges[1:])
        ]
        peak = (
            20 * np.log10(np.maximum(np.max(np.abs(samples), axis=0), 1e-6))
        ).tolist()
        return bins, peak


class Visualizer:
    """Optional PCM analysis; never part of the audio delivery path."""

    def __init__(self, coordinator, capture_target="losspeaker.bus"):
        self.c = coordinator
        self.capture_target = capture_target
        self.listeners = set()
        self.task = None
        self.output_peak = []

    async def frames(self):
        q = asyncio.Queue(maxsize=1)
        self.listeners.add(q)
        if not self.task:
            self.task = asyncio.create_task(self.worker())
        try:
            while True:
                yield await q.get()
        finally:
            self.listeners.discard(q)
            if not self.listeners and self.task:
                self.task.cancel()
                await asyncio.gather(self.task, return_exceptions=True)
                self.task = None

    async def telemetry(self):
        while True:
            self.output_peak = []
            if (
                not self.c.audio.settings.demo
                and self.c.state.mode == "processed"
                and self.c.state.status == "playing"
            ):
                with contextlib.suppress(Exception):
                    self.output_peak = await self.c.audio.camilla(
                        "GetPlaybackSignalPeak"
                    )
            await asyncio.sleep(0.25)

    async def worker(self):
        proc = None
        capture_task = None
        capture = None
        analyzer = SpectrumAnalyzer()
        telemetry = asyncio.create_task(self.telemetry())

        async def stop_capture():
            nonlocal proc, capture_task, capture
            if proc and proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), 1)
                except TimeoutError:
                    proc.kill()
                    await proc.wait()
            if capture_task:
                capture_task.cancel()
                await asyncio.gather(capture_task, return_exceptions=True)
            proc = capture_task = capture = None

        try:
            while self.listeners:
                start = time.monotonic()
                try:
                    if (
                        self.c.audio.settings.demo
                        or self.c.state.mode == "direct"
                        or self.c.state.status != "playing"
                    ):
                        await stop_capture()
                        frame = {
                            "available": False,
                            "reason": "Simulated mode"
                            if self.c.audio.settings.demo
                            else "No processed audio",
                            "spectrum": [],
                            "peak": [],
                            "output_peak": [],
                        }
                        await asyncio.sleep(0.25)
                    else:
                        if proc is None:
                            proc = await asyncio.create_subprocess_exec(
                                "pw-record",
                                f"--target={self.capture_target}",
                                '--properties={"stream.capture.sink":true}',
                                f"--latency={HOP_SAMPLES}",
                                f"--rate={SAMPLE_RATE}",
                                f"--channels={CHANNELS}",
                                "--format=f32",
                                "--raw",
                                "-",
                                stdout=asyncio.subprocess.PIPE,
                                stderr=asyncio.subprocess.DEVNULL,
                            )
                            capture = PCMWindow()
                            capture_task = asyncio.create_task(
                                capture.drain(proc.stdout)
                            )
                        await asyncio.wait_for(capture.ready.wait(), 2)
                        if capture_task.done():
                            capture_task.result()
                        if time.monotonic() - capture.updated_at > 0.5:
                            raise TimeoutError("PCM capture stopped updating")
                        # Analysis and slow WebSocket clients cannot hold up the
                        # independent drain task or build a queue of old audio.
                        bins, peak = analyzer.analyze(bytes(capture.data))
                        frame = {
                            "available": True,
                            "label": "Pre-DSP",
                            "spectrum": bins,
                            "peak": peak,
                            "output_peak": self.output_peak,
                            "sequence": capture.sequence,
                            "analysis_window_ms": WINDOW_SAMPLES / SAMPLE_RATE * 1000,
                        }
                except asyncio.CancelledError:
                    raise
                except Exception:
                    await stop_capture()
                    frame = {
                        "available": False,
                        "reason": "PCM monitor unavailable; retrying",
                        "spectrum": [],
                        "peak": [],
                        "output_peak": [],
                    }
                    await asyncio.sleep(1)
                for q in self.listeners:
                    if q.full():
                        q.get_nowait()
                    q.put_nowait(frame)
                await asyncio.sleep(max(0, 1 / UPDATE_HZ - (time.monotonic() - start)))
        finally:
            telemetry.cancel()
            await asyncio.gather(telemetry, return_exceptions=True)
            await stop_capture()
