import requests, json
BASE = "http://localhost:8000/api/v1"
with open("scratchpad/synth_04_state.json") as f:
    state = json.load(f)
token = state["token"]
project_id = state["project_id"]
datasets = state["datasets"]
headers = {"Authorization": f"Bearer {token}"}

# Check GCP dataset status
r = requests.get(f"{BASE}/projects/{project_id}/datasets/{datasets['gcp']}", headers=headers)
print(f"GCP dataset status code: {r.status_code}")
print(r.text[:2000])
