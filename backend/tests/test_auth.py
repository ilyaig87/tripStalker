"""Auth flow + per-user ownership isolation."""


def _register(client, email, password="supersecret"):
    return client.post("/api/auth/register", json={"email": email, "password": password})


def test_register_login_me(client):
    r = _register(client, "auth1@test.com")
    assert r.status_code == 201, r.text
    token = r.json()["access_token"]
    assert r.json()["user"]["email"] == "auth1@test.com"

    # duplicate → 409, weak password → 422, wrong login → 401, right login → 200
    assert _register(client, "auth1@test.com").status_code == 409
    assert _register(client, "weak@test.com", "short").status_code == 422
    assert client.post("/api/auth/login", json={"email": "auth1@test.com", "password": "nope"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "auth1@test.com", "password": "supersecret"}).status_code == 200

    h = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/auth/me", headers=h).json()["email"] == "auth1@test.com"
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_tracks_require_auth_and_are_isolated(client):
    h1 = {"Authorization": f"Bearer {_register(client, 'owner@test.com').json()['access_token']}"}
    h2 = {"Authorization": f"Bearer {_register(client, 'intruder@test.com').json()['access_token']}"}

    assert client.get("/api/user/tracks").status_code == 401
    assert client.get("/api/user/tracks", headers=h1).json() == []
    # A non-owner cannot see or delete a track id they don't own.
    assert client.get("/api/track/999999", headers=h2).status_code == 404
    assert client.delete("/api/track/999999", headers=h2).status_code == 404
