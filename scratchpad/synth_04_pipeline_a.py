import requests
import json
import time
import os

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

# Flow A: JPEG -> depth pipeline
print("=== FLOW A: Single Image Depth ===")
r = requests.post(f"{BASE}/projects/{project_id}/datasets/{datasets['jpeg']}/analysis",
                   headers=headers, json={"parameters": {"version": "v1"}})
print(f"Create job: {r.status_code}")
if r.status_code == 201:
    job_a = r.json()
    job_a_id = job_a["id"]
    print(f"Job ID: {job_a_id}")
    job_a = poll_job(job_a_id)
    print(f"Final status: {job_a['status']}")
    if job_a["status"] == "failed":
        print(f"Failure reason: {job_a.get('failure_reason', 'N/A')}")
    with open("scratchpad/synth_04_job_a.json", "w") as f:
        json.dump(job_a, f, indent=2)
    print("Job A result saved")
else:
    print(f"Failed: {r.text}")
