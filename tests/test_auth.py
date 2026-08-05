import io
import json
import urllib.error

import pytest

from ohme_log_viewer.auth import (
    AUTH_COMMAND,
    STATUS_COMMAND,
    AuthError,
    BackendSigner,
    Credentials,
    DeviceAuthenticator,
    build_jwt,
    load_credentials,
    parse_auth_challenge,
)

CHALLENGE = "a3f1c8" + "00" * 13
SIGNATURE_B64 = "MEUCIQD+Zm9jaw=="


# ── fakes ────────────────────────────────────────────────────────────────


class FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeHttp:
    """Records requests and replies from a per-URL-substring script."""

    def __init__(self, routes):
        self._routes = routes
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        for fragment, reply in self._routes.items():
            if fragment in request.full_url:
                if isinstance(reply, list):
                    reply = reply.pop(0)
                if isinstance(reply, Exception):
                    raise reply
                return FakeResponse(reply)
        raise AssertionError(f"unexpected request to {request.full_url}")

    def urls(self):
        return [request.full_url for request in self.requests]

    def body(self, index):
        return json.loads(self.requests[index].data.decode())


class FakeLink:
    """Serial link that answers each written command from a script."""

    def __init__(self, replies=None):
        self.written: list[str] = []
        self._replies = dict(replies or {})
        self._queued: list[str | None] = []

    def write_line(self, text):
        self.written.append(text)
        verb = text.split(" ", 1)[0]
        self._queued.extend(self._replies.get(verb, []))

    def read_line(self):
        if not self._queued:
            return None
        return self._queued.pop(0)


def ok_status(charger_id="OHME-GB-000123"):
    return [json.dumps({"result": "ok", "charger_id": charger_id, "authenticated": False})]


def signer_for(challenge=CHALLENGE, jwt="header.payload.signature"):
    class StubSigner:
        def __init__(self):
            self.calls = []

        def sign_challenge(self, challenge_hex, charger_id):
            self.calls.append((challenge_hex, charger_id))
            return jwt

    return StubSigner()


# ── challenge detection ──────────────────────────────────────────────────


def test_auth_required_line_yields_its_challenge():
    line = json.dumps({"result": "auth_required", "challenge": CHALLENGE})
    assert parse_auth_challenge(line) == CHALLENGE


def test_a_prefix_before_the_json_does_not_hide_the_challenge():
    line = 'Type / for the menu {"result":"auth_required","challenge":"' + CHALLENGE + '"}'
    assert parse_auth_challenge(line) == CHALLENGE


def test_an_ordinary_log_line_is_not_a_challenge():
    assert parse_auth_challenge("I/MODEM [2026-07-01T10:11:11.950Z] Registered") is None


def test_another_json_result_is_not_a_challenge():
    assert parse_auth_challenge('{"result":"ok"}') is None


def test_auth_required_without_a_challenge_field_is_rejected():
    assert parse_auth_challenge('{"result":"auth_required"}') is None


# ── credentials ──────────────────────────────────────────────────────────


def write_config(directory, be_url="https://api-dev2.ohme.io", email="qa@ohme-ev.com"):
    path = directory / "config.toml"
    path.write_text(
        "[app]\n" f'be_url = "{be_url}"\n' f'email = "{email}"\n' 'password = "s3cret"\n'
    )
    return path


def test_credentials_come_from_the_device_cli_config(tmp_path):
    write_config(tmp_path)
    creds = load_credentials(config_dir=tmp_path, env={})
    assert creds == Credentials(
        be_url="https://api-dev2.ohme.io", email="qa@ohme-ev.com", password="s3cret"
    )


def test_the_environment_overrides_the_config_file(tmp_path):
    write_config(tmp_path)
    creds = load_credentials(
        config_dir=tmp_path,
        env={"OHME_BE_URL": "https://api-qa1.ohme.io", "OHME_EMAIL": "other@ohme-ev.com"},
    )
    assert creds.be_url == "https://api-qa1.ohme.io"
    assert creds.email == "other@ohme-ev.com"
    assert creds.password == "s3cret"


def test_missing_credentials_are_named_along_with_the_config_path(tmp_path):
    (tmp_path / "config.toml").write_text('[app]\nbe_url = "https://api-dev2.ohme.io"\n')
    with pytest.raises(AuthError) as excinfo:
        load_credentials(config_dir=tmp_path, env={})
    message = str(excinfo.value)
    assert "email" in message and "password" in message
    assert "be_url" not in message
    assert str(tmp_path / "config.toml") in message


def test_an_absent_config_file_is_reported_rather_than_raised_as_oserror(tmp_path):
    with pytest.raises(AuthError) as excinfo:
        load_credentials(config_dir=tmp_path, env={})
    assert str(tmp_path / "config.toml") in str(excinfo.value)


