import uuid

from tests.fixtures import (
    make_bogus_bytes,
    make_corrupt_jpeg_bytes,
    make_geotiff_bytes,
    make_jpeg_bytes,
    make_plain_tiff_bytes,
    make_png_bytes,
)


async def _register_and_login(client, email: str, password: str = "supersecret123") -> dict:
    await client.post("/api/v1/auth/register", json={"email": email, "password": password})
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def _create_project(client, headers: dict, name: str = "Dataset Test Project") -> str:
    resp = await client.post("/api/v1/projects", json={"name": name}, headers=headers)
    return resp.json()["id"]


async def test_upload_requires_authentication(client):
    resp = await client.post(
        "/api/v1/projects/00000000-0000-0000-0000-000000000000/datasets",
        files={"file": ("test.jpg", make_jpeg_bytes(), "image/jpeg")},
    )
    assert resp.status_code == 401


async def test_upload_to_nonexistent_project_rejected(client):
    headers = await _register_and_login(client, "owner1@example.com")
    resp = await client.post(
        f"/api/v1/projects/{uuid.uuid4()}/datasets",
        files={"file": ("test.jpg", make_jpeg_bytes(), "image/jpeg")},
        headers=headers,
    )
    assert resp.status_code == 404


async def test_upload_to_other_users_project_rejected(client):
    headers_owner = await _register_and_login(client, "owner2@example.com")
    headers_intruder = await _register_and_login(client, "intruder2@example.com")
    project_id = await _create_project(client, headers_owner)

    resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets",
        files={"file": ("test.jpg", make_jpeg_bytes(), "image/jpeg")},
        headers=headers_intruder,
    )
    assert resp.status_code == 404


async def test_valid_jpeg_upload_extracts_real_metadata(client):
    headers = await _register_and_login(client, "jpeg-owner@example.com")
    project_id = await _create_project(client, headers)

    resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets",
        files={"file": ("photo.jpg", make_jpeg_bytes(64, 48), "image/jpeg")},
        headers=headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "valid"
    assert body["file_type"] == "jpeg"
    assert body["width"] == 64
    assert body["height"] == 48
    assert body["is_georeferenced"] is False
    assert body["crs"] is None
    assert body["bbox"] is None


async def test_valid_png_upload_extracts_real_metadata(client):
    headers = await _register_and_login(client, "png-owner@example.com")
    project_id = await _create_project(client, headers)

    resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets",
        files={"file": ("image.png", make_png_bytes(32, 16), "image/png")},
        headers=headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "valid"
    assert body["file_type"] == "png"
    assert body["width"] == 32
    assert body["height"] == 16
    assert body["is_georeferenced"] is False


async def test_valid_geotiff_upload_detects_real_georeferencing(client):
    headers = await _register_and_login(client, "geotiff-owner@example.com")
    project_id = await _create_project(client, headers)

    resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets",
        files={
            "file": (
                "scene.tif",
                make_geotiff_bytes(width=30, height=20, bands=2, crs="EPSG:4326"),
                "image/tiff",
            )
        },
        headers=headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "valid"
    assert body["file_type"] == "tiff"
    assert body["width"] == 30
    assert body["height"] == 20
    assert body["bands"] == 2
    assert body["is_georeferenced"] is True
    assert body["crs"] == "EPSG:4326"
    assert body["bbox"] is not None


async def test_plain_tiff_without_crs_is_not_marked_georeferenced(client):
    """A .tif extension alone must never imply georeferencing."""
    headers = await _register_and_login(client, "plaintiff-owner@example.com")
    project_id = await _create_project(client, headers)

    resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets",
        files={"file": ("plain.tif", make_plain_tiff_bytes(), "image/tiff")},
        headers=headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "valid"
    assert body["is_georeferenced"] is False
    assert body["crs"] is None
    assert body["bbox"] is None


async def test_completely_bogus_file_rejected_outright(client):
    headers = await _register_and_login(client, "bogus-owner@example.com")
    project_id = await _create_project(client, headers)

    resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets",
        files={"file": ("fake.jpg", make_bogus_bytes(), "image/jpeg")},
        headers=headers,
    )
    assert resp.status_code == 422

    list_resp = await client.get(f"/api/v1/projects/{project_id}/datasets", headers=headers)
    assert list_resp.json() == []


async def test_corrupted_file_with_valid_header_marked_invalid(client):
    headers = await _register_and_login(client, "corrupt-owner@example.com")
    project_id = await _create_project(client, headers)

    resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets",
        files={"file": ("corrupt.jpg", make_corrupt_jpeg_bytes(), "image/jpeg")},
        headers=headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "invalid"
    assert body["validation_error"] is not None
    assert body["width"] is None


