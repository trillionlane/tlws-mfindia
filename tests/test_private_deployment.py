from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dev_deployment_is_internal_and_authenticated_only() -> None:
    workflow = (ROOT / ".github" / "workflows" / "deploy-dev.yml").read_text()

    assert "--ingress=internal" in workflow
    assert "--no-allow-unauthenticated" in workflow
    assert "--ingress=all" not in workflow
    assert "--allow-unauthenticated" not in workflow
    assert 'member="serviceAccount:$INSIGHTS_RUNTIME_SERVICE_ACCOUNT"' in workflow
    assert "bash scripts/private_read_invokers_dev.sh reconcile" in workflow
    assert "bash scripts/private_read_invokers_dev.sh required" in workflow
    assert "roles/run.invoker" in workflow


def test_dev_deployment_promotes_latest_revision_without_dropping_named_tags() -> None:
    workflow = (ROOT / ".github" / "workflows" / "deploy-dev.yml").read_text()

    read_service_block = workflow.split("      - name: Deploy private read-only service", 1)[
        1
    ].split("      - name: Deploy private association writer disabled", 1)[0]
    assert 'gcloud run services update-traffic "$SERVICE_NAME"' in read_service_block
    assert "--to-latest" in read_service_block
    assert "--clear-tags" not in read_service_block

    reconcile_block = workflow.split(
        "      - name: Reconcile private service and immutable revision", 1
    )[1].split("      - name: Reconcile the optional association writer revision", 1)[0]
    assert "select((.percent // 0) == 100)" in reconcile_block
    assert 'test "$serving_revision" = "$latest_ready_revision"' in reconcile_block


def test_dev_preflight_verifies_deployed_insights_and_tag_runtime_before_mutation() -> None:
    workflow = (ROOT / ".github" / "workflows" / "deploy-dev.yml").read_text()

    assert "INSIGHTS_SERVICE_NAME: trillion-insights-api-dev" in workflow
    assert 'gcloud run services describe "$INSIGHTS_SERVICE_NAME"' in workflow
    assert "value(spec.template.spec.serviceAccountName)" in workflow
    assert ' = "$INSIGHTS_RUNTIME_SERVICE_ACCOUNT"' in workflow
    assert "gcloud run services describe tl-dwf-dev-api" in workflow
    assert ' = "tl-dwf-dev-runtime@trillionlane-dev.iam.gserviceaccount.com"' in workflow
    preflight = workflow.split(
        "      - name: Verify dedicated tag runtime prerequisites before mutation", 1
    )[1].split("      - name: Verify private-read access before publishing", 1)[0]
    assert 'gcloud iam service-accounts describe "$TAG_RUNTIME_SERVICE_ACCOUNT"' in preflight
    assert 'gcloud projects get-iam-policy "$EXPECTED_PROJECT"' in preflight
    assert "roles/cloudsql.client" in preflight
    assert "roles/iam.serviceAccountUser" in preflight
    assert "tlws-mf-data-tag-writer-dsn-dev" in preflight
    assert "roles/secretmanager.secretAccessor" in preflight
    assert preflight.count(".condition? == null") == 4
    assert 'ingest_member="serviceAccount:$INGEST_SERVICE_ACCOUNT"' in preflight
    assert 'test "$TAG_RUNTIME_SERVICE_ACCOUNT" != "$INGEST_SERVICE_ACCOUNT"' in preflight
    assert "Tag or ingest runtime has project-wide secret access" in preflight
    assert "tlws-mf-data-ingest-dsn-dev" in preflight
    assert "$members == ([$tag] | sort) or $members == ([$tag,$ingest] | sort)" in preflight
    assert '"/app/scripts/refresh_fund_tags_after_success.py","daily-amfi-refresh"' in preflight
    assert '["/app/scripts/refresh_daily_v2.py"]' in preflight
    assert workflow.index(
        "Verify dedicated tag runtime prerequisites before mutation"
    ) < workflow.index("Build and publish immutable image")


