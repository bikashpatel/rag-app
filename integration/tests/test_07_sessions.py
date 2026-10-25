"""Flow 7: sessions are signed cookies checked against users.token_version in Postgres: a password change, an admin
reset or deactivation ends the other sessions, and "log out other sessions" ends every session but the caller's."""

from harness import ENV, check, client


def _me(session) -> int:
    return session.get("/api/auth/me").status_code


def test_password_change_ends_other_sessions(make_user) -> None:
    user = make_user("password")
    second = user.login_again()
    assert _me(user.client) == _me(second) == 200
    new_password = user.password + "-changed"
    body = {"current_password": user.password, "new_password": new_password}
    check(user.client.post("/api/auth/change-password", json=body), 204)
    assert _me(user.client) == 200, "the caller keeps its re-issued session"
    assert _me(second) == 401
    old = client().post("/api/auth/login", json={"username": user.username, "password": user.password})
    assert old.status_code == 401
    fresh = client()
    check(fresh.post("/api/auth/login", json={"username": user.username, "password": new_password}))
    assert _me(fresh) == 200


def test_wrong_current_password_is_refused(make_user) -> None:
    user = make_user("password")
    body = {"current_password": "not-my-password", "new_password": "whatever-new-1"}
    assert user.client.post("/api/auth/change-password", json=body).status_code == 400
    assert _me(user.client) == 200


def test_admin_password_reset_ends_sessions(make_user, admin) -> None:
    user = make_user("reset")
    check(admin.patch(f"/api/admin/users/{user.id}", json={"password": "reset-by-admin-1"}))
    assert _me(user.client) == 401
    check(client().post("/api/auth/login", json={"username": user.username, "password": "reset-by-admin-1"}))


def test_deactivation_ends_sessions_and_blocks_login(make_user, admin) -> None:
    user = make_user("inactive")
    check(admin.patch(f"/api/admin/users/{user.id}", json={"is_active": False}))
    assert _me(user.client) == 401
    assert user.client.get("/api/documents").status_code == 401
    refused = client().post("/api/auth/login", json={"username": user.username, "password": user.password})
    assert refused.status_code == 401 and refused.json()["detail"] == "Invalid username or password"
    check(admin.patch(f"/api/admin/users/{user.id}", json={"is_active": True}))
    assert _me(user.client) == 401, "an old session stays ended after reactivation"
    assert _me(user.login_again()) == 200


def test_logout_other_sessions(make_user) -> None:
    user = make_user("others")
    second, third = user.login_again(), user.login_again()
    check(user.client.post("/api/auth/logout-others"), 204)
    assert _me(user.client) == 200
    assert _me(second) == _me(third) == 401
    account = check(user.client.get("/api/me/account")).json()
    assert len(account["recent_logins"]) == 3
    assert sum(1 for login in account["recent_logins"] if login["current"]) == 1


def test_logout_clears_the_cookie(make_user) -> None:
    user = make_user("logout")
    check(user.client.post("/api/auth/logout"), 204)
    assert _me(user.client) == 401


def test_env_admin_password_cannot_be_changed_through_the_api(admin) -> None:
    body = {"current_password": ENV["ADMIN_PASSWORD"], "new_password": "something-else-1"}
    response = admin.post("/api/auth/change-password", json=body)
    assert response.status_code == 400 and response.json()["detail"] == "The admin password is managed in .env"
    me = check(admin.get("/api/auth/me")).json()
    users = check(admin.get("/api/admin/users")).json()
    admin_id = next(user["id"] for user in users if user["username"] == me["username"])
    assert admin.patch(f"/api/admin/users/{admin_id}", json={"is_active": False}).status_code == 400
    assert admin.delete(f"/api/admin/users/{admin_id}").status_code == 400
    assert _me(admin) == 200
