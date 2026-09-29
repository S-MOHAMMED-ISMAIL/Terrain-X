"""T2 performance-only harness for the unchanged production depth path."""

import argparse
import json
import os
import threading
import time
from pathlib import Path

import rasterio
from app.services.depth_pipeline import extract_rgb_uint8

from ai.registry import get_depth_estimator
from geospatial.raster_io import read_raster_array, write_single_band_float32


def current_rss_mib() -> float:
    with open("/proc/self/status", encoding="ascii") as status:
        for line in status:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    raise RuntimeError("VmRSS is unavailable")


class RSSMonitor:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._peak = current_rss_mib()
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self) -> None:
        while not self._stop.wait(0.02):
            rss = current_rss_mib()
            with self._lock:
                self._peak = max(self._peak, rss)

    def start(self) -> None:
        self._thread.start()

    def reset_peak(self) -> None:
        with self._lock:
            self._peak = current_rss_mib()

    def peak(self) -> float:
        with self._lock:
            return self._peak

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--run", type=int, required=True)
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    timings: dict[str, dict[str, float]] = {}
    monitor = RSSMonitor()
    monitor.start()
    overall_start = time.perf_counter()

    def measure(name, operation):
        monitor.reset_peak()
        start = time.perf_counter()
        value = operation()
        timings[name] = {
            "seconds": time.perf_counter() - start,
            "peak_rss_mib": monitor.peak(),
        }
        return value

    try:
        raster = measure("raster_read", lambda: read_raster_array(input_path))
        rgb = measure("rgb_preprocessing", lambda: extract_rgb_uint8(raster, "geotiff"))
        estimator = get_depth_estimator()
        measure("model_load", estimator.load)
        prediction = measure("depth_predict_total", lambda: estimator.predict(rgb))
        measure(
            "depth_raster_write",
            lambda: write_single_band_float32(
                output_path,
                prediction.depth,
                crs=raster.crs,
                transform=raster.transform,
            ),
        )

        def verify_output() -> dict:
            with rasterio.open(output_path) as written:
                sample = written.read(1, window=((0, 1), (0, 1)))
                return {
                    "width": written.width,
                    "height": written.height,
                    "count": written.count,
                    "dtype": written.dtypes[0],
                    "crs": str(written.crs),
                    "sample_is_finite": bool(sample.size and sample[0, 0] == sample[0, 0]),
                }

        verification = measure("depth_raster_verify", verify_output)
        model = estimator.info()
        result = {
            "run": args.run,
            "input": {
                "path": str(input_path),
                "width": int(raster.data.shape[2]),
                "height": int(raster.data.shape[1]),
                "bands": int(raster.data.shape[0]),
                "dtype": raster.dtype,
                "size_bytes": input_path.stat().st_size,
            },
            "model": {
                "name": model.name,
                "revision": model.revision,
                "device": model.device,
                "model_input_width": prediction.model_input_width,
                "model_input_height": prediction.model_input_height,
            },
            "prediction": {
                "output_width": prediction.input_width,
                "output_height": prediction.input_height,
                "model_forward_and_resize_seconds": prediction.inference_seconds,
                "min": float(prediction.depth.min()),
                "max": float(prediction.depth.max()),
                "mean": float(prediction.depth.mean()),
            },
            "output": {
                "path": str(output_path),
                "size_bytes": os.path.getsize(output_path),
                **verification,
            },
            "timings": timings,
            "total_seconds": time.perf_counter() - overall_start,
            "peak_rss_mib": max(stage["peak_rss_mib"] for stage in timings.values()),
        }
    finally:
        monitor.stop()

    print(json.dumps(result))


if __name__ == "__main__":
    main()