async def test_dataset_list_and_detail(client):
    headers = await _register_and_login(client, "list-owner@example.com")
    project_id = await _create_project(client, headers)

    await client.post(
        f"/api/v1/projects/{project_id}/datasets",
        files={"file": ("a.jpg", make_jpeg_bytes(), "image/jpeg")},
        headers=headers,
    )
    await client.post(
        f"/api/v1/projects/{project_id}/datasets",
        files={"file": ("b.png", make_png_bytes(), "image/png")},
        headers=headers,
    )

    list_resp = await client.get(f"/api/v1/projects/{project_id}/datasets", headers=headers)
    assert list_resp.status_code == 200
    datasets = list_resp.json()
    assert len(datasets) == 2

    detail_resp = await client.get(
        f"/api/v1/projects/{project_id}/datasets/{datasets[0]['id']}", headers=headers
    )
    assert detail_resp.status_code == 200
    assert detail_resp.json()["id"] == datasets[0]["id"]


async def test_dataset_download_returns_original_bytes(client):
    headers = await _register_and_login(client, "download-owner@example.com")
    project_id = await _create_project(client, headers)
    original_bytes = make_jpeg_bytes(64, 48)

    upload_resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets",
        files={"file": ("photo.jpg", original_bytes, "image/jpeg")},
        headers=headers,
    )
    dataset_id = upload_resp.json()["id"]

    download_resp = await client.get(
        f"/api/v1/projects/{project_id}/datasets/{dataset_id}/download", headers=headers
    )
    assert download_resp.status_code == 200
    assert download_resp.content == original_bytes


async def test_another_user_cannot_access_or_download_dataset(client):
    headers_owner = await _register_and_login(client, "private-owner@example.com")
    headers_intruder = await _register_and_login(client, "private-intruder@example.com")
    project_id = await _create_project(client, headers_owner)

    upload_resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets",
        files={"file": ("secret.jpg", make_jpeg_bytes(), "image/jpeg")},
        headers=headers_owner,
    )
    dataset_id = upload_resp.json()["id"]

    get_resp = await client.get(
        f"/api/v1/projects/{project_id}/datasets/{dataset_id}", headers=headers_intruder
    )
    assert get_resp.status_code == 404

    download_resp = await client.get(
        f"/api/v1/projects/{project_id}/datasets/{dataset_id}/download",
        headers=headers_intruder,
    )
    assert download_resp.status_code == 404

    delete_resp = await client.delete(
        f"/api/v1/projects/{project_id}/datasets/{dataset_id}", headers=headers_intruder
    )
    assert delete_resp.status_code == 404


async def test_delete_dataset_removes_row_and_stored_file(client):
    from app.core.storage import get_storage

    headers = await _register_and_login(client, "delete-owner@example.com")
    project_id = await _create_project(client, headers)

    upload_resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets",
        files={"file": ("to-delete.jpg", make_jpeg_bytes(), "image/jpeg")},
        headers=headers,
    )
    dataset = upload_resp.json()
    storage = get_storage()
    storage_key = f"projects/{project_id}/datasets/{dataset['id']}/original.jpg"
    assert storage.exists(storage_key)

    delete_resp = await client.delete(
        f"/api/v1/projects/{project_id}/datasets/{dataset['id']}", headers=headers
    )
    assert delete_resp.status_code == 204
    assert not storage.exists(storage_key)

    get_resp = await client.get(
        f"/api/v1/projects/{project_id}/datasets/{dataset['id']}", headers=headers
    )
    assert get_resp.status_code == 404


async def test_deleting_project_cascades_datasets_and_storage(client):
    from app.core.storage import get_storage

    headers = await _register_and_login(client, "cascade-owner@example.com")
    project_id = await _create_project(client, headers)

    upload_resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets",
        files={"file": ("cascade.jpg", make_jpeg_bytes(), "image/jpeg")},
        headers=headers,
    )
    dataset_id = upload_resp.json()["id"]
    storage = get_storage()
    storage_key = f"projects/{project_id}/datasets/{dataset_id}/original.jpg"
    assert storage.exists(storage_key)

    delete_resp = await client.delete(f"/api/v1/projects/{project_id}", headers=headers)
    assert delete_resp.status_code == 204

    assert not storage.exists(storage_key)

    get_resp = await client.get(
        f"/api/v1/projects/{project_id}/datasets/{dataset_id}", headers=headers
    )
    assert get_resp.status_code == 404
