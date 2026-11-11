"""How this process authenticates to Keycloak as a client (P1-T8d2, ADR-012).

The Django side of ``ihr_platform.client_keys``. The backend signs for every hospital client and
for the console with its own key, from ``IHR_CLIENT_KEY_DIR`` (a volume only the backend
mounts). The public key sets the sync writes into Keycloak come from ``IHR_CLIENT_JWKS_DIR``
(files the key tool writes; no secrets).
"""

import threading

from django.conf import settings
from ihr_platform import client_keys
from ihr_platform.client_keys import ClientKeyError

__all__ = ["ClientKeyError", "assertion_fields", "public_jwks", "store"]

_lock = threading.Lock()
_stores = {}


def store():
    """This process's own key folder, shared by its threads (one ``stat`` per signature)."""
    directory = settings.IHR_CLIENT_KEY_DIR
    with _lock:
        if directory not in _stores:
            _stores[directory] = client_keys.KeyStore(directory)
        return _stores[directory]


def assertion_fields(client_id, realm=None):
    """Form fields that prove to Keycloak that this request comes from ``client_id``. The
    audience is the realm's public issuer, never the back-channel address the request goes
    to: Keycloak checks the audience against its own public name."""
    from ihr_identity import oidc

    return store().fields(client_id, oidc.issuer(realm))


def public_jwks(purpose):
    """The published key set of ``purpose`` ("backend", "onboarding" or "staff"), read on every
    call so a long-running provisioner picks up a rotation."""
    return client_keys.read_public_jwks(settings.IHR_CLIENT_JWKS_DIR, purpose)
