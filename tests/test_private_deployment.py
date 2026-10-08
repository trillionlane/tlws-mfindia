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
