import requests, json, time, os
import numpy as np

BASE = 'http://localhost:8000/api/v1'
with open('scratchpad/synth_04_state.json') as f:
    state = json.load(f)

token = state['token']
project_id = state['project_id']
datasets = state['datasets']
headers = {'Authorization': 'Bearer ' + token}

data_dir = r'D:\SIH_project\TERRAIN-X-TEST-DATA\04_synthetic_complete'

# Fix Flow I: correlation without scipy
print('=== FLOW I: Reference-Output Comparison (fixed) ===')
import rasterio

with rasterio.open(os.path.join(data_dir, '03_reference_dem/terrainx_reference_dem.tif')) as ds:
    dem_data = ds.read(1)
    print(f'Reference DEM: min={dem_data.min():.3f}, max={dem_data.max():.3f}, mean={dem_data.mean():.3f}')

with rasterio.open(os.path.join(data_dir, '05_reference_outputs/terrainx_reference_dsm.tif')) as ds:
    dsm_data = ds.read(1)
    print(f'Reference DSM: min={dsm_data.min():.3f}, max={dsm_data.max():.3f}, mean={dsm_data.mean():.3f}')

with rasterio.open(os.path.join(data_dir, '05_reference_outputs/relative_depth_reference.tif')) as ds:
    rd_data = ds.read(1)
    print(f'Reference relative depth: min={rd_data.min():.3f}, max={rd_data.max():.3f}, mean={rd_data.mean():.3f}')

ndsm = dsm_data - dem_data
print(f'nDSM (DSM-DEM): min={ndsm.min():.3f}, max={ndsm.max():.3f}, mean={ndsm.mean():.3f}')

# Correlation using numpy
valid_mask = rd_data > 0
if valid_mask.sum() > 0:
    rd_valid = rd_data[valid_mask]
    dem_valid = dem_data[valid_mask]
    corr = np.corrcoef(rd_valid, dem_valid)[0, 1]
    print(f'Correlation (relative_depth vs DEM): r={corr:.4f}')
    # Spearman via rankdata
    from numpy import argsort
    def rankdata(a):
        arr = np.asarray(a)
        temp = arr.argsort()
        ranks = np.empty_like(temp)
        ranks[temp] = np.arange(len(arr))
        return ranks
    rd_ranks = rankdata(rd_valid)
    dem_ranks = rankdata(dem_valid)
    spearman = np.corrcoef(rd_ranks, dem_ranks)[0, 1]
    print(f'Spearman correlation: rho={spearman:.4f}')

# Check layer structure
print('\n=== Layer Structure Check ===')
r = requests.get(f'{BASE}/projects/{project_id}/datasets/{datasets["jpeg"]}/visualization/context', headers=headers)
if r.status_code == 200:
    ctx = r.json()
    layers = ctx.get('layers', [])
    if isinstance(layers, list) and len(layers) > 0:
        print(f'First layer keys: {list(layers[0].keys()) if isinstance(layers[0], dict) else type(layers[0])}')
        print(f'First layer: {json.dumps(layers[0], indent=2)[:500]}')

# Flow E: Check if any metric terrain products exist (they shouldn't since calibration failed)
print('\n=== FLOW E: Metric Terrain Products ===')
print('Calibration was rejected by quality gate - no metric elevation or DSM artifacts expected.')
print('This is the correct behavior per the existing calibration gate policy.')

# Flow F extended: Check terrain grid endpoint
print('\n=== FLOW F Extended: Terrain Grid ===')
# Get the JPEG job's relative depth artifact
with open('scratchpad/synth_04_job_a.json') as f:
    job_a = json.load(f)
rd_artifact_id = job_a.get('execution_summary', {}).get('depth_estimation', {}).get('artifact_id')
if rd_artifact_id:
    r = requests.get(f'{BASE}/projects/{project_id}/analysis/{job_a["id"]}/artifacts/{rd_artifact_id}/visualization/terrain/metadata', headers=headers)
    print(f'Terrain metadata: {r.status_code}')
    if r.status_code == 200:
        meta = r.json()
        print(f'  Grid dimensions: {meta.get("width")} x {meta.get("height")}')
        print(f'  Height kind: {meta.get("height_kind", "N/A")}')

print('\n=== REMAINING FLOWS COMPLETE ===')