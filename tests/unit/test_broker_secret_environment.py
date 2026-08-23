from packages.security import without_broker_credentials


def test_non_broker_children_never_inherit_broker_credentials() -> None:
    sanitized = without_broker_credentials(
        {
            "PATH": "safe-placeholder",
            "DUALTRADE_DERIV_DEMO_TOKEN": "must-not-cross",
            "DUALTRADE_DERIV_APP_ID": "must-not-cross",
            "DUALTRADE_IQOPTION_SESSION": "must-not-cross",
        }
    )

    assert sanitized == {"PATH": "safe-placeholder"}
