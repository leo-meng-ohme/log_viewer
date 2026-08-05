"""CLI session authentication for devices that protect ``extract_logs``.

A provisioned device — one whose QA has completed, or one that is mid-QA —
refuses protected commands until the CLI session is authenticated. Instead of
the command's output it answers::

    {"result":"auth_required","challenge":"<32-char hex>"}

The handshake that clears it, as implemented by the firmware in
``cli_cmd_auth.c`` and mirrored here from ``firmware-hq/ohme_fw_cli``:

1. ``get_cli_status`` — answered without authentication — gives the device's
   ``charger_id``.
2. The Ohme backend signs the challenge for that charger. The signature is an
   ECDSA P-256 signature over SHA-256 of the challenge's hex *string*; the CLI
   auth private key never leaves the backend.
3. The signature is wrapped in an ES256 JWT whose payload carries the challenge
   as its ``nonce`` claim, and sent as ``cli_authenticate <jwt>``.
4. The device replies ``{"result":"ok"}`` and the session stays authenticated
   until the device reboots, so the original command can simply be re-sent.

Backend credentials are read from the ``device-cli`` configuration this machine
already has, so the viewer needs no credential UI of its own and no second
place to keep a password. A device that never challenges never touches any of
this: the signer is built on first use, so an unconfigured machine still views
logs from a factory-fresh unit.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

#: Reports session state and the device's charger ID. Never authenticated.
STATUS_COMMAND = "get_cli_status"

#: Carries the signed JWT back to the device.
AUTH_COMMAND = "cli_authenticate"

#: How long the device may take to answer each handshake command.
#:
#: The RT1020 has no ECC accelerator, so verifying the JWT signature is
#: software bignum arithmetic: ~1.25 s of CPU time, and longer whenever the CLI
#: thread is preempted. Generous is the safe direction here — a handshake that
#: is merely slow must not be reported as a dead device.
_AUTH_TIMEOUT_S = 20.0
_STATUS_TIMEOUT_S = 5.0

_HTTP_TIMEOUT_S = 30.0

#: Where device-cli keeps its config and its Firebase token cache.
_CONFIG_DIR_NAME = "device-cli"
_CONFIG_FILE = "config.toml"
_TOKEN_CACHE_FILE = "firebase_cache.json"

#: Environment overrides, for a machine that has no device-cli config.
_ENV_VARS = {
    "be_url": "OHME_BE_URL",
    "email": "OHME_EMAIL",
    "password": "OHME_PASSWORD",
}

_FIREBASE_SIGNIN_URL = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
_FIREBASE_REFRESH_URL = "https://securetoken.googleapis.com/v1/token"

#: Firebase web API keys, chosen by backend hostname exactly as device-cli does.
_FIREBASE_KEY_DEV_QA = "AIzaSyAXNbieB6V6MsYQwvEeGMVVigmHTtv7TMo"
_FIREBASE_KEY_PROD = "AIzaSyD7LkU-gNjjW2w06aiDIfYliAf0q3I15pQ"
_DEV_QA_HOST_RE = re.compile(r"api-(dev|qa)\d*\.ohme\.io$", re.IGNORECASE)

#: Treat a token as expired this early, so it cannot lapse mid-request.
_TOKEN_MARGIN_S = 60.0

_DECODER = json.JSONDecoder()


class AuthError(Exception):
    """The session could not be authenticated.

    Deliberately not a :class:`~ohme_log_viewer.serial_link.SerialLinkError`:
    the link is fine, and the message that reaches the user should say so.

    ``retryable`` separates a transient from a refusal, because the viewer
    retries a failed session once a second and the two must not be treated
    alike. A device that did not answer in time, or a backend that could not be
    reached, will very likely work on the next attempt — a device rebooting
    mid-handshake is exactly that case. A refusal — wrong credentials, a
    rejected JWT, an unprovisioned unit — will not, and retrying it would mean
    one pointless backend round trip per second for as long as the window is
    open.
    """

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


# ── protocol ─────────────────────────────────────────────────────────────


def _parse_json(line: str) -> dict | None:
    """Return the JSON object in *line*, or None if there is not one.

    Decoding starts at the first ``{`` and stops at the end of that object, so
    a response with the device's banner in front of it or trailing bytes behind
    it still parses.
    """
    brace = line.find("{")
    if brace < 0:
        return None
    try:
        parsed, _ = _DECODER.raw_decode(line[brace:])
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def parse_auth_challenge(line: str) -> str | None:
    """Return the challenge *line* is asking to have signed, else None."""
    parsed = _parse_json(line)
    if parsed is None or parsed.get("result") != "auth_required":
        return None
    challenge = parsed.get("challenge")
    if not isinstance(challenge, str) or not challenge:
        return None
    return challenge


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def build_jwt(challenge_hex: str, signature_b64: str) -> str:
    """Wrap the backend's signature and the challenge into an ES256 JWT.

    The device reads the ``nonce`` claim, checks it against the challenge it
    issued, and verifies the signature against SHA-256 of that same hex string.
    The header is therefore not covered by the signature, and its exact
    serialisation does not matter.
    """
    header = _b64url(json.dumps({"alg": "ES256", "typ": "JWT"}).encode())
    payload = _b64url(json.dumps({"nonce": challenge_hex}).encode())
    unpadded = signature_b64.strip().rstrip("=")
    signature = _b64url(base64.b64decode(unpadded + "=" * (-len(unpadded) % 4)))
    return f"{header}.{payload}.{signature}"


# ── credentials ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Credentials:
    """What is needed to have a challenge signed."""

    be_url: str
    email: str
    password: str


def devcli_config_dir() -> Path:
    """Return device-cli's configuration directory for this platform."""
    if sys.platform == "win32":
        base = Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path.home() / ".config"
    return base / _CONFIG_DIR_NAME


def load_credentials(config_dir: Path | None = None, env: dict | None = None) -> Credentials:
    """Read backend credentials from the environment, then device-cli's config.

    Raises :class:`AuthError` naming what is missing and where to put it. A
    missing file is not an error in itself — only a missing value is — so the
    same message serves both cases.
    """
    variables = os.environ if env is None else env
    directory = devcli_config_dir() if config_dir is None else Path(config_dir)
    path = directory / _CONFIG_FILE

    app: dict = {}
    try:
        with open(path, "rb") as handle:
            app = tomllib.load(handle).get("app", {})
    except FileNotFoundError:
        pass
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise AuthError(f"Could not read {path}: {exc}") from exc

    values = {
        field: str(variables.get(var) or app.get(field) or "").strip()
        for field, var in _ENV_VARS.items()
    }
    missing = [field for field, value in values.items() if not value]
    if missing:
        raise AuthError(
            f"This device requires an authenticated CLI session, but the backend "
            f"credentials needed to sign its challenge are missing: "
            f"{', '.join(missing)}. Set them under [app] in {path}, or in "
            f"{', '.join(_ENV_VARS[field] for field in missing)}."
        )
    return Credentials(**values)


# ── backend ──────────────────────────────────────────────────────────────


class _HttpFailure(Exception):
    """An HTTP request that did not return a response body."""

    def __init__(self, status: int | None, body: str) -> None:
        super().__init__(f"status={status} body={body}")
        self.status = status
        self.body = body

    def describe(self) -> str:
        if self.status is None:
            return f"network error — {self.body}"
        return f"HTTP {self.status} — {self.body}"

    @property
    def transient(self) -> bool:
        """True if trying again shortly could plausibly succeed.

        An unreachable host and a 5xx are worth another attempt; a 4xx is the
        server telling us the request itself is wrong.
        """
        return self.status is None or self.status >= 500


def _firebase_key_for(be_url: str) -> str:
    host = urlparse(be_url).hostname or ""
    return _FIREBASE_KEY_DEV_QA if _DEV_QA_HOST_RE.match(host) else _FIREBASE_KEY_PROD


class BackendSigner:
    """Has the Ohme backend sign a device challenge.

    Firebase tokens are shared with device-cli by reading its cache, so a
    session that tool has already signed in for costs no sign-in here. The
    cache is never written back: two tools writing one file would be a race for
    no benefit, and a token held in memory lasts longer than a viewer session.
    """

    def __init__(
        self,
        credentials: Credentials,
        *,
        urlopen: Callable | None = None,
        now: Callable[[], float] = time.time,
        cache_path: Path | None = None,
    ) -> None:
        self._credentials = credentials
        self._urlopen = urlopen or urllib.request.urlopen
        self._now = now
        self._cache_path = (
            devcli_config_dir() / _TOKEN_CACHE_FILE if cache_path is None else Path(cache_path)
        )
        self._firebase_key = _firebase_key_for(credentials.be_url)
        self._id_token = ""
        self._refresh_token = ""
        self._expiry = 0.0
        self._cache_read = False

    def sign_challenge(self, challenge_hex: str, charger_id: str) -> str:
        """Return the JWT proving *challenge_hex* was signed for *charger_id*."""
        url = (
            f"{self._credentials.be_url.rstrip('/')}"
            f"/v2/charge-devices/{charger_id}/hardware/qa/sign"
        )
        payload = {"challenge": challenge_hex}
        try:
            response = self._post(url, payload, self._auth_header())
        except _HttpFailure as failure:
            if failure.status != 401:
                raise AuthError(
                    f"Backend would not sign the challenge: {failure.describe()}",
                    retryable=failure.transient,
                )
            # The token was accepted when it was fetched and is not now. One
            # forced refresh distinguishes a lapsed token from a real refusal.
            self._expiry = 0.0
            try:
                response = self._post(url, payload, self._auth_header())
            except _HttpFailure as retry:
                raise AuthError(
                    f"Backend would not sign the challenge: {retry.describe()}",
                    retryable=retry.transient,
                )

        signature = response.get("signature")
        if not signature:
            raise AuthError(f"Backend returned no 'signature' for the challenge: {response}")
        return build_jwt(challenge_hex, signature)

    # ── Firebase tokens ──────────────────────────────────────────────────

    def _auth_header(self) -> dict[str, str]:
        return {"Authorization": f"Firebase {self._id_or_new_token()}"}

    def _id_or_new_token(self) -> str:
        if self._id_token and self._now() < self._expiry:
            return self._id_token

        if not self._cache_read:
            self._read_cache()
            self._cache_read = True
            if self._id_token and self._now() < self._expiry:
                return self._id_token

        if self._refresh_token:
            return self._refresh()
        return self._sign_in()

    def _read_cache(self) -> None:
        """Adopt device-cli's cached tokens, if they belong to this account."""
        try:
            cached = json.loads(self._cache_path.read_text())
            if cached.get("email") != self._credentials.email:
                return
            self._id_token = str(cached.get("id_token", ""))
            self._refresh_token = str(cached.get("refresh_token", ""))
            self._expiry = float(cached.get("expiry", 0.0))
        except (OSError, ValueError, TypeError, AttributeError):
            pass  # absent or unusable cache; sign in instead

    def _refresh(self) -> str:
        try:
            response = self._post(
                f"{_FIREBASE_REFRESH_URL}?key={self._firebase_key}",
                {"grant_type": "refresh_token", "refresh_token": self._refresh_token},
            )
        except _HttpFailure:
            return self._sign_in()

        id_token = str(response.get("id_token", ""))
        if not id_token:
            return self._sign_in()
        self._store_token(
            id_token,
            str(response.get("refresh_token", self._refresh_token)),
            response.get("expires_in", 3600),
        )
        return id_token

    def _sign_in(self) -> str:
        try:
            response = self._post(
                f"{_FIREBASE_SIGNIN_URL}?key={self._firebase_key}",
                {
                    "email": self._credentials.email,
                    "password": self._credentials.password,
                    "returnSecureToken": True,
                },
            )
        except _HttpFailure as failure:
            raise AuthError(
                f"Firebase sign-in as {self._credentials.email} failed: {failure.describe()}",
                retryable=failure.transient,
            )

        id_token = str(response.get("idToken", ""))
        if not id_token:
            raise AuthError("Firebase sign-in returned no 'idToken'")
        self._store_token(
            id_token, str(response.get("refreshToken", "")), response.get("expiresIn", 3600)
        )
        return id_token

    def _store_token(self, id_token: str, refresh_token: str, expires_in) -> None:
        self._id_token = id_token
        self._refresh_token = refresh_token
        try:
            lifetime = float(expires_in)
        except (TypeError, ValueError):
            lifetime = 3600.0
        self._expiry = self._now() + lifetime - _TOKEN_MARGIN_S

    # ── HTTP ─────────────────────────────────────────────────────────────

    def _post(self, url: str, payload: dict, headers: dict[str, str] | None = None) -> dict:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", **(headers or {})},
            method="POST",
        )
        try:
            with self._urlopen(request, timeout=_HTTP_TIMEOUT_S) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            raise _HttpFailure(exc.code, _error_body(exc)) from exc
        except urllib.error.URLError as exc:
            raise _HttpFailure(None, str(exc.reason)) from exc
        except OSError as exc:
            raise _HttpFailure(None, str(exc)) from exc

        try:
            decoded = json.loads(body.decode())
        except (UnicodeDecodeError, ValueError) as exc:
            raise AuthError(f"Backend sent a non-JSON response from {url}: {body[:200]!r}") from exc
        if not isinstance(decoded, dict):
            raise AuthError(f"Backend sent an unexpected response from {url}: {decoded!r}")
        return decoded


