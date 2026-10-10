from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dev_deployment_is_internal_and_authenticated_only() -> None:
    workflow = (ROOT / ".github" / "workflows" / "deploy-dev.yml").read_text()

    assert "--ingress=internal" in workflow
    assert "--no-allow-unauthenticated" in workflow
    assert "--ingress=all" not in workflow
    assert "--allow-unauthenticated" not in workflow
    assert 'member="serviceAccount:$INSIGHTS_RUNTIME_SERVICE_ACCOUNT"' in workflow
    assert (
        'member="serviceAccount:${{ vars.GCP_TLWS_MF_DATA_RUNTIME_SERVICE_ACCOUNT }}"' in workflow
    )
    assert 'member="allUsers"' in workflow
    assert "roles/run.invoker" in workflow


def test_dev_preflight_verifies_the_deployed_insights_identity_without_iam_read() -> None:
    workflow = (ROOT / ".github" / "workflows" / "deploy-dev.yml").read_text()

    assert "INSIGHTS_SERVICE_NAME: trillion-insights-api-dev" in workflow
    assert 'gcloud run services describe "$INSIGHTS_SERVICE_NAME"' in workflow
    assert "value(spec.template.spec.serviceAccountName)" in workflow
    assert ' = "$INSIGHTS_RUNTIME_SERVICE_ACCOUNT"' in workflow
    assert "gcloud iam service-accounts describe" not in workflow


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
    assert "tlws-mf-data-association-writer-dsn-dev:latest" in workflow
    assert "mfdataindia.api.association_app:create_association_app" in workflow
    assert "MFDATAINDIA_ASSOCIATION_WRITES_ENABLED=false" in workflow
    assert "--ingress=internal" in workflow
    assert "--no-allow-unauthenticated" in workflow
    assert "serviceAccount:$INSIGHTS_RUNTIME_SERVICE_ACCOUNT" in workflow
    assert "([$insights] | sort)" in workflow
    assert "ASSOCIATION_RUNTIME_SERVICE_ACCOUNT: tlws-mf-assoc-writer-dev@" in workflow
    assert "--to-latest" in workflow


def test_association_activation_is_manual_exact_sha_bound() -> None:
    workflow = (
        ROOT / ".github" / "workflows" / "enable-association-writes-dev.yml"
    ).read_text()

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
    migration = (ROOT / "sql" / "018_fund_family_association_tags.sql").read_text()

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


def test_supervised_refresh_is_explicitly_a_full_snapshot_candidate() -> None:
    workflow = (ROOT / ".github" / "workflows" / "refresh-nav-dev.yml").read_text()
    assert "MFDATAINDIA_SNAPSHOT_SCOPE=FULL" in workflow