# ── JWT assembly ─────────────────────────────────────────────────────────


def test_the_jwt_payload_carries_the_challenge_as_its_nonce():
    import base64

    _, payload, _ = build_jwt(CHALLENGE, SIGNATURE_B64).split(".")
    decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    assert json.loads(decoded) == {"nonce": CHALLENGE}


def test_the_jwt_signature_part_is_the_der_signature_base64url_encoded():
    import base64

    _, _, signature = build_jwt(CHALLENGE, SIGNATURE_B64).split(".")
    decoded = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
    assert decoded == base64.b64decode(SIGNATURE_B64 + "==")


def test_the_jwt_header_declares_es256():
    import base64

    header, _, _ = build_jwt(CHALLENGE, SIGNATURE_B64).split(".")
    decoded = base64.urlsafe_b64decode(header + "=" * (-len(header) % 4))
    assert json.loads(decoded) == {"alg": "ES256", "typ": "JWT"}


# ── backend signer ───────────────────────────────────────────────────────


def cache_file(tmp_path, email="qa@ohme-ev.com", expiry=10_000.0):
    path = tmp_path / "firebase_cache.json"
    path.write_text(
        json.dumps(
            {
                "email": email,
                "id_token": "cached-id-token",
                "refresh_token": "cached-refresh-token",
                "expiry": expiry,
            }
        )
    )
    return path


def make_signer(tmp_path, http, now=1_000.0, **kwargs):
    return BackendSigner(
        Credentials(be_url="https://api-dev2.ohme.io", email="qa@ohme-ev.com", password="s3cret"),
        urlopen=http,
        now=lambda: now,
        cache_path=tmp_path / "firebase_cache.json",
        **kwargs,
    )


def test_signing_posts_the_challenge_to_the_charger_sign_endpoint(tmp_path):
    cache_file(tmp_path)
    http = FakeHttp({"/hardware/qa/sign": {"signature": SIGNATURE_B64}})
    jwt = make_signer(tmp_path, http).sign_challenge(CHALLENGE, "OHME-GB-000123")

    assert http.urls() == [
        "https://api-dev2.ohme.io/v2/charge-devices/OHME-GB-000123/hardware/qa/sign"
    ]
    assert http.body(0) == {"challenge": CHALLENGE}
    assert jwt == build_jwt(CHALLENGE, SIGNATURE_B64)


def test_signing_authenticates_with_the_cached_firebase_token(tmp_path):
    cache_file(tmp_path)
    http = FakeHttp({"/hardware/qa/sign": {"signature": SIGNATURE_B64}})
    make_signer(tmp_path, http).sign_challenge(CHALLENGE, "OHME-GB-000123")
    assert http.requests[0].get_header("Authorization") == "Firebase cached-id-token"


def test_a_cache_for_a_different_account_is_ignored(tmp_path):
    cache_file(tmp_path, email="someone-else@ohme-ev.com")
    http = FakeHttp(
        {
            "signInWithPassword": {"idToken": "fresh", "refreshToken": "r", "expiresIn": "3600"},
            "/hardware/qa/sign": {"signature": SIGNATURE_B64},
        }
    )
    make_signer(tmp_path, http).sign_challenge(CHALLENGE, "OHME-GB-000123")
    assert "signInWithPassword" in http.urls()[0]
    assert http.requests[1].get_header("Authorization") == "Firebase fresh"


def test_signing_in_uses_the_configured_email_and_password(tmp_path):
    http = FakeHttp(
        {
            "signInWithPassword": {"idToken": "fresh", "refreshToken": "r", "expiresIn": "3600"},
            "/hardware/qa/sign": {"signature": SIGNATURE_B64},
        }
    )
    make_signer(tmp_path, http).sign_challenge(CHALLENGE, "OHME-GB-000123")
    assert http.body(0) == {
        "email": "qa@ohme-ev.com",
        "password": "s3cret",
        "returnSecureToken": True,
    }


def test_a_second_signature_reuses_the_token_it_already_holds(tmp_path):
    http = FakeHttp(
        {
            "signInWithPassword": {"idToken": "fresh", "refreshToken": "r", "expiresIn": "3600"},
            "/hardware/qa/sign": [
                {"signature": SIGNATURE_B64},
                {"signature": SIGNATURE_B64},
            ],
        }
    )
    signer = make_signer(tmp_path, http)
    signer.sign_challenge(CHALLENGE, "OHME-GB-000123")
    signer.sign_challenge(CHALLENGE, "OHME-GB-000123")
    assert sum("signInWithPassword" in url for url in http.urls()) == 1


