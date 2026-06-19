"""Tests for the read-only GET /repositories endpoint."""


def test_list_repositories_includes_seed(client):
    response = client.get("/repositories")
    assert response.status_code == 200
    repos = response.json()
    ids = {r["repository_id"] for r in repos}
    assert "agentops-core-main" in ids
    seeded = next(r for r in repos if r["repository_id"] == "agentops-core-main")
    assert seeded["project_id"] == "agentops-core"
    assert seeded["remote_url"] and "github.com" in seeded["remote_url"]
