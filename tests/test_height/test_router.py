"""Router-level tests for /api/height provider exposure."""


def test_height_sources_excludes_shadow_provider(client):
    resp = client.post(
        "/api/height/sources",
        json={
            "north": 37.19,
            "south": 37.16,
            "east": -3.58,
            "west": -3.63,
        },
    )
    assert resp.status_code == 200
    names = {p["name"] for p in resp.json().get("providers", [])}
    assert "shadow_height" not in names