def test_an_expired_cached_token_is_refreshed_rather_than_re_signed_in(tmp_path):
    cache_file(tmp_path, expiry=500.0)
    http = FakeHttp(
        {
            "securetoken": {"id_token": "refreshed", "refresh_token": "r2", "expires_in": "3600"},
            "/hardware/qa/sign": {"signature": SIGNATURE_B64},
        }
    )
    make_signer(tmp_path, http, now=1_000.0).sign_challenge(CHALLENGE, "OHME-GB-000123")
    assert http.body(0) == {"grant_type": "refresh_token", "refresh_token": "cached-refresh-token"}
    assert http.requests[1].get_header("Authorization") == "Firebase refreshed"


def test_a_failed_refresh_falls_back_to_a_full_sign_in(tmp_path):
    cache_file(tmp_path, expiry=500.0)
    http = FakeHttp(
        {
            "securetoken": urllib.error.HTTPError(
                "https://securetoken.googleapis.com/v1/token", 400, "Bad", {}, io.BytesIO(b"{}")
            ),
            "signInWithPassword": {"idToken": "fresh", "refreshToken": "r", "expiresIn": "3600"},
            "/hardware/qa/sign": {"signature": SIGNATURE_B64},
        }
    )
    make_signer(tmp_path, http, now=1_000.0).sign_challenge(CHALLENGE, "OHME-GB-000123")
    assert http.requests[2].get_header("Authorization") == "Firebase fresh"


def test_a_dev_backend_uses_the_dev_firebase_key(tmp_path):
    http = FakeHttp(
        {
            "signInWithPassword": {"idToken": "fresh", "refreshToken": "r", "expiresIn": "3600"},
            "/hardware/qa/sign": {"signature": SIGNATURE_B64},
        }
    )
    make_signer(tmp_path, http).sign_challenge(CHALLENGE, "OHME-GB-000123")
    assert "AIzaSyAXNbieB6V6MsYQwvEeGMVVigmHTtv7TMo" in http.urls()[0]


def test_a_production_backend_uses_the_production_firebase_key(tmp_path):
    http = FakeHttp(
        {
            "signInWithPassword": {"idToken": "fresh", "refreshToken": "r", "expiresIn": "3600"},
            "/hardware/qa/sign": {"signature": SIGNATURE_B64},
        }
    )
    signer = BackendSigner(
        Credentials(be_url="https://api.ohme.io", email="qa@ohme-ev.com", password="s3cret"),
        urlopen=http,
        now=lambda: 1_000.0,
        cache_path=tmp_path / "firebase_cache.json",
    )
    signer.sign_challenge(CHALLENGE, "OHME-GB-000123")
    assert "AIzaSyD7LkU-gNjjW2w06aiDIfYliAf0q3I15pQ" in http.urls()[0]


def test_a_signature_response_without_a_signature_is_an_auth_error(tmp_path):
    cache_file(tmp_path)
    http = FakeHttp({"/hardware/qa/sign": {"msg": "no such charger"}})
    with pytest.raises(AuthError, match="signature"):
        make_signer(tmp_path, http).sign_challenge(CHALLENGE, "OHME-GB-000123")


def test_a_rejected_sign_request_reports_the_status_and_body(tmp_path):
    cache_file(tmp_path)
    http = FakeHttp(
        {
            "/hardware/qa/sign": urllib.error.HTTPError(
                "https://api-dev2.ohme.io/x", 403, "Forbidden", {}, io.BytesIO(b"not your charger")
            )
        }
    )
    with pytest.raises(AuthError) as excinfo:
        make_signer(tmp_path, http).sign_challenge(CHALLENGE, "OHME-GB-000123")
    assert "403" in str(excinfo.value)
    assert "not your charger" in str(excinfo.value)


def test_a_sign_in_failure_is_reported_as_an_auth_error(tmp_path):
    http = FakeHttp(
        {
            "signInWithPassword": urllib.error.HTTPError(
                "https://identitytoolkit.googleapis.com/x",
                400,
                "Bad Request",
                {},
                io.BytesIO(b'{"error":{"message":"INVALID_PASSWORD"}}'),
            )
        }
    )
    with pytest.raises(AuthError, match="INVALID_PASSWORD"):
        make_signer(tmp_path, http).sign_challenge(CHALLENGE, "OHME-GB-000123")


def test_an_unreadable_cache_file_does_not_stop_a_sign_in(tmp_path):
    (tmp_path / "firebase_cache.json").write_text("{ this is not json")
    http = FakeHttp(
        {
            "signInWithPassword": {"idToken": "fresh", "refreshToken": "r", "expiresIn": "3600"},
            "/hardware/qa/sign": {"signature": SIGNATURE_B64},
        }
    )
    make_signer(tmp_path, http).sign_challenge(CHALLENGE, "OHME-GB-000123")
    assert http.requests[1].get_header("Authorization") == "Firebase fresh"


