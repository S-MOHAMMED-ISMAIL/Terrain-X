import requests, json, time, os

BASE = "http://localhost:8000/api/v1"
with open("scratchpad/synth_04_state.json") as f:
    state = json.load(f)

token = state["token"]
project_id = state["project_id"]
datasets = state["datasets"]
headers = {"Authorization": f"Bearer {token}"}

def poll_job(job_id, max_polls=120, interval=5):
    for i in range(max_polls):
        time.sleep(interval)
        r = requests.get(f"{BASE}/projects/{project_id}/analysis/{job_id}", headers=headers)
        job = r.json()
        status = job["status"]
        stage = job.get("current_stage", "N/A")
        print(f"  Poll {i+1}: status={status}, stage={stage}")
        if status in ("completed", "failed", "cancelled"):
            return job
    return job

# Flow B: Georeferenced RGB -> metadata/CRS validation
print("=== FLOW B: Georeferenced RGB Validation ===")
r = requests.get(f"{BASE}/projects/{project_id}/datasets/{datasets['rgb_tif']}", headers=headers)
print(f"Get dataset: {r.status_code}")
ds = r.json()
print(f"  Filename: {ds.get('filename')}")
print(f"  CRS: {ds.get('crs')}")
print(f"  Width: {ds.get('width')}, Height: {ds.get('height')}")
print(f"  Bands: {ds.get('bands')}")
print(f"  Pixel size: {ds.get('pixel_size')}")
print(f"  Georeferenced: {ds.get('georeferenced')}")
print(f"  Status: {ds.get('status')}")

# Flow C: RGB + DEM -> calibration
print("\n=== FLOW C: RGB + DEM Calibration ===")
r = requests.post(f"{BASE}/projects/{project_id}/datasets/{datasets['rgb_tif']}/analysis",
                   headers=headers, json={"parameters": {"version": "v1", "dem_reference_dataset_id": datasets["dem"]}})
print(f"Create job: {r.status_code}")
if r.status_code == 201:
    job_c = r.json()
    job_c_id = job_c["id"]
    print(f"Job ID: {job_c_id}")
    job_c = poll_job(job_c_id)
    print(f"Final status: {job_c['status']}")
    print(f"Calibration status: {job_c.get('calibration_status', 'N/A')}")
    if job_c.get("calibration_metadata"):
        cm = job_c["calibration_metadata"]
        print(f"  Scale: {cm.get('scale')}, Offset: {cm.get('offset')}")
        print(f"  Samples: {cm.get('sample_count')}, Inliers: {cm.get('inlier_count')}, Outliers: {cm.get('outlier_count')}")
        print(f"  MAE: {cm.get('mae')}, RMSE: {cm.get('rmse')}, Bias: {cm.get('bias')}")
    if job_c["status"] == "failed":
        print(f"Failure reason: {job_c.get('failure_reason', 'N/A')}")
    with open("scratchpad/synth_04_job_c.json", "w") as f:
        json.dump(job_c, f, indent=2)
    print("Job C result saved")
else:
    print(f"Failed: {r.text}")

# Flow D: RGB + GCP -> GCP calibration
print("\n=== FLOW D: RGB + GCP Calibration ===")
r = requests.post(f"{BASE}/projects/{project_id}/datasets/{datasets['rgb_tif']}/analysis",
                   headers=headers, json={"parameters": {"version": "v1", "gcp_reference_dataset_id": datasets["gcp"]}})
print(f"Create job: {r.status_code}")
if r.status_code == 201:
    job_d = r.json()
    job_d_id = job_d["id"]
    print(f"Job ID: {job_d_id}")
    job_d = poll_job(job_d_id)
    print(f"Final status: {job_d['status']}")
    print(f"Calibration status: {job_d.get('calibration_status', 'N/A')}")
    if job_d.get("calibration_metadata"):
        cm = job_d["calibration_metadata"]
        print(f"  Scale: {cm.get('scale')}, Offset: {cm.get('offset')}")
        print(f"  Samples: {cm.get('sample_count')}, Inliers: {cm.get('inlier_count')}, Outliers: {cm.get('outlier_count')}")
        print(f"  MAE: {cm.get('mae')}, RMSE: {cm.get('rmse')}, Bias: {cm.get('bias')}")
    if job_d["status"] == "failed":
        print(f"Failure reason: {job_d.get('failure_reason', 'N/A')}")
    with open("scratchpad/synth_04_job_d.json", "w") as f:
        json.dump(job_d, f, indent=2)
    print("Job D result saved")
else:
    print(f"Failed: {r.text}")
