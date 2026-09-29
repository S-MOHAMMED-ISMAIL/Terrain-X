"""T2 synthetic calibrated performance harness; not scientific validation."""

import argparse
import gc
import json
import threading
import time
from functools import partial
from pathlib import Path

import numpy as np
import rasterio
from app.core.config import get_settings
from app.services.calibration_pipeline import quality_gate_policy
from app.services.ground_filter_pipeline import run_ground_filter

from geospatial.calibration import (
    compute_fit_diagnostics,
    compute_validation_metrics,
    cross_validate_samples,
    evaluate_quality_gate,
    fit_robust_affine,
    sample_dem_pairs,
)
from geospatial.ground_filter import GROUND_FILTER_NODATA
from geospatial.mesh_export import build_terrain_mesh, write_glb
from geospatial.raster_io import write_single_band_float32
from geospatial.raster_preview import generate_preview_png
from geospatial.terrain_derivatives import (
    compute_hillshade,
    compute_slope_aspect,
    prepare_elevation,
)
from geospatial.terrain_grid import extract_terrain_grid
from geospatial.vertical_units import KNOWN, METRE, VerticalUnitResolution


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
    parser.add_argument("--depth", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    depth_path = Path(args.depth)
    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
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
        def read_depth():
            dataset = rasterio.open(depth_path)
            return dataset, dataset.read(1).astype("float32")

        dataset, depth = measure("depth_raster_read", read_depth)
        crs = dataset.crs
        transform = dataset.transform
        dataset.close()

        synthetic_dem = (2.5 * depth + 100.0).astype("float32")
        dem_path = work_dir / "synthetic_reference_PERFORMANCE_ONLY.tif"
        measure(
            "synthetic_reference_write",
            partial(
                write_single_band_float32,
                dem_path,
                synthetic_dem,
                crs=crs,
                transform=transform,
                band_unit="metre",
            ),
        )
        del synthetic_dem
        gc.collect()

        samples = measure(
            "calibration_sampling",
            lambda: sample_dem_pairs(
                depth,
                crs,
                transform,
                dem_path,
                max_samples=settings.CALIBRATION_MAX_SAMPLES,
            ),
        )
        fit = measure(
            "calibration_fit",
            lambda: fit_robust_affine(
                samples.relative_depth,
                samples.reference_elevation,
                outlier_sigma=settings.CALIBRATION_OUTLIER_SIGMA,
                max_iterations=settings.CALIBRATION_MAX_ITERATIONS,
            ),
        )
        metrics = measure(
            "calibration_metrics",
            lambda: compute_validation_metrics(
                samples.relative_depth, samples.reference_elevation, fit
            ),
        )
        diagnostics = measure(
            "calibration_diagnostics",
            lambda: compute_fit_diagnostics(
                samples.relative_depth,
                samples.reference_elevation,
                fit,
                effective_sample_count=samples.effective_sample_count,
            ),
        )
        policy = quality_gate_policy(settings)
        cross_validation = measure(
            "calibration_cross_validation",
            lambda: cross_validate_samples(
                samples,
                reference_type="dem",
                source_height=depth.shape[0],
                source_width=depth.shape[1],
                policy=policy,
                outlier_sigma=settings.CALIBRATION_OUTLIER_SIGMA,
                max_iterations=settings.CALIBRATION_MAX_ITERATIONS,
            ),
        )
        gate = measure(
            "calibration_quality_gate",
            lambda: evaluate_quality_gate(fit, cross_validation, policy),
        )
        if not gate.passed:
            raise RuntimeError(f"Synthetic performance calibration did not pass: {gate.as_dict()}")

        metric = measure(
            "metric_elevation_compute",
            lambda: (fit.scale * depth + fit.offset).astype("float32"),
        )
        metric_path = work_dir / "metric_elevation_PERFORMANCE_ONLY.tif"
        measure(
            "metric_elevation_write",
            lambda: write_single_band_float32(
                metric_path,
                metric,
                crs=crs,
                transform=transform,
                band_unit="metre",
            ),
        )

        vertical_unit = VerticalUnitResolution(
            KNOWN,
            METRE,
            "T2 synthetic performance fixture",
            "metre",
            None,
        )
        ground = measure(
            "ground_filter",
            lambda: run_ground_filter(
                metric,
                crs=crs,
                transform=transform,
                settings=settings,
                vertical_unit=vertical_unit,
            ),
        )
        if ground.result is None:
            raise RuntimeError(f"Ground filter failed: {ground.metadata}")

        dtm_path = work_dir / "dtm_PERFORMANCE_ONLY.tif"
        ndsm_path = work_dir / "ndsm_PERFORMANCE_ONLY.tif"
        measure(
            "dtm_write",
            lambda: write_single_band_float32(
                dtm_path,
                ground.result.dtm,
                crs=crs,
                transform=transform,
                nodata=GROUND_FILTER_NODATA,
                band_unit="metre",
            ),
        )
        measure(
            "ndsm_write",
            lambda: write_single_band_float32(
                ndsm_path,
                ground.result.ndsm,
                crs=crs,
                transform=transform,
                nodata=GROUND_FILTER_NODATA,
                band_unit="metre",
            ),
        )

        prepared = measure("derivative_prepare", lambda: prepare_elevation(metric_path))
        slope, aspect = measure(
            "slope_aspect_compute", partial(compute_slope_aspect, prepared)
        )
        derivative_summary = {
            "slope_shape": list(slope.shape),
            "aspect_shape": list(aspect.shape),
            "slope_finite_count": int(np.count_nonzero(slope != -9999.0)),
        }
        del slope, aspect
        gc.collect()
        hillshade = measure("hillshade_compute", partial(compute_hillshade, prepared))
        derivative_summary["hillshade_shape"] = list(hillshade.shape)
        del hillshade, prepared
        gc.collect()

        preview = measure(
            "visualization_preview_1024",
            lambda: generate_preview_png(metric_path, max_dimension=1024),
        )
        grid_256 = measure(
            "terrain_grid_256", lambda: extract_terrain_grid(metric_path, max_dimension=256)
        )
        grid_512 = measure(
            "terrain_grid_512", lambda: extract_terrain_grid(metric_path, max_dimension=512)
        )

        def mesh_bytes(grid):
            mesh = build_terrain_mesh(
                grid.elevations,
                step_x=abs(grid.cell_size_x or 1.0),
                step_z=abs(grid.cell_size_y or 1.0),
            )
            data = write_glb(mesh, extras={"label": "PERFORMANCE FIXTURE ONLY"})
            return data, mesh.vertex_count, mesh.triangle_count

        glb_256, vertices_256, triangles_256 = measure(
            "mesh_glb_256", lambda: mesh_bytes(grid_256)
        )
        glb_512, vertices_512, triangles_512 = measure(
            "mesh_glb_512", lambda: mesh_bytes(grid_512)
        )

        result = {
            "label": "SYNTHETIC CALIBRATED PERFORMANCE FIXTURE ONLY",
            "scientific_validation": False,
            "source_depth": str(depth_path),
            "dimensions": [int(depth.shape[1]), int(depth.shape[0])],
            "calibration": {
                "total_candidate_samples": samples.total_candidates,
                "valid_samples": samples.valid_count,
                "effective_samples": samples.effective_sample_count,
                "fit_scale": fit.scale,
                "fit_offset": fit.offset,
                "fit_iterations": fit.iterations_used,
                "mae": metrics.mae,
                "rmse": metrics.rmse,
                "cv_skill": cross_validation.skill,
                "cv_folds": len(cross_validation.folds),
                "gate": gate.as_dict(),
                "diagnostics": diagnostics.as_dict(),
            },
            "ground_filter": {
                "status": ground.status.value,
                "duration_seconds_reported": ground.metadata.get("duration_seconds"),
                "window_levels": ground.metadata.get("window_levels"),
                "statistics": ground.metadata.get("statistics"),
            },
            "derivatives": derivative_summary,
            "visualization": {
                "preview_size_bytes": len(preview),
                "grid_256_dimensions": [grid_256.width, grid_256.height],
                "grid_512_dimensions": [grid_512.width, grid_512.height],
                "glb_256_size_bytes": len(glb_256),
                "glb_256_vertices": vertices_256,
                "glb_256_triangles": triangles_256,
                "glb_512_size_bytes": len(glb_512),
                "glb_512_vertices": vertices_512,
                "glb_512_triangles": triangles_512,
            },
            "files": {
                path.name: path.stat().st_size
                for path in (dem_path, metric_path, dtm_path, ndsm_path)
            },
            "timings": timings,
            "total_seconds": time.perf_counter() - overall_start,
            "peak_rss_mib": max(stage["peak_rss_mib"] for stage in timings.values()),
            "process_final_rss_mib": current_rss_mib(),
            "configuration": {
                "calibration_max_samples": settings.CALIBRATION_MAX_SAMPLES,
                "ground_filter_max_window_m": settings.GROUND_FILTER_MAX_WINDOW_M,
                "ground_filter_slope": settings.GROUND_FILTER_SLOPE,
                "ground_filter_initial_threshold": settings.GROUND_FILTER_INITIAL_THRESHOLD,
                "ground_filter_max_threshold": settings.GROUND_FILTER_MAX_THRESHOLD,
                "preview_max_dimension": settings.PREVIEW_MAX_DIMENSION,
                "terrain_max_dimension": settings.MAX_TERRAIN_DIMENSION,
                "mesh_export_resolutions": settings.MESH_EXPORT_RESOLUTIONS,
            },
        }
    finally:
        monitor.stop()

    Path(args.output).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": args.output,
                "total_seconds": result["total_seconds"],
                "peak_rss_mib": result["peak_rss_mib"],
                "gate_passed": result["calibration"]["gate"]["passed"],
                "ground_filter_status": result["ground_filter"]["status"],
            }
        )
    )


if __name__ == "__main__":
    main()
