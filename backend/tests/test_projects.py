async def _register_and_login(client, email: str, password: str = "supersecret123") -> dict:
    await client.post("/api/v1/auth/register", json={"email": email, "password": password})
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def test_project_crud_and_ownership(client):
    headers_owner = await _register_and_login(client, "owner@example.com")
    headers_intruder = await _register_and_login(client, "intruder@example.com")

    create_resp = await client.post(
        "/api/v1/projects",
        json={"name": "Flood Study", "description": "coastal test area"},
        headers=headers_owner,
    )
    assert create_resp.status_code == 201
    project_id = create_resp.json()["id"]

    list_resp = await client.get("/api/v1/projects", headers=headers_owner)
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1

    get_resp = await client.get(f"/api/v1/projects/{project_id}", headers=headers_owner)
    assert get_resp.status_code == 200

    # IDOR prevention: another authenticated user gets 404, not 403 or the data.
    intruder_get = await client.get(f"/api/v1/projects/{project_id}", headers=headers_intruder)
    assert intruder_get.status_code == 404

    intruder_patch = await client.patch(
        f"/api/v1/projects/{project_id}", json={"name": "hacked"}, headers=headers_intruder
    )
    assert intruder_patch.status_code == 404

    intruder_delete = await client.delete(
        f"/api/v1/projects/{project_id}", headers=headers_intruder
    )
    assert intruder_delete.status_code == 404

    patch_resp = await client.patch(
        f"/api/v1/projects/{project_id}", json={"name": "Updated"}, headers=headers_owner
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["name"] == "Updated"

    delete_resp = await client.delete(f"/api/v1/projects/{project_id}", headers=headers_owner)
    assert delete_resp.status_code == 204

    missing_resp = await client.get(f"/api/v1/projects/{project_id}", headers=headers_owner)
    assert missing_resp.status_code == 404

    unauth_resp = await client.get("/api/v1/projects")
    assert unauth_resp.status_code == 401
