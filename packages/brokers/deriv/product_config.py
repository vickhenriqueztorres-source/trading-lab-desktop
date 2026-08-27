from __future__ import annotations

# Public identifier of the Trading Lab PAT application registered at developers.deriv.com.
# This is not a credential. End users provide only their scoped Personal Access Token.
DERIV_PRODUCT_APP_ID = "34ckOjQy8dpzPGZ7EEKwP"


def deriv_product_app_id() -> str:
    if not DERIV_PRODUCT_APP_ID.strip():
        raise RuntimeError("DERIV_PRODUCT_APP_ID_NOT_CONFIGURED")
    return DERIV_PRODUCT_APP_ID
