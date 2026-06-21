"""Tests for the GET /repositories and POST /repositories endpoints."""


def test_list_repositories_includes_seed(client):
    response = client.get("/repositories")
    assert response.status_code == 200
    repos = response.json()
    ids = {r["repository_id"] for r in repos}
    assert "agentops-core-main" in ids
    seeded = next(r for r in repos if r["repository_id"] == "agentops-core-main")
    assert seeded["project_id"] == "agentops-core"
    assert seeded["remote_url"] and "github.com" in seeded["remote_url"]


def test_create_repository_creates_row(client):
    body = {
        "repository_id": "pytest-repo-create",
        "project_id": "agentops-core",
        "repository_name": "Pytest Repo",
        "remote_url": "https://github.com/acme/pytest-repo.git",
    }
    resp = client.post("/repositories", json=body)
    assert resp.status_code == 201
    out = resp.json()
    assert out["repository_id"] == "pytest-repo-create"
    assert out["project_id"] == "agentops-core"
    listed = {r["repository_id"] for r in client.get("/repositories").json()}
    assert "pytest-repo-create" in listed


def test_create_repository_unknown_project_is_400(client):
    body = {
        "repository_id": "pytest-repo-badproj",
        "project_id": "does-not-exist",
        "repository_name": "X",
    }
    resp = client.post("/repositories", json=body)
    assert resp.status_code == 400


def test_create_repository_idempotent_upsert(client):
    body = {
        "repository_id": "pytest-repo-upsert",
        "project_id": "agentops-core",
        "repository_name": "First Name",
        "remote_url": "https://github.com/acme/first.git",
    }
    first = client.post("/repositories", json=body)
    assert first.status_code == 201
    body["repository_name"] = "Second Name"
    body["remote_url"] = "https://github.com/acme/second.git"
    second = client.post("/repositories", json=body)
    assert second.status_code == 200
    # Proves the ON CONFLICT UPDATE path wrote the new value, not just avoided a duplicate.
    assert second.json()["remote_url"] == "https://github.com/acme/second.git"
    rows = [
        r for r in client.get("/repositories").json() if r["repository_id"] == "pytest-repo-upsert"
    ]
    assert len(rows) == 1