def _error_body(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", errors="replace").strip()
    except (OSError, ValueError):
        return exc.reason if isinstance(exc.reason, str) else str(exc.reason)


# ── device handshake ─────────────────────────────────────────────────────


def _signer_from_config() -> BackendSigner:
    return BackendSigner(load_credentials())


def _never() -> bool:
    """Default ``should_stop``: a caller with nothing to interrupt it."""
    return False


class DeviceAuthenticator:
    """Answers a device's challenge on the link the challenge arrived on.

    One instance per connection: the charger ID is asked for once and the
    Firebase token is held for as long as the session lasts.
    """

    def __init__(
        self,
        *,
        signer=None,
        signer_factory: Callable[[], BackendSigner] = _signer_from_config,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._signer = signer
        self._signer_factory = signer_factory
        self._now = now
        self._charger_id = ""

    def authenticate(
        self,
        link,
        challenge_hex: str,
        emit: Callable[[str], None],
        should_stop: Callable[[], bool] = _never,
    ) -> bool:
        """Complete the handshake for *challenge_hex*.

        Returns True once the session is authenticated, and False if
        *should_stop* asked to stop while a device response was outstanding —
        the two device waits here are far longer than the reader's own read
        timeout, and Disconnect must not have to sit through them. Raises
        :class:`AuthError` if the session cannot be authenticated.

        Lines that arrive while a response is awaited are passed to *emit*
        rather than dropped: the device may still be logging, and this viewer
        discards nothing it received.
        """
        if self._signer is None:
            # Built here, not in __init__: a device that never challenges must
            # not need credentials to be configured at all.
            self._signer = self._signer_factory()

        if not self._charger_id and not self._ask_charger_id(link, emit, should_stop):
            return False

        emit(
            f"[auth] challenge {challenge_hex} — asking the backend to sign for "
            f"{self._charger_id}"
        )
        # The one step that cannot be interrupted: an HTTP request already in
        # flight. It is bounded by _HTTP_TIMEOUT_S, and with a cached token it
        # is a single call.
        jwt = self._signer.sign_challenge(challenge_hex, self._charger_id)

        link.write_line(f"{AUTH_COMMAND} {jwt}")
        response = self._read_json(link, emit, _AUTH_TIMEOUT_S, AUTH_COMMAND, should_stop)
        if response is None:
            return False

        if response.get("result") != "ok":
            raise AuthError(f"Device rejected authentication: {response.get('msg', response)}")
        emit("[auth] session authenticated")
        return True

    def _ask_charger_id(
        self, link, emit: Callable[[str], None], should_stop: Callable[[], bool]
    ) -> bool:
        """Query and store the device's charger ID. False if asked to stop."""
        link.write_line(STATUS_COMMAND)
        status = self._read_json(link, emit, _STATUS_TIMEOUT_S, STATUS_COMMAND, should_stop)
        if status is None:
            return False
        charger_id = str(status.get("charger_id", "")).strip()
        if not charger_id:
            raise AuthError(
                "Device reported no charger_id, so there is nothing for the backend to "
                "sign against. A unit that has not been provisioned with a factory ID "
                "cannot authenticate its CLI session."
            )
        self._charger_id = charger_id
        return True

    def _read_json(
        self,
        link,
        emit: Callable[[str], None],
        timeout_s: float,
        command: str,
        should_stop: Callable[[], bool],
    ) -> dict | None:
        """Wait for *command*'s JSON response. None if asked to stop first."""
        deadline = self._now() + timeout_s
        while True:
            if should_stop():
                return None
            if self._now() > deadline:
                raise AuthError(
                    f"Device did not answer '{command}': timed out after {timeout_s:.0f}s",
                    # A device mid-reboot answers nothing and is fine a moment
                    # later, which is the case the viewer's retry exists for.
                    retryable=True,
                )
            line = link.read_line()
            if line is None:
                continue  # read timeout; keep waiting until the deadline
            parsed = _parse_json(line)
            if parsed is not None:
                return parsed
            emit(line)
