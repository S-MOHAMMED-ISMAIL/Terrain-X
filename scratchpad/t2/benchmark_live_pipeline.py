"""T2 live-stack benchmark using only public TERRAIN-X APIs."""

import argparse
import json
import time
import uuid
from datetime import datetime
from pathlib import Path

import httpx

TERMINAL = {"completed", "failed", "cancelled"}


def cgroup_memory_mib() -> float | None:
    path = Path("/sys/fs/cgroup/memory.current")
    if not path.is_file():
        return None
    return int(path.read_text(encoding="ascii").strip()) / (1024 * 1024)


def require(response: httpx.Response) -> httpx.Response:
    if response.is_error:
        raise RuntimeError(f"{response.request.method} {response.request.url}: {response.status_code}")
    return response


def timed_request(client: httpx.Client, method: str, url: str, **kwargs):
    start = time.perf_counter()
    response = require(client.request(method, url, **kwargs))
    return response, time.perf_counter() - start


def poll_job(client: httpx.Client, url: str, *, timeout: float) -> tuple[dict, list[dict], float]:
    started = time.perf_counter()
    events: list[dict] = []
    last_stage = None
    peak_memory = cgroup_memory_mib()
    while time.perf_counter() - started < timeout:
        job = require(client.get(url)).json()
        memory = cgroup_memory_mib()
        if memory is not None:
            peak_memory = max(peak_memory or memory, memory)
        if job["current_stage"] != last_stage:
            events.append(
                {
                    "elapsed_seconds": time.perf_counter() - started,
                    "stage": job["current_stage"],
                    "status": job["status"],
                    "cgroup_memory_mib": memory,
                }
            )
            last_stage = job["current_stage"]
        if job["status"] in TERMINAL:
            return job, events, peak_memory or 0.0
        time.sleep(0.25)
    raise TimeoutError(f"Job did not become terminal within {timeout}s")


def poll_report(client: httpx.Client, url: str, *, timeout: float) -> tuple[dict, float, float]:
    started = time.perf_counter()
    peak_memory = cgroup_memory_mib()
    while time.perf_counter() - started < timeout:
        report = require(client.get(url)).json()
        memory = cgroup_memory_mib()
        if memory is not None:
            peak_memory = max(peak_memory or memory, memory)
        if report["status"] in {"completed", "failed"}:
            return report, time.perf_counter() - started, peak_memory or 0.0
        time.sleep(0.25)
    raise TimeoutError(f"Report did not become terminal within {timeout}s")


