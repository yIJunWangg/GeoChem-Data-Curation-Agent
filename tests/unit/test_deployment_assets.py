"""Static deployment asset checks that can run without Docker or systemd."""

from pathlib import Path
import json
import os
import subprocess


ROOT = Path(__file__).resolve().parents[2]


def test_systemd_units_render_without_unresolved_placeholders(tmp_path):
    env = os.environ.copy()
    env["GEOCHEM_ENV_FILE"] = str(ROOT / "deploy" / ".env.example")

    completed = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts" / "install-server-services.sh"),
            "--render-only",
            str(tmp_path),
        ],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Rendered GeoChem systemd units" in completed.stdout
    service = (tmp_path / "geochem.service").read_text(encoding="utf-8")
    backup = (tmp_path / "geochem-backup.service").read_text(encoding="utf-8")
    timer = (tmp_path / "geochem-backup.timer").read_text(encoding="utf-8")

    rendered = service + backup + timer
    assert "@GEOCHEM_" not in rendered
    assert "@DOCKER_" not in rendered
    assert str(ROOT / "deploy" / ".env.example") in service
    assert str(ROOT / "deploy" / "docker-compose.yml") in service
    assert "up -d --wait --wait-timeout 360" in service
    assert "scripts/server-verify.sh" in service
    assert "scripts/backup-server.sh" in backup
    assert 'RequiresMountsFor="/mnt/nas"' in backup
    assert "Persistent=true" in timer


def test_production_acceptance_requires_enabled_systemd_lifecycle():
    acceptance = (ROOT / "scripts" / "server-acceptance.sh").read_text(
        encoding="utf-8"
    )

    assert 'systemctl is-enabled --quiet "$unit"' in acceptance
    assert 'systemctl is-active --quiet "$unit"' in acceptance
    assert "geochem.service geochem-backup.timer" in acceptance


