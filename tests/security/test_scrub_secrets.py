from scripts.scrub_secrets import scan_content


def test_secret_scrubber_accepts_env_indirection_but_rejects_literal() -> None:
    assert scan_content('secret_key = "env(SECRET_VALUE)"', "config.toml") == []
    literal_assignment = "secret_" + 'key = "live-' + 'credential-value"'
    violations = scan_content(literal_assignment, "config.toml")
    assert len(violations) == 1
    assert "Exposed Credential" in violations[0]
