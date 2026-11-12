from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock

# Import mcp-ci-doctor/ci_doctor_server.py without namespace collisions
MCP_SERVER_PATH = Path(__file__).resolve().parent.parent / "mcp-ci-doctor" / "ci_doctor_server.py"
spec = importlib.util.spec_from_file_location("mcp_ci_doctor_server", MCP_SERVER_PATH)
if spec is None or spec.loader is None:
    raise ImportError(f"Cannot load MCP doctor server from {MCP_SERVER_PATH}")
mcp_doctor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mcp_doctor)

triage_vulnerabilities = mcp_doctor.triage_vulnerabilities
diagnose_pipeline_failure = mcp_doctor.diagnose_pipeline_failure
fetch_ci_logs = mcp_doctor.fetch_ci_logs


def test_triage_vulnerabilities_deduplication_and_sorting(tmp_path: Path):
    """Verify deduplication, severity filtering, and sorting of Trivy findings."""
    mock_report = {
        "SchemaVersion": 2,
        "ArtifactName": "signalos:ci",
        "Results": [
            {
                "Target": "signalos:ci (debian 12)",
                "Vulnerabilities": [
                    {
                        "VulnerabilityID": "CVE-2023-1111",
                        "PkgName": "openssl",
                        "InstalledVersion": "3.0.11",
                        "FixedVersion": "",
                        "Severity": "CRITICAL",
                        "Title": "OpenSSL memory corruption",
                    },
                    {
                        "VulnerabilityID": "CVE-2023-2222",
                        "PkgName": "glibc",
                        "InstalledVersion": "2.36",
                        "FixedVersion": "2.36-9+deb12u4",
                        "Severity": "CRITICAL",
                        "Title": "glibc buffer overflow",
                    },
                    {
                        "VulnerabilityID": "CVE-2023-3333",
                        "PkgName": "curl",
                        "InstalledVersion": "7.88",
                        "FixedVersion": "7.88.1",
                        "Severity": "HIGH",
                        "Title": "curl SOCKS issue",
                    },
                    {
                        "VulnerabilityID": "CVE-2023-4444",
                        "PkgName": "libssh",
                        "InstalledVersion": "0.10",
                        "FixedVersion": "",
                        "Severity": "HIGH",
                        "Title": "libssh timing attack",
                    },
                    {
                        "VulnerabilityID": "CVE-2023-5555",
                        "PkgName": "tar",
                        "InstalledVersion": "1.34",
                        "FixedVersion": "1.35",
                        "Severity": "MEDIUM",
                        "Title": "Tar directory traversal",
                    },
                    {
                        "VulnerabilityID": "CVE-2023-6666",
                        "PkgName": "bash",
                        "InstalledVersion": "5.2",
                        "FixedVersion": "5.2.1",
                        "Severity": "LOW",
                        "Title": "Bash minor bug",
                    },
                ],
            },
            {
                "Target": "python:3.11-slim (app-layer)",
                "Vulnerabilities": [
                    # Duplicate of CVE-2023-2222 across targets
                    {
                        "VulnerabilityID": "CVE-2023-2222",
                        "PkgName": "glibc",
                        "InstalledVersion": "2.36",
                        "FixedVersion": "2.36-9+deb12u4",
                        "Severity": "CRITICAL",
                        "Title": "glibc buffer overflow",
                    },
                    # Duplicate of CVE-2023-3333 across targets
                    {
                        "VulnerabilityID": "CVE-2023-3333",
                        "PkgName": "curl",
                        "InstalledVersion": "7.88",
                        "FixedVersion": "7.88.1",
                        "Severity": "HIGH",
                        "Title": "curl SOCKS issue",
                    },
                ],
            },
        ],
    }

    report_path = tmp_path / "trivy-results.json"
    report_path.write_text(json.dumps(mock_report), encoding="utf-8")

    result = triage_vulnerabilities(str(report_path))

    # Total raw: 6 in target 1 + 2 in target 2 = 8
    assert result["total_raw_findings"] == 8

    # Deduplicated HIGH/CRITICAL: CVE-2023-1111, 2222, 3333, 4444 (MEDIUM & LOW filtered out)
    assert result["unique_high_critical_count"] == 4

    # Actionable (with FixedVersion): CVE-2023-2222, CVE-2023-3333 = 2
    assert result["actionable_patches_available"] == 2

    vulns = result["vulnerabilities"]
    assert len(vulns) == 4

    # Order verification:
    # 1. CRITICAL with fix: CVE-2023-2222
    assert vulns[0]["cve_id"] == "CVE-2023-2222"
    assert vulns[0]["severity"] == "CRITICAL"
    assert vulns[0]["fixed_version"] == "2.36-9+deb12u4"
    assert len(vulns[0]["targets"]) == 2

    # 2. CRITICAL without fix: CVE-2023-1111
    assert vulns[1]["cve_id"] == "CVE-2023-1111"
    assert vulns[1]["severity"] == "CRITICAL"
    assert vulns[1]["fixed_version"] == ""

    # 3. HIGH with fix: CVE-2023-3333
    assert vulns[2]["cve_id"] == "CVE-2023-3333"
    assert vulns[2]["severity"] == "HIGH"
    assert vulns[2]["fixed_version"] == "7.88.1"

    # 4. HIGH without fix: CVE-2023-4444
    assert vulns[3]["cve_id"] == "CVE-2023-4444"
    assert vulns[3]["severity"] == "HIGH"
    assert vulns[3]["fixed_version"] == ""