# ── device handshake ─────────────────────────────────────────────────────


def test_the_handshake_asks_the_device_for_its_charger_id():
    link = FakeLink({STATUS_COMMAND: ok_status(), AUTH_COMMAND: ['{"result":"ok"}']})
    signer = signer_for()
    DeviceAuthenticator(signer=signer).authenticate(link, CHALLENGE, lambda _line: None)
    assert link.written[0] == STATUS_COMMAND
    assert signer.calls == [(CHALLENGE, "OHME-GB-000123")]


def test_the_handshake_sends_the_signed_jwt_and_accepts_the_devices_ok():
    link = FakeLink({STATUS_COMMAND: ok_status(), AUTH_COMMAND: ['{"result":"ok"}']})
    DeviceAuthenticator(signer=signer_for(jwt="a.b.c")).authenticate(
        link, CHALLENGE, lambda _line: None
    )
    assert link.written[1] == f"{AUTH_COMMAND} a.b.c"


def test_the_charger_id_is_queried_once_per_authenticator():
    link = FakeLink(
        {
            STATUS_COMMAND: ok_status(),
            AUTH_COMMAND: ['{"result":"ok"}', '{"result":"ok"}'],
        }
    )
    auth = DeviceAuthenticator(signer=signer_for())
    auth.authenticate(link, CHALLENGE, lambda _line: None)
    auth.authenticate(link, CHALLENGE, lambda _line: None)
    assert link.written.count(STATUS_COMMAND) == 1


def test_a_rejected_jwt_raises_with_the_devices_reason():
    link = FakeLink(
        {
            STATUS_COMMAND: ok_status(),
            AUTH_COMMAND: ['{"result":"error","msg":"nonce_mismatch"}'],
        }
    )
    with pytest.raises(AuthError, match="nonce_mismatch"):
        DeviceAuthenticator(signer=signer_for()).authenticate(link, CHALLENGE, lambda _line: None)


def test_an_unprovisioned_device_cannot_be_authenticated():
    link = FakeLink({STATUS_COMMAND: ok_status(charger_id="")})
    with pytest.raises(AuthError, match="charger_id"):
        DeviceAuthenticator(signer=signer_for()).authenticate(link, CHALLENGE, lambda _line: None)


def test_lines_arriving_during_the_handshake_are_not_swallowed():
    link = FakeLink(
        {
            STATUS_COMMAND: ["I/MODEM [t] still alive"] + ok_status(),
            AUTH_COMMAND: ['{"result":"ok"}'],
        }
    )
    emitted: list[str] = []
    DeviceAuthenticator(signer=signer_for()).authenticate(link, CHALLENGE, emitted.append)
    assert "I/MODEM [t] still alive" in emitted


def test_a_stop_request_abandons_the_handshake():
    """Disconnect must not wait out the device's handshake timeouts."""
    link = FakeLink({STATUS_COMMAND: ok_status(), AUTH_COMMAND: []})  # never answers
    reads = {"n": 0}

    def should_stop():
        reads["n"] += 1
        return reads["n"] > 3

    authenticated = DeviceAuthenticator(signer=signer_for()).authenticate(
        link, CHALLENGE, lambda _line: None, should_stop
    )
    assert authenticated is False


def test_a_stop_request_before_the_charger_id_arrives_abandons_the_handshake():
    link = FakeLink()  # answers nothing at all
    signer = signer_for()

    authenticated = DeviceAuthenticator(signer=signer).authenticate(
        link, CHALLENGE, lambda _line: None, lambda: True
    )
    assert authenticated is False
    assert signer.calls == []
    assert not any(text.startswith(AUTH_COMMAND) for text in link.written)


def test_a_completed_handshake_reports_success():
    link = FakeLink({STATUS_COMMAND: ok_status(), AUTH_COMMAND: ['{"result":"ok"}']})
    assert (
        DeviceAuthenticator(signer=signer_for()).authenticate(link, CHALLENGE, lambda _line: None)
        is True
    )


def test_a_silent_device_times_out_the_handshake():
    clock = [0.0]

    def now():
        clock[0] += 10.0
        return clock[0]

    link = FakeLink()  # answers nothing
    with pytest.raises(AuthError, match="timed out"):
        DeviceAuthenticator(signer=signer_for(), now=now).authenticate(
            link, CHALLENGE, lambda _line: None
        )


def test_the_signer_is_built_from_the_config_only_when_auth_is_needed(tmp_path):
    """A device that never challenges must work with no credentials configured."""
    built = []

    def factory():
        built.append(True)
        raise AuthError("no credentials")

    DeviceAuthenticator(signer_factory=factory)
    assert built == []
