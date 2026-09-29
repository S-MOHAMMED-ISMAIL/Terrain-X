import requests, json, time, os

BASE = 'http://localhost:8000/api/v1'
with open('scratchpad/synth_04_state.json') as f:
    state = json.load(f)

token = state['token']
project_id = state['project_id']
headers = {'Authorization': 'Bearer ' + token}

derived = r'D:\SIH_project\TERRAIN-X-TEST-DATA\04_synthetic_complete\derived_metric_validation'

# Upload derived datasets
datasets = {}

# 1. Georeferenced RGB (same as original)
with open(os.path.join(derived, '02_georeferenced_rgb/mountain_georeferenced_rgb.tif'), 'rb') as f:
    r = requests.post(f'{BASE}/projects/{project_id}/datasets', headers=headers,
                     files={'file': ('mountain_georeferenced_rgb.tif', f, 'image/tiff')})
    print(f'Upload RGB: {r.status_code}')
    if r.status_code == 201:
        datasets['rgb'] = r.json()['id']

# 2. Reference DEM (same as original)
with open(os.path.join(derived, '03_reference_dem/terrainx_reference_dem.tif'), 'rb') as f:
    r = requests.post(f'{BASE}/projects/{project_id}/datasets', headers=headers,
                     files={'file': ('terrainx_reference_dem.tif', f, 'image/tiff')},
                     data={'role': 'dem_reference'})
    print(f'Upload DEM: {r.status_code}')
    if r.status_code == 201:
        datasets['dem'] = r.json()['id']

# 3. Derived GCP CSV (renamed columns)
with open(os.path.join(derived, '04_gcp/gcp.csv'), 'rb') as f:
    r = requests.post(f'{BASE}/projects/{project_id}/datasets', headers=headers,
                     files={'file': ('gcp.csv', f, 'text/csv')},
                     data={'role': 'gcp_reference', 'gcp_crs': 'EPSG:32643'})
    print(f'Upload GCP: {r.status_code}')
    if r.status_code == 201:
        datasets['gcp'] = r.json()['id']
        print(f'  GCP status: {r.json().get("status")}')

print(f'\nDatasets: {json.dumps(datasets, indent=2)}')

# Save state
with open('scratchpad/synth_04_derived_state.json', 'w') as f:
    json.dump({'token': token, 'project_id': project_id, 'datasets': datasets}, f)
print('State saved')