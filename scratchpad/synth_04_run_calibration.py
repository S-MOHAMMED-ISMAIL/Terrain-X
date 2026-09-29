import requests, json, time, os

BASE = 'http://localhost:8000/api/v1'
with open('scratchpad/synth_04_derived_state.json') as f:
    state = json.load(f)

token = state['token']
project_id = state['project_id']
datasets = state['datasets']
headers = {'Authorization': 'Bearer ' + token}

def poll_job(job_id, max_polls=120, interval=5):
    for i in range(max_polls):
        time.sleep(interval)
        r = requests.get(f'{BASE}/projects/{project_id}/analysis/{job_id}', headers=headers)
        job = r.json()
        status = job['status']
        stage = job.get('current_stage', 'N/A')
        print(f'  Poll {i+1}: status={status}, stage={stage}')
        if status in ('completed', 'failed', 'cancelled'):
            return job
    return job

# Flow C: RGB + DEM -> calibration (using derived fixture)
print('=== DERIVED FIXTURE: RGB + DEM Calibration ===')
r = requests.post(f'{BASE}/projects/{project_id}/datasets/{datasets["rgb"]}/analysis',
                   headers=headers, json={'parameters': {'version': 'v1', 'dem_reference_dataset_id': datasets['dem']}})
print(f'Create job: {r.status_code}')
if r.status_code == 201:
    job = r.json()
    job_id = job['id']
    print(f'Job ID: {job_id}')
    job = poll_job(job_id)
    print(f'Final status: {job["status"]}')
    print(f'Calibration status: {job.get("calibration_status", "N/A")}')
    
    if job.get('calibration_metadata'):
        cm = job['calibration_metadata']
        print(f'  Scale: {cm.get("scale_a")}, Offset: {cm.get("offset_b")}')
        print(f'  Valid samples: {cm.get("valid_samples")}')
        print(f'  Inlier samples: {cm.get("inlier_samples")}')
        print(f'  Outlier samples: {cm.get("outlier_samples")}')
        print(f'  MAE: {cm.get("validation_metrics", {}).get("mae")}')
        print(f'  RMSE: {cm.get("validation_metrics", {}).get("rmse")}')
        print(f'  Bias: {cm.get("validation_metrics", {}).get("bias")}')
        
        # Check quality gate
        qg = cm.get('quality_gate', {})
        print(f'  Quality gate passed: {qg.get("passed")}')
        if not qg.get('passed'):
            print(f'  Failed criteria: {qg.get("failed_criteria")}')
        
        # Check cross-validation
        cv = cm.get('cross_validation', {})
        print(f'  CV skill: {cv.get("skill")}')
        print(f'  CV feasible: {cv.get("feasible")}')
    
    if job['status'] == 'failed':
        print(f'Failure reason: {job.get("failure_reason", "N/A")}')
    
    with open('scratchpad/synth_04_derived_job_c.json', 'w') as f:
        json.dump(job, f, indent=2)
    print('Job result saved')
    
    # Check artifacts
    r = requests.get(f'{BASE}/projects/{project_id}/analysis/{job_id}/artifacts', headers=headers)
    if r.status_code == 200:
        artifacts = r.json()
        print(f'\nArtifacts ({len(artifacts)}):')
        for a in artifacts:
            print(f'  {a["artifact_type"]}: {a["id"]} ({a["file_size_bytes"]} bytes)')
else:
    print(f'Failed: {r.text}')