def test_dev_deployment_uses_internal_zero_retry_smoke_job() -> None:
    workflow = (ROOT / ".github" / "workflows" / "deploy-dev.yml").read_text()

    assert "tlws-mf-data-private-smoke-dev" in workflow
    assert "VPC_NETWORK: tl-dev-vpc" in workflow
    assert "VPC_SUBNET: tl-dev-mumbai" in workflow
    assert '--network="$VPC_NETWORK"' in workflow
    assert '--subnet="$VPC_SUBNET"' in workflow
    assert "--vpc-egress=all-traffic" in workflow
    assert "--args=/app/scripts/smoke_private_api.py" in workflow
    assert "--max-retries=0" in workflow
    assert "MFDATAINDIA_PRIVATE_BASE_URL" in workflow
    assert "MFDATAINDIA_PRIVATE_AUDIENCE" in workflow
    assert "curl --fail" not in workflow

    verify = (ROOT / ".github" / "workflows" / "verify.yml").read_text()
    assert 'Path("/app/scripts/smoke_private_api.py").is_file()' in verify


def test_private_network_preflight_is_read_only_and_non_deploying() -> None:
    script = (ROOT / "scripts" / "verify_private_dev_network.sh").read_text()

    assert "trillion-insights-runtime-dev" in script
    assert "tl-dev-vpc" in script
    assert "tl-dev-mumbai" in script
    assert "roles/run.serviceAgent" in script
    assert "privateIpGoogleAccess" in script
    assert "add-iam-policy-binding" not in script
    assert "gcloud run deploy" not in script
    assert "gcloud run jobs execute" not in script
    assert "roles/run.invoker" not in script


def test_association_writer_is_separate_private_and_default_off() -> None:
    workflow = (ROOT / ".github" / "workflows" / "deploy-dev.yml").read_text()

    assert "ASSOCIATION_SERVICE_NAME: tlws-mf-data-association-writer-dev" in workflow
    assert "MFDATAINDIA_ASSOCIATION_WRITER_PROVISIONED || 'false'" in workflow
    assert "if: env.ASSOCIATION_WRITER_PROVISIONED == 'true'" in workflow
    required_variables = next(
        line for line in workflow.splitlines() if line.strip().startswith("for name in PROJECT_ID")
    )
    assert "ASSOCIATION_WRITER_SERVICE_ACCOUNT" not in required_variables
    assert "tlws-mf-data-association-writer-dsn-dev:latest" in workflow
    assert "mfdataindia.api.association_app:create_association_app" in workflow
    assert "MFDATAINDIA_ASSOCIATION_WRITES_ENABLED=false" in workflow
    assert "--ingress=internal" in workflow
    assert "--no-allow-unauthenticated" in workflow
    assert "serviceAccount:$INSIGHTS_RUNTIME_SERVICE_ACCOUNT" in workflow
    assert "([$insights] | sort)" in workflow
    assert "ASSOCIATION_RUNTIME_SERVICE_ACCOUNT: tlws-mf-assoc-writer-dev@" in workflow
    assert "--to-latest" in workflow
    assert "verify_optional_association_writer_dev.sh" in workflow


def test_read_deployment_cuts_over_refresh_job_before_legacy_secret_revoke() -> None:
    workflow = (ROOT / ".github" / "workflows" / "deploy-dev.yml").read_text()

    assert "REFRESH_JOB: tlws-mf-data-nav-refresh-dev" in workflow
    assert "Cut over NAV job before revoking legacy tag-secret access" in workflow
    assert '--image="${{ steps.image.outputs.uri }}"' in workflow
    assert '--service-account="$INGEST_SERVICE_ACCOUNT"' in workflow
    assert "--set-env-vars=MFDATAINDIA_SNAPSHOT_SCOPE=FULL" in workflow
    assert "--args=/app/scripts/refresh_daily_v2.py" in workflow
    assert "TAG_REFRESH_JOB: tlws-mf-data-tag-refresh-dev" in workflow
    assert "--args=/app/scripts/publish_fund_tags.py" in workflow
    assert "MFDATAINDIA_TAG_REFRESH_DSN=tlws-mf-data-tag-writer-dsn-dev:latest" in workflow
    assert "refresh_scope" in workflow
    assert 'gcloud run jobs execute "$REFRESH_JOB"' not in workflow
    assert "gcloud scheduler" not in workflow
    cutover = workflow.split(
        "      - name: Cut over NAV job before revoking legacy tag-secret access", 1
    )[1].split("      - name: Deploy and execute the zero-retry migration job", 1)[0]
    assert "MFDATAINDIA_TAG_REFRESH_DSN" in cutover
    assert "status.completionTime" in cutover
    assert "gcloud secrets remove-iam-policy-binding tlws-mf-data-tag-writer-dsn-dev" in cutover
    assert cutover.index("gcloud run jobs deploy") < cutover.index(
        "gcloud secrets remove-iam-policy-binding"
    )


