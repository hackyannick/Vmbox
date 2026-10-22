"""Authentication, sessions and the screen lock."""

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

ITERATIONS = 600_000
MIN_PASSWORD_LENGTH = 6


class AuthError(Exception):
    pass


def hash_password(password, iterations=ITERATIONS, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return "pbkdf2_sha256$%d$%s$%s" % (
        iterations,
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    )


def verify_password(password, encoded):
    try:
        algo, iterations, salt, digest = encoded.split("$")
    except (AttributeError, ValueError):
        return False
    if algo != "pbkdf2_sha256":
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(iterations))
    return hmac.compare_digest(candidate, base64.b64decode(digest))


class Auth:
    def __init__(self, path, session_timeout=12 * 3600, iterations=ITERATIONS):
        self.path = path
        self.session_timeout = session_timeout
        self.iterations = iterations
        self._sessions = {}
        self._lock = threading.Lock()
        self._failures = 0
        self._blocked_until = 0.0
        self.locked = False

    # -- password storage -------------------------------------------------
    def _load(self):
        try:
            with open(self.path) as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def _save(self, data):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh)
        os.replace(tmp, self.path)

    @property
    def setup_required(self):
        return "password" not in self._load()

    def _check_strength(self, password):
        if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
            raise AuthError("Das Passwort muss mindestens %d Zeichen lang sein" % MIN_PASSWORD_LENGTH)

    def setup(self, password):
        with self._lock:
            if not self.setup_required:
                raise AuthError("Es wurde bereits ein Passwort festgelegt")
            self._check_strength(password)
            self._save({"password": hash_password(password, self.iterations)})
        return self._new_session()

    def change_password(self, old, new):
        if not self._verify(old):
            raise AuthError("Das bisherige Passwort ist falsch")
        self._check_strength(new)
        with self._lock:
            data = self._load()
            data["password"] = hash_password(new, self.iterations)
            self._save(data)

    def _verify(self, password):
        encoded = self._load().get("password")
        return bool(encoded) and isinstance(password, str) and verify_password(password, encoded)

    # -- sessions -----------------------------------------------------------
    def _new_session(self):
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[token] = time.monotonic() + self.session_timeout
            self.locked = False
        return token

    def login(self, password):
        now = time.monotonic()
        if now < self._blocked_until:
            raise AuthError("Zu viele Fehlversuche - bitte %d s warten" % int(self._blocked_until - now + 1))
        if not self._verify(password):
            self._failures += 1
            if self._failures >= 3:
                self._blocked_until = now + min(300, 5 * 2 ** (self._failures - 3))
            raise AuthError("Falsches Passwort")
        self._failures = 0
        return self._new_session()

    def valid(self, token):
        if not token:
            return False
        with self._lock:
            expiry = self._sessions.get(token)
            if expiry is None:
                return False
            if expiry < time.monotonic():
                del self._sessions[token]
                return False
            return not self.locked

    def logout(self, token):
        with self._lock:
            self._sessions.pop(token, None)

    def lock(self):
        """Lock the screen: every session is dropped, a new login is required."""
        with self._lock:
            self._sessions.clear()
            self.locked = True
