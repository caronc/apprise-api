#
# Copyright (C) 2026 Chris Caron <lead2gold@gmail.com>
# All rights reserved.
#
# This code is licensed under the MIT License.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files(the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and / or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions :
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
"""Provide authentication policy, credential checks, and browser sessions."""

import base64
import binascii
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import hmac
import logging
import math
import os
import threading
import time

from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.core import signing
from django.http import HttpRequest

logger = logging.getLogger("django")


class ConfigCredentialVerifier:
    """Safely reuse recent successful password checks.

    Every API request still supplies Basic Auth. The cache only remembers a
    private fingerprint after Django accepts the password, so the fingerprint
    cannot be sent back to the API as a replacement credential.
    """

    _REQUEST_CACHE_ATTRIBUTE = "_apprise_auth_verification_results"

    def __init__(self, max_entries=4096, ttl=300, secret=None, clock=None, password_checker=None):
        """Create a bounded verifier with a fixed lifetime for each result."""
        # Validate arguments before creating any internal state.
        if not isinstance(max_entries, int) or isinstance(max_entries, bool) or max_entries < 1:
            raise ValueError("max_entries must be at least one")
        if not isinstance(ttl, (int, float)) or isinstance(ttl, bool) or not math.isfinite(ttl) or ttl <= 0:
            raise ValueError("ttl must be greater than zero")
        if secret is not None and (not isinstance(secret, bytes) or len(secret) < 32):
            raise ValueError("secret must contain at least 32 bytes")
        if clock is not None and not callable(clock):
            raise ValueError("clock must be callable")
        if password_checker is not None and not callable(password_checker):
            raise ValueError("password_checker must be callable")

        # Track max_entries as public attributes for introspection and testing.
        # This is a security feature: a large cache could be stolen and reused by an attacker.
        self.max_entries = max_entries

        # Track ttl as a public attribute for introspection and testing.
        # This is a security feature: a long-lived cache entry could be stolen and reused by an attacker.
        self.ttl = ttl

        # This secret exists only in this process. A cache entry is useless to
        # someone who copies it without also gaining access to this secret.
        if secret is None:
            try:
                secret = os.urandom(32)
            except OSError as e:
                # Starting without secure randomness could expose every cache
                # fingerprint, so refusing to start is safer than guessing.
                raise RuntimeError("secure randomness is unavailable") from e
        self._secret = secret
        self._clock = clock or time.monotonic
        self._password_checker = password_checker or check_password
        self._cache = OrderedDict()
        # Django can serve requests on several threads in the same process.
        # The lock keeps two threads from changing the ordered cache together.
        self._lock = threading.Lock()

    def clear(self):
        """Forget all process-local results without changing saved logins."""
        with self._lock:
            self._cache.clear()

    def __len__(self):
        """Return the number of successful checks currently remembered."""
        with self._lock:
            return len(self._cache)

    def _fingerprint(self, key, username, password, digest):
        """Hide credentials inside a process-private, fixed-size cache key."""
        fingerprint = hmac.new(self._secret, digestmod=hashlib.sha256)
        for value in (key, username, password, digest):
            encoded = value.encode("utf-8", errors="surrogatepass")
            # Lengths keep combinations such as ("ab", "c") separate from
            # ("a", "bc") even though their joined text would look the same.
            fingerprint.update(len(encoded).to_bytes(8, "big"))
            fingerprint.update(encoded)
        return fingerprint.digest()

    def _request_cache(self, request):
        """Return this request's small decision cache when it is usable."""
        if request is None:
            return None

        try:
            cache = getattr(request, self._REQUEST_CACHE_ATTRIBUTE, None)
            if cache is None:
                cache = {}
                setattr(request, self._REQUEST_CACHE_ATTRIBUTE, cache)
            elif not isinstance(cache, dict):
                # Unexpected middleware state must never grant access.
                logger.warning("Ignoring an invalid request authentication cache")
                return None
            return cache

        except Exception as e:
            # Custom request wrappers may reject new attributes. Authentication
            # still works; only the per-request shortcut is unavailable.
            logger.warning("Could not use the request authentication cache: %s", e)
            return None

    def _cached_success(self, fingerprint):
        """Return whether an unexpired successful check is in memory."""
        now = self._clock()
        with self._lock:
            expires = self._cache.pop(fingerprint, None)
            if expires is None or expires <= now:
                return False
            # Put the entry at the end because it was just used. Its original
            # expiry stays unchanged, so activity does not extend five minutes.
            self._cache[fingerprint] = expires
            return True

    def _remember_success(self, fingerprint):
        """Store one successful result and discard the oldest when full."""
        expires = self._clock() + self.ttl
        with self._lock:
            self._cache[fingerprint] = expires
            self._cache.move_to_end(fingerprint)
            while len(self._cache) > self.max_entries:
                self._cache.popitem(last=False)

    def verify(self, key, username, password, stored_username, digest, request=None):
        """Return whether credentials match the current configuration digest.

        The request cache avoids duplicate work inside one Django request. The
        process cache helps later requests, while password changes naturally
        use a different fingerprint because the saved digest changes.
        """
        values = (key, username, password, digest)
        if not all(isinstance(value, str) for value in values):
            return False
        if stored_username is not None and not isinstance(stored_username, str):
            return False

        # Always perform the password check below. Returning early for a wrong
        # username exposes the saved account label through a large timing gap.
        username_matches = stored_username is None or username == stored_username

        try:
            fingerprint = self._fingerprint(key, username, password, digest)
        except Exception as e:
            # Fingerprinting is an optimization. If it is unavailable, use
            # Django's password check directly and do not cache the result.
            logger.warning("Could not fingerprint configuration credentials: %s", e)
            return self._check_password(username, password, digest) and username_matches

        request_cache = self._request_cache(request)
        if request_cache is not None:
            try:
                if fingerprint in request_cache:
                    return request_cache[fingerprint]
            except Exception as e:
                # A strange dictionary subclass should not be able to crash
                # authentication. Ignore it and continue with normal checks.
                logger.warning("Could not read the request authentication result: %s", e)
                request_cache = None

        try:
            result = self._cached_success(fingerprint)
        except Exception as e:
            # A broken clock or cache must not crash authentication. We can
            # still safely fall back to Django's complete password check.
            logger.warning("Could not read the authentication cache: %s", e)
            result = False

        if not result:
            result = self._check_password(username, password, digest)
            result = username_matches and result
            if result:
                try:
                    self._remember_success(fingerprint)
                except Exception as e:
                    # The password was valid. A cache failure should make
                    # later requests slower, not reject this request.
                    logger.warning("Could not cache valid configuration credentials: %s", e)

        if request_cache is not None:
            try:
                request_cache[fingerprint] = result
            except Exception as e:
                # This result is only a shortcut for the current request. The
                # authentication decision itself remains valid without it.
                logger.warning("Could not save the request authentication result: %s", e)
        return result

    def _check_password(self, username, password, digest):
        """Run Django's password verifier and reject unexpected failures."""
        try:
            return bool(self._password_checker("{}:{}".format(username, password), digest))
        except Exception as e:
            # Authentication boundaries fail closed. A damaged digest or
            # unavailable crypto backend becomes a rejected login, not a 500.
            logger.warning("Could not verify configuration credentials: %s", e)
            return False