def test_tag_activation_uses_direct_owner_writer_and_reader_principals() -> None:
    deploy = (ROOT / ".github/workflows/deploy-dev.yml").read_text()
    refresh = (ROOT / ".github/workflows/refresh-nav-dev.yml").read_text()
    provision = (ROOT / "scripts/provision_dev_infra.sh").read_text()
    installer = (ROOT / "scripts/install_fund_tagging.py").read_text()

    assert "TAG_INSTALL_JOB: tlws-mf-data-tag-install-dev" in deploy
    assert "--args=/app/scripts/install_fund_tagging.py" in deploy
    assert "--max-retries=0" in deploy
    assert "MFDATAINDIA_DSN=tlws-mf-data-admin-dsn-dev:latest" in deploy
    assert "MFDATAINDIA_DSN=tlws-mf-review-read-dsn-dev:latest" in deploy
    assert "mfdataindia.tagging.values_app:create_app" in deploy
    for workflow in (deploy, refresh):
        assert "MFDATAINDIA_DSN=tlws-mf-data-ingest-dsn-dev:latest" in workflow
        assert "--args=/app/scripts/refresh_daily_v2.py" in workflow
    tag_refresh = (ROOT / ".github/workflows/refresh-tags-dev.yml").read_text()
    assert "MFDATAINDIA_TAG_REFRESH_DSN=tlws-mf-data-tag-writer-dsn-dev:latest" in deploy
    assert "tlws-mf-data-tag-writer-dsn-dev" in tag_refresh
    for workflow in (deploy, tag_refresh):
        assert "/app/scripts/publish_fund_tags.py" in workflow
    assert "MFDATAINDIA_DSN=tlws-mf-data-ingest-dsn-dev:latest" not in tag_refresh
    assert "value(spec.template.spec.containers[0].image)" in tag_refresh
    assert 'containers[0].image\' <<<"$job_json")" = "$live_image"' in tag_refresh
    assert "TAG_RUNTIME_SERVICE_ACCOUNT: tlws-mf-data-tag-writer-dev@" in deploy
    assert "TAG_RUNTIME_SERVICE_ACCOUNT: tlws-mf-data-tag-writer-dev@" in tag_refresh
    assert '--service-account="$TAG_RUNTIME_SERVICE_ACCOUNT"' in deploy
    assert 'serviceAccountName\' <<<"$job_json")" = "$TAG_RUNTIME_SERVICE_ACCOUNT"' in tag_refresh
    validation_shell = (
        tag_refresh.split("      - name: Validate supervised execution contract", 1)[1]
        .split("      - id: auth", 1)[0]
        .split("        run: |", 1)[1]
    )
    assert "${{ inputs." not in validation_shell
    assert 'test "$CONFIRM_PROJECT" = "$EXPECTED_PROJECT"' in validation_shell
    assert 'test "$CONFIRM_JOB" = "$TAG_REFRESH_JOB"' in validation_shell
    assert "PRIVATE_SMOKE_JOB: tlws-mf-data-private-smoke-dev" in tag_refresh
    assert "Verify published tags through packaged authenticated service" in tag_refresh
    assert 'containers[0].args == ["/app/scripts/smoke_private_api.py"]' in tag_refresh
    assert "--args=/app/scripts/smoke_private_api.py,--require-tags" in tag_refresh
    assert 'id: publication' in tag_refresh
    assert 'labels.\\"run.googleapis.com/execution_name\\"' in tag_refresh
    assert "steps.publication.outputs.published_snapshot" in tag_refresh
    assert "--expected-tag-snapshot" in tag_refresh
    assert 'gcloud run jobs execute "$PRIVATE_SMOKE_JOB"' in tag_refresh
    assert (
        "create_database_principal mfdata_tag_writer tlws-mf-data-tag-writer-dsn-dev" in provision
    )
    assert "gcloud sql users assign-roles mfdata_tag_writer" in provision
    assert 'ensure_secret_access tlws-mf-data-tag-writer-dsn-dev "$TAG_WRITER_SA"' in provision
    assert 'ensure_secret_access tlws-mf-data-ingest-dsn-dev "$TAG_WRITER_SA"' not in provision
    assert 'remove_secret_access' not in provision
    assert "deployment workflow first replaces the legacy NAV job" in provision
    assert "ensure_service_account tlws-mf-data-tag-writer-dev" in provision
    assert 'ensure_project_role "serviceAccount:$DEPLOY_SA" roles/iam.securityReviewer' in provision
    assert "store.install(conn, ROOT)" in installer
    assert '"UPGRADED"' in installer
    assert "047_tag_input_publications.sql" in installer
    assert "TAGGING_INSTALL_FAILED" in installer


