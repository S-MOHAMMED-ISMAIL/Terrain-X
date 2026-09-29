import requests, json, time, os

BASE = 'http://localhost:8000/api/v1'
with open('scratchpad/synth_04_state.json') as f:
    state = json.load(f)

token = state['token']
project_id = state['project_id']
datasets = state['datasets']
headers = {'Authorization': 'Bearer ' + token}

# Flow F: Relative terrain visualization context
print('=== FLOW F: Relative Terrain Visualization ===')
r = requests.get(f'{BASE}/projects/{project_id}/datasets/{datasets["jpeg"]}/visualization/context', headers=headers)
print(f'JPEG viz context: {r.status_code}')
if r.status_code == 200:
    ctx = r.json()
    print(f'  Terrain available: {ctx.get("terrain", {}).get("available")}')
    print(f'  Height kind: {ctx.get("terrain", {}).get("height_kind")}')
    layers = ctx.get('layers', [])
    if isinstance(layers, list):
        print(f'  Layers: {[l.get("name", l.get("type", "unknown")) if isinstance(l, dict) else str(l) for l in layers]}')
    else:
        print(f'  Layers: {list(layers.keys())}')

r = requests.get(f'{BASE}/projects/{project_id}/datasets/{datasets["rgb_tif"]}/visualization/context', headers=headers)
print(f'GeoTIFF viz context: {r.status_code}')
if r.status_code == 200:
    ctx = r.json()
    print(f'  Terrain available: {ctx.get("terrain", {}).get("available")}')
    print(f'  Height kind: {ctx.get("terrain", {}).get("height_kind")}')
    layers = ctx.get('layers', [])
    if isinstance(layers, list):
        print(f'  Layers: {[l.get("name", l.get("type", "unknown")) if isinstance(l, dict) else str(l) for l in layers]}')
    else:
        print(f'  Layers: {list(layers.keys())}')

# Flow G: Segmentation reference products
print('\n=== FLOW G: Segmentation Reference Products ===')
import rasterio
import numpy as np

data_dir = r'D:\SIH_project\TERRAIN-X-TEST-DATA\04_synthetic_complete'

with rasterio.open(os.path.join(data_dir, '06_segmentation_reference/semantic_reference_classes.tif')) as ds:
    sem_data = ds.read(1)
    sem_classes = np.unique(sem_data)
    print(f'Semantic reference classes: {sem_classes}')
    print(f'  Class count: {len(sem_classes)}')
    for c in sem_classes:
        count = np.sum(sem_data == c)
        print(f'    Class {c}: {count} pixels')

with rasterio.open(os.path.join(data_dir, '06_segmentation_reference/sky_terrain_valid_mask.tif')) as ds:
    mask_data = ds.read(1)
    mask_vals = np.unique(mask_data)
    print(f'Sky/terrain mask values: {mask_vals}')
    for v in mask_vals:
        count = np.sum(mask_data == v)
        print(f'    Value {v}: {count} pixels')

# Flow H: Quality testing
print('\n=== FLOW H: Quality Testing ===')
quality_images = [
    ('01_single_image/mountain_aerial.jpg', 'Normal'),
    ('07_quality_test/mountain_blurred.jpg', 'Blurred'),
    ('07_quality_test/mountain_overexposed.jpg', 'Overexposed'),
]

for img_path, label in quality_images:
    full_path = os.path.join(data_dir, img_path)
    with open(full_path, 'rb') as f:
        r = requests.post(f'{BASE}/projects/{project_id}/datasets', headers=headers,
                         files={'file': (os.path.basename(img_path), f, 'image/jpeg')})
        if r.status_code == 201:
            ds_id = r.json()['id']
            r2 = requests.post(f'{BASE}/projects/{project_id}/datasets/{ds_id}/analysis',
                              headers=headers, json={'parameters': {'version': 'v1'}})
            if r2.status_code == 201:
                job = r2.json()
                job_id = job['id']
                for i in range(60):
                    time.sleep(3)
                    r3 = requests.get(f'{BASE}/projects/{project_id}/analysis/{job_id}', headers=headers)
                    j = r3.json()
                    if j['status'] in ('completed', 'failed'):
                        break
                if j['status'] == 'completed':
                    iq = j.get('execution_summary', {}).get('image_quality', {})
                    sharpness = iq.get('sharpness_laplacian_variance', 0)
                    overexposed = iq.get('overexposed_fraction', 0)
                    underexposed = iq.get('underexposed_fraction', 0)
                    valid_ratio = iq.get('valid_pixel_fraction', 0)
                    print(f'  {label}: sharpness={sharpness:.2f}, overexposed={overexposed:.4f}, underexposed={underexposed:.4f}, valid_ratio={valid_ratio:.4f}')
                else:
                    print(f'  {label}: job failed - {j.get("failure_reason", "N/A")}')
            else:
                print(f'  {label}: failed to create job')
        else:
            print(f'  {label}: failed to upload')

# Flow I: Reference-output comparison
print('\n=== FLOW I: Reference-Output Comparison ===')
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

from scipy import stats
valid_mask = rd_data > 0
if valid_mask.sum() > 0:
    corr, _ = stats.pearsonr(rd_data[valid_mask], dem_data[valid_mask])
    print(f'Correlation (relative_depth vs DEM): r={corr:.4f}')
    spearman, _ = stats.spearmanr(rd_data[valid_mask], dem_data[valid_mask])
    print(f'Spearman correlation: rho={spearman:.4f}')

print('\n=== ALL FLOWS COMPLETE ===')