def test_compose_migration_initializes_business_and_checkpoint_schemas():
    compose = (ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")

    assert "alembic -c /app/alembic.ini upgrade head" in compose
    assert "geochem-checkpoint-setup" in compose
    assert "service_completed_successfully" in compose


def test_deployment_environment_defines_a_first_application_admin():
    environment = (ROOT / "deploy" / ".env.example").read_text(encoding="utf-8")
    bootstrap = (ROOT / "scripts" / "server-bootstrap.sh").read_text(
        encoding="utf-8"
    )
    preflight = (ROOT / "scripts" / "server-preflight.sh").read_text(
        encoding="utf-8"
    )

    assert "GEOCHEM_INITIAL_ADMIN_USERNAME=geochem-admin" in environment
    assert "GEOCHEM_INITIAL_ADMIN_PASSWORD=replace-with-a-long-random-password" in environment
    assert "GEOCHEM_INITIAL_ADMIN_PASSWORD" in bootstrap
    assert "GEOCHEM_INITIAL_ADMIN_PASSWORD" in preflight
    assert "initial GeoChem administrator username is valid" in preflight


def test_keycloak_configuration_creates_only_missing_initial_admin():
    configuration = (ROOT / "scripts" / "configure-keycloak-client.sh").read_text(
        encoding="utf-8"
    )

    create_guard = configuration.index('if [[ -z "$initial_admin_uuid" ]]')
    create_user = configuration.index("create users -r geochem", create_guard)
    set_password = configuration.index("set-password", create_user)
    existing_user_branch = configuration.index("else", set_password)

    assert create_guard < create_user < set_password < existing_user_branch
    assert 'requiredActions=["UPDATE_PASSWORD"]' in configuration
    assert '--rolename admin' in configuration
    assert "its password was not changed" in configuration


def test_server_verify_checks_checkpoint_schema_and_application_admin():
    verification = (ROOT / "scripts" / "server-verify.sh").read_text(
        encoding="utf-8"
    )

    for table in (
        "checkpoint_migrations",
        "checkpoints",
        "checkpoint_blobs",
        "checkpoint_writes",
    ):
        assert table in verification
    assert "LangGraph PostgreSQL checkpoint schema is initialized" in verification
    assert "Keycloak service account can read an initial GeoChem administrator" in verification
    assert "/api/v1/auth/config" in verification
    assert "geochem.health.probe" in verification
    assert "result.get(timeout=30)" in verification
    assert "Redis broker/result-backend round trip" in verification


def test_production_keycloak_client_accepts_only_the_configured_https_origin():
    configuration = (ROOT / "scripts" / "configure-keycloak-client.sh").read_text(
        encoding="utf-8"
    )
    realm = json.loads(
        (ROOT / "deploy" / "keycloak" / "realm-geochem.json").read_text(
            encoding="utf-8"
        )
    )
    web_client = next(
        client for client in realm["clients"] if client["clientId"] == "geochem-web"
    )

    assert r'redirectUris=[\"$site_redirect\"]' in configuration
    assert r'webOrigins=[\"$site_origin\"]' in configuration
    assert "localhost" not in configuration
    assert "http://127.0.0.1:*/*" not in configuration
    assert web_client["redirectUris"] == ["https://geochem.lan/*"]
    assert web_client["webOrigins"] == ["https://geochem.lan"]


def test_keycloak_uses_the_geochem_login_theme():
    realm = json.loads(
        (ROOT / "deploy" / "keycloak" / "realm-geochem.json").read_text(
            encoding="utf-8"
        )
    )
    compose = (ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
    theme = ROOT / "deploy" / "keycloak" / "themes" / "geochem" / "login"

    assert realm["loginTheme"] == "geochem"
    assert (theme / "theme.properties").exists()
    assert (theme / "resources" / "css" / "login.css").exists()
    assert "/opt/keycloak/themes/geochem:ro" in compose


def test_model_credential_master_key_is_a_docker_secret():
    compose = (ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
    bootstrap = (ROOT / "scripts" / "server-bootstrap.sh").read_text(encoding="utf-8")
    preflight = (ROOT / "scripts" / "server-preflight.sh").read_text(encoding="utf-8")

    assert "GEOCHEM_CREDENTIAL_MASTER_KEY_FILE: /run/secrets/geochem_credential_master_key" in compose
    assert "file: ./secrets/credential_master_key" in compose
    assert "openssl rand -hex 32" in bootstrap
    assert "exactly 64 hexadecimal characters" in preflight


def test_production_acceptance_always_writes_a_durable_report():
    acceptance = (ROOT / "scripts" / "server-acceptance.sh").read_text(
        encoding="utf-8"
    )

    assert 'trap on_exit EXIT' in acceptance
    assert 'REPORT_DIR="${GEOCHEM_ACCEPTANCE_REPORT_DIR:' in acceptance
    assert '"$REPORT_DIR/summary.md"' in acceptance
    assert '"$REPORT_DIR/summary.json"' in acceptance
    assert 'GEOCHEM_LOAD_REPORT="$REPORT_DIR/capacity-report.json"' in acceptance
    assert 'ACCEPTANCE_STATUS="partial"' in acceptance
    assert "exit 3" in acceptance


def test_mac_preview_rejects_a_stale_running_python_build():
    preview = (ROOT / "scripts" / "mac-preview.sh").read_text(encoding="utf-8")

    assert "GEOCHEM_BUILD_ID" in preview
    assert 'running_build_id' in preview
    assert "is an older process" in preview
    assert "Mode:  development (SQLite, local tasks, login disabled)" in preview


def test_wsl2_entry_installs_windows_names_and_local_ca():
    entrypoint = (ROOT / "scripts" / "wsl2-deploy.sh").read_text(encoding="utf-8")
    windows_setup = (
        ROOT / "deploy" / "windows" / "install-geochem-lan.ps1"
    ).read_text(encoding="utf-8")

    assert "server-deploy.sh" in entrypoint
    assert "export-caddy-ca.sh" in entrypoint
    assert "install-geochem-lan.ps1" in entrypoint
    assert "wsl2-acceptance-report.txt" in entrypoint
    assert "wsl2-login-acceptance.sh" in entrypoint
    assert "WSL2 infrastructure integration passed" in entrypoint
    assert "WSL2 integration passed" not in entrypoint
    assert "Import-Certificate" in windows_setup
    assert "BEGIN GEOCHEM LAN" in windows_setup
    assert "Clear-DnsClientCache" in windows_setup


def test_wsl2_login_acceptance_requires_real_admin_browser_activity():
    acceptance = (ROOT / "scripts" / "wsl2-login-acceptance.sh").read_text(
        encoding="utf-8"
    )

    assert "/api/v1/auth/me" in acceptance
    assert "/api/v1/admin/overview" in acceptance
    assert "api_audit_events" in acceptance
    assert "user_profiles" in acceptance
    assert "user_storage_quotas" in acceptance
    assert "roles_json LIKE" in acceptance
    assert '\\"admin\\"' in acceptance


def test_caddy_applies_a_restrictive_application_content_security_policy():
    caddyfile = (ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8")

    assert "Content-Security-Policy" in caddyfile
    assert "default-src 'self'" in caddyfile
    assert "object-src 'none'" in caddyfile
    assert "frame-ancestors 'none'" in caddyfile
    assert "worker-src 'self' blob:" in caddyfile
    assert "https://{$GEOCHEM_AUTH_HOST:auth.geochem.lan}" in caddyfile


def test_restore_validates_snapshot_before_stopping_services_and_verifies_result():
    restore = (ROOT / "scripts" / "restore-server.sh").read_text(encoding="utf-8")

    dump_validation = restore.index("Snapshot database dump is missing or empty")
    stop_services = restore.index('stop caddy web worker keycloak redis qdrant')
    assert dump_validation < stop_services
    assert "geochem-restore.lock" in restore
    assert "configure-keycloak-client.sh" in restore
    assert "server-verify.sh" in restore
    assert "up -d --wait --wait-timeout 360" in restore
