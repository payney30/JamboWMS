"""
Tests for the two hardening changes: login rate limiting/lockout
(app/rate_limit.py) and restricted CORS (app/main.py).
"""
from app import rate_limit


def test_lockout_after_max_failed_attempts(client, admin_user):
    for _ in range(rate_limit.EMAIL_MAX_ATTEMPTS):
        resp = client.post(
            "/auth/login", data={"username": admin_user.email, "password": "wrong"}
        )
        assert resp.status_code == 401

    # one more attempt — even with the correct password — should now be
    # locked out, not merely rejected for bad credentials
    locked_resp = client.post(
        "/auth/login", data={"username": admin_user.email, "password": "test-password"}
    )
    assert locked_resp.status_code == 429
    assert "Retry-After" in locked_resp.headers


def test_successful_login_before_lockout_still_works(client, admin_user):
    for _ in range(rate_limit.EMAIL_MAX_ATTEMPTS - 1):
        resp = client.post(
            "/auth/login", data={"username": admin_user.email, "password": "wrong"}
        )
        assert resp.status_code == 401

    # one attempt short of the lockout threshold — correct password should
    # still succeed
    ok_resp = client.post(
        "/auth/login", data={"username": admin_user.email, "password": "test-password"}
    )
    assert ok_resp.status_code == 200


def test_successful_login_clears_failure_count(client, admin_user):
    for _ in range(rate_limit.EMAIL_MAX_ATTEMPTS - 1):
        client.post("/auth/login", data={"username": admin_user.email, "password": "wrong"})

    ok_resp = client.post(
        "/auth/login", data={"username": admin_user.email, "password": "test-password"}
    )
    assert ok_resp.status_code == 200

    # failure count should have reset — this shouldn't trip the lockout
    # even though we're now at (MAX_ATTEMPTS - 1) + 1 total prior failures
    another_ok = client.post(
        "/auth/login", data={"username": admin_user.email, "password": "test-password"}
    )
    assert another_ok.status_code == 200


def test_lockout_is_scoped_to_one_email(client, admin_user, loc_user):
    for _ in range(rate_limit.EMAIL_MAX_ATTEMPTS):
        client.post("/auth/login", data={"username": admin_user.email, "password": "wrong"})

    locked = client.post(
        "/auth/login", data={"username": admin_user.email, "password": "test-password"}
    )
    assert locked.status_code == 429

    # a different account, same client, is unaffected
    other_ok = client.post(
        "/auth/login", data={"username": loc_user.email, "password": "test-password"}
    )
    assert other_ok.status_code == 200


def test_lockout_check_precedes_credential_check(client, admin_user):
    """
    Once locked out, even attempts with a bad password must return 429,
    not 401 — otherwise the response code itself would leak whether the
    account is currently locked vs. just being guessed correctly.
    """
    for _ in range(rate_limit.EMAIL_MAX_ATTEMPTS):
        client.post("/auth/login", data={"username": admin_user.email, "password": "wrong"})

    still_wrong = client.post(
        "/auth/login", data={"username": admin_user.email, "password": "still-wrong"}
    )
    assert still_wrong.status_code == 429


def test_cors_headers_absent_by_default_for_cross_origin_request(client, admin_user):
    """
    CORS_ALLOWED_ORIGINS is unset in the test environment, so no origin
    should receive Access-Control-Allow-Origin — matching the "same-origin
    only by default" posture.
    """
    resp = client.post(
        "/auth/login",
        data={"username": admin_user.email, "password": "test-password"},
        headers={"Origin": "https://evil.example.com"},
    )
    assert "access-control-allow-origin" not in {k.lower() for k in resp.headers.keys()}