class AuthStorageError(Exception):
    """Raised when an existing configuration access record cannot be read."""


@dataclass(frozen=True)
class ConfigAuthRecord:
    """Store one configuration's access policy and optional login."""

    access: str
    username: str | None = None
    digest: str | None = None


@dataclass(frozen=True)
class ConfigAuthState:
    """Describe one Config ID's saved and effective access policy."""

    mode: str
    username: str | None = None
    digest: str | None = None
    access: str = "user"
    saved_access: str | None = None
    unreadable: bool = False

    @property
    def assigned(self):
        """Return whether this key has saved credentials."""
        return self.digest is not None

    @property
    def configured(self):
        """Return whether this key has a saved access record."""
        return self.mode == Authentication.MODE_ASSIGNED

    @property
    def config_locked(self):
        """Return whether non-admin callers cannot view configuration content."""
        return self.access in {
            Authentication.ACCESS_LOCK,
            Authentication.ACCESS_PUBLIC,
            Authentication.ACCESS_DISABLED,
        }

    @property
    def public(self):
        """Return whether stateful notifications may omit authentication."""
        return self.access == Authentication.ACCESS_PUBLIC

    @property
    def disabled(self):
        """Return whether only an administrator may use this configuration."""
        return self.access == Authentication.ACCESS_DISABLED