def parsed_elapsed(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--dem", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--base-url", default="http://backend:8000/api/v1")
    args = parser.parse_args()

    email = f"t2-performance-{uuid.uuid4().hex}@example.com"
    password = f"T2-{uuid.uuid4().hex}-Aa1!"
    results: dict = {
        "label": "PERFORMANCE VALIDATION ONLY",
        "base_url": args.base_url,
        "requests": {},
    }

    with httpx.Client(base_url=args.base_url, timeout=120.0) as client:
        require(client.post("/auth/register", json={"email": email, "password": password}))
        token = require(
            client.post("/auth/login", json={"email": email, "password": password})
        ).json()["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"

        project = require(
            client.post(
                "/projects",
                json={
                    "name": "T2 realistic performance validation",
                    "description": "Temporary performance-only 4096 workload.",
                },
            )
        ).json()
        project_id = project["id"]

        with open(args.source, "rb") as source_file:
            response, seconds = timed_request(
                client,
                "POST",
                f"/projects/{project_id}/datasets",
                data={"role": "source_image"},
                files={"file": (Path(args.source).name, source_file, "image/tiff")},
            )
        source = response.json()
        results["requests"]["source_upload"] = {
            "seconds": seconds,
            "status_code": response.status_code,
            "source_size_bytes": Path(args.source).stat().st_size,
        }

        with open(args.dem, "rb") as dem_file:
            response, seconds = timed_request(
                client,
                "POST",
                f"/projects/{project_id}/datasets",
                data={"role": "dem_reference"},
                files={"file": (Path(args.dem).name, dem_file, "image/tiff")},
            )
        dem = response.json()
        results["requests"]["dem_upload"] = {
            "seconds": seconds,
            "status_code": response.status_code,
            "source_size_bytes": Path(args.dem).stat().st_size,
        }

        create_started = time.perf_counter()
        job = require(
            client.post(
                f"/projects/{project_id}/datasets/{source['id']}/analysis",
                json={"parameters": {"dem_reference_dataset_id": dem["id"]}},
            )
        ).json()
        job_url = f"/projects/{project_id}/analysis/{job['id']}"
        job, events, peak_memory = poll_job(client, job_url, timeout=900)
        results["analysis"] = {
            "job_id": job["id"],
            "status": job["status"],
            "error_message": job["error_message"],
            "calibration_status": job["calibration_status"],
            "calibration_metadata": job["calibration_metadata"],
            "ground_filter_status": job["ground_filter_status"],
            "execution_summary": job["execution_summary"],
            "api_wall_seconds": time.perf_counter() - create_started,
            "worker_timestamp_seconds": parsed_elapsed(job["started_at"], job["completed_at"]),
            "stage_observations": events,
            "peak_container_memory_mib": peak_memory,
        }
        if job["status"] != "completed":
            raise RuntimeError(f"Analysis ended as {job['status']}: {job['error_message']}")

        artifacts = require(client.get(f"{job_url}/artifacts")).json()
        results["artifacts"] = artifacts
        depth = next(item for item in artifacts if item["artifact_type"] == "relative_depth")
        base = f"{job_url}/artifacts/{depth['id']}/visualization"

        for name, url in {
            "dataset_preview": f"/projects/{project_id}/datasets/{source['id']}/visualization/preview",
            "dataset_map_preview": (
                f"/projects/{project_id}/datasets/{source['id']}/visualization/map-preview"
            ),
            "depth_preview": f"{base}/preview",
            "depth_map_preview": f"{base}/map-preview",
            "terrain_metadata": f"{base}/terrain/metadata",
            "terrain_grid": f"{base}/terrain/grid",
            "mesh_256": f"{base}/terrain/mesh.glb?resolution=256&texture=true",
            "mesh_512": f"{base}/terrain/mesh.glb?resolution=512&texture=true",
        }.items():
            response, seconds = timed_request(client, "GET", url)
            entry = {
                "seconds": seconds,
                "status_code": response.status_code,
                "size_bytes": len(response.content),
                "content_type": response.headers.get("content-type"),
            }
            if name == "terrain_metadata":
                entry["metadata"] = response.json()
            results["requests"][name] = entry

        report_create_started = time.perf_counter()
        report = require(
            client.post(
                f"/projects/{project_id}/datasets/{source['id']}/reports",
                json={},
            )
        ).json()
        report_url = f"/projects/{project_id}/reports/{report['id']}"
        report, report_poll_seconds, report_peak_memory = poll_report(
            client, report_url, timeout=900
        )
        results["report"] = {
            "report_id": report["id"],
            "status": report["status"],
            "error_message": report["error_message"],
            "api_wall_seconds": time.perf_counter() - report_create_started,
            "poll_seconds": report_poll_seconds,
            "timestamp_seconds": parsed_elapsed(report["created_at"], report["completed_at"]),
            "peak_container_memory_mib": report_peak_memory,
        }
        if report["status"] != "completed":
            raise RuntimeError(f"Report ended as {report['status']}: {report['error_message']}")

        metadata_response, seconds = timed_request(client, "GET", f"{report_url}/json")
        results["requests"]["report_json"] = {
            "seconds": seconds,
            "status_code": metadata_response.status_code,
            "size_bytes": len(metadata_response.content),
        }
        results["report"]["metadata"] = metadata_response.json()
        for name in ("pdf", "bundle"):
            response, seconds = timed_request(client, "GET", f"{report_url}/{name}")
            results["requests"][f"report_{name}"] = {
                "seconds": seconds,
                "status_code": response.status_code,
                "size_bytes": len(response.content),
                "content_type": response.headers.get("content-type"),
            }

        results["ids"] = {
            "project_id": project_id,
            "source_dataset_id": source["id"],
            "dem_dataset_id": dem["id"],
            "job_id": job["id"],
            "depth_artifact_id": depth["id"],
            "report_id": report["id"],
        }

    Path(args.output).write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": args.output,
                "analysis_status": results["analysis"]["status"],
                "analysis_wall_seconds": results["analysis"]["api_wall_seconds"],
                "calibration_status": results["analysis"]["calibration_status"],
                "report_status": results["report"]["status"],
                "report_wall_seconds": results["report"]["api_wall_seconds"],
                "project_id": results["ids"]["project_id"],
            }
        )
    )


if __name__ == "__main__":
    main()