def test_diagnose_pytest_failure_offline():
    """Verify diagnosis of pytest failure logs without external dependencies."""
    log_sample = """
    =================================== FAILURES ===================================
    ____________________ test_action_categorization_and_intent _____________________
    tests/test_signalos.py:120: in test_action_categorization_and_intent
        assert server.infer_type(appointment_text) == "event"
    E   AssertionError: assert 'task' == 'event'
    FAILED tests/test_signalos.py::test_action_categorization_and_intent - AssertionError
    =========================== 1 failed, 9 passed in 0.44s ========================
    """
    res = diagnose_pipeline_failure(log_sample)
    assert res["failure_category"] == "test_assertion_failure"
    assert res["suspected_component"] == "test_suite_or_logic"
    assert any("test_action_categorization_and_intent" in f for f in res["deterministic_findings"])
    assert len(res["remediation_checklist"]) >= 2
    assert res["ollama_available"] is False


def test_diagnose_docker_build_failure_offline():
    """Verify diagnosis of Docker build failures without external dependencies."""
    docker_log = """
    #8 [4/6] RUN pip install --no-cache-dir -r requirements.txt
    #8 2.145 ERROR: Could not find a version that satisfies the requirement invalid_pkg
    #8 ERROR: executor failed running [/bin/sh -c pip install --no-cache-dir -r requirements.txt]: exit code: 1
    ------
    ERROR: failed to solve: process "/bin/sh -c pip install" did not complete successfully: exit code: 1
    """
    res = diagnose_pipeline_failure(docker_log)
    assert res["failure_category"] == "container_build_failure"
    assert res["suspected_component"] == "dockerfile_or_build_context"
    assert any("container build" in f.lower() for f in res["deterministic_findings"])
    assert any("docker" in step.lower() for step in res["remediation_checklist"])


def test_diagnose_postgres_and_import_errors():
    """Verify diagnosis of Python missing import and database connection errors."""
    import_log = "ModuleNotFoundError: No module named 'psycopg'"
    res_import = diagnose_pipeline_failure(import_log)
    assert res_import["failure_category"] == "dependency_import_error"
    assert res_import["suspected_component"] == "python_requirements"
    assert "psycopg" in res_import["deterministic_findings"][0]

    db_log = 'connection to server at "db" (172.18.0.2), port 5432 failed: Connection refused'
    res_db = diagnose_pipeline_failure(db_log)
    assert res_db["failure_category"] == "database_connection_refused"
    assert res_db["suspected_component"] == "postgres_service_or_network"


def test_fetch_ci_logs_mocked(monkeypatch):
    """Verify fetch_ci_logs handles successful and 404 responses via mocked HTTP client."""
    mock_payload = {
        "total_count": 1,
        "jobs": [
            {
                "name": "lint-and-test",
                "conclusion": "failure",
                "html_url": "https://github.com/my-org/SignalOS/actions/runs/1234/jobs/5678",
                "steps": [
                    {"name": "Checkout", "number": 1, "conclusion": "success"},
                    {"name": "Run linter", "number": 4, "conclusion": "failure"},
                ],
            }
        ],
    }

    mock_resp_success = MagicMock()
    mock_resp_success.status_code = 200
    mock_resp_success.json.return_value = mock_payload

    monkeypatch.setattr(mcp_doctor.httpx, "get", lambda *args, **kwargs: mock_resp_success)
    summary = fetch_ci_logs("my-org/SignalOS", 1234)

    assert "Run #1234" in summary
    assert "lint-and-test" in summary
    assert "Step #4 `Run linter`: **failure**" in summary

    # Test 404 Not Found response handling
    mock_resp_404 = MagicMock()
    mock_resp_404.status_code = 404
    monkeypatch.setattr(mcp_doctor.httpx, "get", lambda *args, **kwargs: mock_resp_404)
    summary_404 = fetch_ci_logs("my-org/SignalOS", 9999)
    assert "was not found" in summary_404