class Authentication:
    """Keep authentication policy and request helpers in one place."""

    # These values describe the person making a request.
    ROLE_DISABLED = "disabled"
    ROLE_ADMIN = "admin"
    ROLE_USER = "user"

    # These values describe how a Config ID is protected.
    MODE_DISABLED = "disabled"
    MODE_GLOBAL = "global"
    MODE_ASSIGNED = "assigned"

    # One saved field controls who may use and manage a configuration.
    ACCESS_USER = "user"
    ACCESS_LOCK = "locked"
    ACCESS_PUBLIC = "public"
    ACCESS_DISABLED = "disabled"
    ACCESS_CHOICES = frozenset({ACCESS_USER, ACCESS_LOCK, ACCESS_PUBLIC, ACCESS_DISABLED})

    @staticmethod
    def effective_config_access(access: str) -> str:
        """Apply the global lock without rewriting a saved per-key choice."""
        if settings.APPRISE_CONFIG_LOCK and access in {
            Authentication.ACCESS_USER,
            Authentication.ACCESS_PUBLIC,
        }:
            # The site-wide policy is a minimum: only the stricter disabled
            # mode may remain different while configuration locking is active.
            return Authentication.ACCESS_LOCK
        return access

    # Browser pages use a signed cookie while API clients use Basic Auth.
    WEB_COOKIE = "apprise_web_auth"
    WEB_HEADER = "X-Apprise-Web-Auth"
    _WEB_SIGNING_SALT = "apprise-api.web-auth"

    # One verifier is shared by requests handled in this Python process.
    credential_verifier = ConfigCredentialVerifier()

    @staticmethod
    def basic_credentials(request: HttpRequest):
        """Decode Basic Auth, returning empty markers when it is malformed."""
        header = request.headers.get("authorization", "")
        if header[:6].lower() != "basic ":
            return None, None

        try:
            decoded = base64.b64decode(header[6:], validate=True).decode()
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return None, None

        if ":" not in decoded:
            return None, None

        # Split once because passwords may contain colons.
        username, _, password = decoded.partition(":")
        return username.strip(), password

    @staticmethod
    def global_credentials_ok(username: str, password: str) -> bool:
        """Return whether a username and password match the admin login."""
        if settings.APPRISE_BASIC_AUTH_TOKEN is None:
            return False

        if username is None:
            username = ""
        if not isinstance(username, str) or not isinstance(password, str):
            return False

        try:
            username = username.strip()
            provided = base64.b64encode(f"{username}:{password}".encode()).decode()
        except UnicodeEncodeError:
            return False

        return hmac.compare_digest(provided, settings.APPRISE_BASIC_AUTH_TOKEN)

    @staticmethod
    def is_authenticated(request: HttpRequest) -> bool:
        """Return whether a request satisfies the global Basic Auth gate."""
        if not settings.APPRISE_AUTH_REQUIRED:
            return True
        if settings.APPRISE_BASIC_AUTH_TOKEN is None:
            return False

        username, password = Authentication.basic_credentials(request)
        return username is not None and Authentication.global_credentials_ok(username, password)

    @staticmethod
    def config_state(key: str, request=None) -> ConfigAuthState:
        """Read one Config ID's access state once per request."""
        from .utils import ConfigCache

        request_cache = getattr(request, "_apprise_config_auth_states", None)
        if request_cache is not None and key in request_cache:
            return request_cache[key]

        if not settings.APPRISE_AUTH_REQUIRED:
            state = ConfigAuthState(
                Authentication.MODE_DISABLED,
                access=Authentication.effective_config_access(Authentication.ACCESS_USER),
            )
        else:
            try:
                record = ConfigCache.get_auth_record(key)
            except AuthStorageError:
                # A damaged record stays protected until an admin replaces it.
                state = ConfigAuthState(
                    Authentication.MODE_ASSIGNED,
                    access=Authentication.ACCESS_LOCK,
                    unreadable=True,
                )
            else:
                state = (
                    ConfigAuthState(
                        Authentication.MODE_GLOBAL,
                        access=Authentication.effective_config_access(Authentication.ACCESS_USER),
                    )
                    if record is None
                    else ConfigAuthState(
                        Authentication.MODE_ASSIGNED,
                        username=record.username,
                        digest=record.digest,
                        saved_access=record.access,
                        # Keep the record unchanged so its original access
                        # returns if the administrator removes the global lock.
                        access=Authentication.effective_config_access(record.access),
                    )
                )

        if request is not None:
            if request_cache is None:
                request_cache = {}
                request._apprise_config_auth_states = request_cache
            request_cache[key] = state
        return state

    @staticmethod
    def key_credentials_ok(request: HttpRequest, key: str, username: str, password: str) -> bool:
        """Check credentials against one configuration account."""
        state = Authentication.config_state(key, request)
        if not state.assigned or state.digest is None:
            return False

        if Authentication.credential_verifier.verify(
            key,
            username,
            password,
            state.username,
            state.digest,
            request=request,
        ):
            request.apprise_auth_permission = Authentication.ROLE_USER
            request.apprise_auth_username = username
            return True
        return False

    @staticmethod
    def key_ok(request: HttpRequest, key: str, allow_public=False) -> bool:
        """Return whether this request may use a protected Config ID."""
        request.apprise_config_key = key
        if getattr(request, "globally_authenticated", False):
            request.apprise_auth_permission = Authentication.ROLE_ADMIN
            return True

        state = Authentication.config_state(key, request)
        if (
            getattr(request, "apprise_auth_permission", Authentication.ROLE_DISABLED) == Authentication.ROLE_USER
            and getattr(request, "apprise_web_auth_key", None) == key
        ):
            if state.disabled:
                request.apprise_disabled_config_key = key
                return False
            return True

        if not settings.APPRISE_AUTH_REQUIRED:
            request.apprise_auth_permission = Authentication.ROLE_DISABLED
            return True

        if allow_public and state.public:
            # Views still require a specific tag before sending anything.
            return True

        username, password = Authentication.basic_credentials(request)
        credentials_ok = username is not None and Authentication.key_credentials_ok(
            request,
            key,
            username,
            password,
        )
        if credentials_ok and state.disabled:
            # Only reveal the disabled policy after these credentials have
            # successfully proven ownership of this exact configuration.
            request.apprise_disabled_config_key = key
            return False
        return credentials_ok

    @staticmethod
    def configuration_is_locked(key=None, request=None):
        """Return whether global or per-key policy hides configuration content."""
        from .utils import stateful_store_enabled

        if not stateful_store_enabled():
            return False
        if settings.APPRISE_CONFIG_LOCK:
            return True
        if not settings.APPRISE_AUTH_REQUIRED or not key:
            return False
        return Authentication.config_state(key, request).config_locked

    @staticmethod
    def config_lock_allows(request, key=None):
        """Return whether this caller may view or change configuration content."""
        if getattr(request, "globally_authenticated", False):
            return True
        return not Authentication.configuration_is_locked(key, request)

    @staticmethod
    def can_list_configurations(request):
        """Return whether this request may list every saved configuration."""
        from .utils import AppriseStoreMode

        mode = str(settings.APPRISE_STATEFUL_MODE).strip().lower()
        if not settings.APPRISE_ADMIN or mode != AppriseStoreMode.SIMPLE:
            return False
        if settings.APPRISE_AUTH_REQUIRED:
            return getattr(request, "globally_authenticated", False)
        return not settings.APPRISE_CONFIG_LOCK

    @staticmethod
    def can_move_or_delete(request):
        """Return whether the global lock permits moving or deleting a key."""
        from .utils import stateful_store_enabled

        return stateful_store_enabled() and Authentication.config_lock_allows(request)

    @staticmethod
    def _web_proof(mode: str, key=None):
        """Return a private fingerprint of the login backing a web session."""
        from .utils import ConfigCache

        if mode == Authentication.ROLE_ADMIN:
            credential = settings.APPRISE_BASIC_AUTH_TOKEN
        elif mode == Authentication.ROLE_USER and key:
            try:
                credential = ConfigCache.get_auth(key)
            except AuthStorageError:
                return None
        else:
            return None

        if not credential:
            return None
        return hmac.new(
            settings.APPRISE_WEB_AUTH_SECRET.encode("utf-8"),
            credential.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def set_web_cookie(response, request, mode: str, username: str, key=None) -> None:
        """Store a signed browser login without saving its password."""
        payload = {
            "mode": mode,
            "username": username,
            "key": key if mode == Authentication.ROLE_USER else None,
            "proof": Authentication._web_proof(mode, key),
        }
        secure = request.is_secure() or f"https://{request.get_host()}".lower() in settings.APPRISE_TRUSTED_ORIGINS
        response.set_cookie(
            Authentication.WEB_COOKIE,
            signing.dumps(
                payload,
                key=settings.APPRISE_WEB_AUTH_SECRET,
                salt=Authentication._WEB_SIGNING_SALT,
                compress=False,
            ),
            httponly=True,
            secure=secure,
            samesite="Lax",
            path=settings.BASE_URL or "/",
            max_age=settings.APPRISE_WEB_AUTH_MAX_AGE,
        )

    @staticmethod
    def clear_web_cookie(response) -> None:
        """Remove the browser login and its remembered configuration."""
        response.delete_cookie(
            Authentication.WEB_COOKIE,
            path=settings.BASE_URL or "/",
            samesite="Lax",
        )
        response.delete_cookie("key", path="/", samesite="Lax")

    @staticmethod
    def restore_web(request: HttpRequest, requested_key=None, allow_shared_without_key=False) -> bool:
        """Restore a valid signed browser login onto the request."""
        value = request.COOKIES.get(Authentication.WEB_COOKIE)
        if not value or len(value) > 4096 or value.startswith("."):
            return False

        try:
            payload = signing.loads(
                value,
                key=settings.APPRISE_WEB_AUTH_SECRET,
                salt=Authentication._WEB_SIGNING_SALT,
                max_age=settings.APPRISE_WEB_AUTH_MAX_AGE,
            )
        except (signing.BadSignature, TypeError, ValueError):
            return False

        mode = payload.get("mode") if isinstance(payload, dict) else None
        username = payload.get("username") if isinstance(payload, dict) else None
        key = payload.get("key") if isinstance(payload, dict) else None
        proof = payload.get("proof") if isinstance(payload, dict) else None
        if not isinstance(username, str) or not isinstance(proof, str):
            return False

        if mode == Authentication.ROLE_USER:
            if requested_key and key != requested_key:
                return False
            if not requested_key and not allow_shared_without_key:
                return False

        expected = Authentication._web_proof(mode, key)
        if expected is None or not hmac.compare_digest(proof, expected):
            return False

        if mode == Authentication.ROLE_USER and Authentication.config_state(key, request).disabled:
            # Disabling a configuration immediately freezes browser access too.
            request.apprise_disabled_config_key = key
            return False

        request.apprise_auth_permission = mode
        request.apprise_auth_username = username
        request.apprise_web_auth_key = key
        request.globally_authenticated = mode == Authentication.ROLE_ADMIN
        return True