def test_association_activation_is_manual_exact_sha_bound() -> None:
    workflow = (ROOT / ".github" / "workflows" / "enable-association-writes-dev.yml").read_text()

    assert "workflow_dispatch:" in workflow
    assert "push:" not in workflow
    assert "confirm_git_sha" in workflow
    assert "ENABLE_ASSOCIATION_WRITES" in workflow
    assert "MFDATAINDIA_ASSOCIATION_WRITES_ENABLED=true" in workflow
    assert "--allow-unauthenticated" not in workflow
    assert 'test "$deployed_sha" = "${{ inputs.confirm_git_sha }}"' in workflow
    assert "group: tlws-mf-data-dev-mutations" in workflow
    assert "--no-traffic" in workflow
    assert "actions/checkout@v4" in workflow
    assert "promote_association_revision_dev.sh" in workflow

    promotion = (ROOT / "scripts" / "promote_association_revision_dev.sh").read_text()
    assert "trap rollback_on_exit EXIT" in promotion
    assert '--to-revisions="$enabled_revision=100"' in promotion
    assert '--to-revisions="$disabled_revision=100"' in promotion

    deploy = (ROOT / ".github" / "workflows" / "deploy-dev.yml").read_text()
    assert "group: tlws-mf-data-dev-mutations" in deploy


def test_association_writer_gets_dedicated_identity_and_database_principal() -> None:
    provision = (ROOT / "scripts" / "provision_dev_infra.sh").read_text()
    migration = (ROOT / "sql" / "019_reconcile_association_writer_privileges.sql").read_text()

    assert "tlws-mf-assoc-writer-dev@" in provision
    assert "tlws-mf-data-association-writer-dev@" not in provision
    assert "mfdata_association_writer" in provision
    assert "tlws-mf-data-association-writer-dsn-dev" in provision
    assert "gcloud sql users assign-roles mfdata_association_writer" in provision
    assert "--database-roles=" in provision
    assert "--revoke-existing-roles" in provision
    assert "REVOKE cloudsqlsuperuser FROM mfdata_association_writer" in migration
    assert "ALTER ROLE mfdata_association_writer NOCREATEROLE NOCREATEDB" in migration
    assert "GRANT SELECT ON mf.fund_family TO mfdata_association_writer" in migration
    assert "GRANT SELECT, INSERT ON mf.fund_family_association_tags" in migration
    assert "DELETE ON mf.fund_family_association_tags" not in migration
    assert (
        '"019_reconcile_association_writer_privileges.sql"'
        in (ROOT / "src" / "mfdataindia" / "store" / "postgres.py").read_text()
    )


def test_supervised_refresh_is_explicitly_a_full_snapshot_candidate() -> None:
    workflow = (ROOT / ".github" / "workflows" / "refresh-nav-dev.yml").read_text()
    assert "MFDATAINDIA_SNAPSHOT_SCOPE=FULL" in workflow


def test_supervised_refresh_uses_internal_api_verification() -> None:
    workflow = (ROOT / ".github" / "workflows" / "refresh-nav-dev.yml").read_text()

    assert "group: tlws-mf-data-dev-mutations" in workflow
    assert "PRIVATE_SMOKE_JOB: tlws-mf-data-private-smoke-dev" in workflow
    assert 'gcloud run jobs execute "$REFRESH_JOB"' in workflow
    assert 'gcloud run jobs execute "$PRIVATE_SMOKE_JOB"' in workflow
    assert "smoke_private_api.py" in workflow
    assert "curl " not in workflow
    assert "/api/stats" not in workflow
