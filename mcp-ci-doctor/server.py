from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("SignalOS-CI-Doctor")


@mcp.tool()
def fetch_ci_logs(repo: str, run_id: int) -> str:
    """Fetch and summarize CI job failure details from GitHub Actions.

    Queries the GitHub Actions REST API for a specific workflow run to extract
    failed jobs, steps, execution conclusions, and failure summaries.

    Args:
        repo: Repository in 'owner/repo' format (e.g., 'octocat/Hello-World').
        run_id: GitHub Actions workflow run identifier.

    Returns:
        A formatted markdown summary of failed jobs and steps, or a diagnostic message.
    """
    url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "SignalOS-CI-Doctor",
    }
    github_token = os.getenv("GITHUB_TOKEN", "").strip()
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    try:
        response = httpx.get(url, headers=headers, timeout=10.0)
    except httpx.RequestError as exc:
        return f"Failed to connect to GitHub API for {repo} run {run_id}: {exc}"

    if response.status_code == 404:
        return f"GitHub Actions run #{run_id} in repository '{repo}' was not found. Please verify repo name and run ID."

    if response.status_code in (401, 403):
        rate_limit_remaining = response.headers.get("x-ratelimit-remaining", "")
        if rate_limit_remaining == "0":
            return (
                f"GitHub API rate limit exceeded for repository '{repo}'. "
                "Set a valid GITHUB_TOKEN environment variable to increase your rate limits."
            )
        return (
            f"GitHub API authentication/permission error ({response.status_code}) "
            f"for '{repo}'. Verify GITHUB_TOKEN scope and repository visibility."
        )

    if response.status_code != 200:
        return f"GitHub API returned unexpected HTTP status {response.status_code} for {repo} run {run_id}."

    data = response.json()
    jobs = data.get("jobs", [])
    if not jobs:
        return f"No jobs found for GitHub Actions run #{run_id} in '{repo}'."

    failed_jobs = [
        job for job in jobs
        if job.get("conclusion") in {"failure", "timed_out", "cancelled", "action_required"}
    ]

    if not failed_jobs:
        return (
            f"### GitHub Actions Run #{run_id} ({repo})\n"
            "All jobs completed successfully (or were skipped)."
        )

    summary_lines = [
        f"### GitHub Actions Failure Summary: Run #{run_id} ({repo})",
        f"**Failed Jobs Count:** {len(failed_jobs)}",
        "",
    ]

    for job in failed_jobs:
        job_name = job.get("name", "Unknown Job")
        conclusion = job.get("conclusion", "failure")
        html_url = job.get("html_url", "")
        summary_lines.append(f"#### Job: `{job_name}` (Conclusion: **{conclusion}**)")
        if html_url:
            summary_lines.append(f"- **URL:** {html_url}")

        steps = job.get("steps", [])
        failed_steps = [
            s for s in steps
            if s.get("conclusion") in {"failure", "timed_out", "cancelled"}
        ]

        if failed_steps:
            summary_lines.append("- **Failed Steps:**")
            for step in failed_steps:
                s_name = step.get("name", "Unknown Step")
                s_num = step.get("number", "?")
                s_concl = step.get("conclusion", "failure")
                summary_lines.append(f"  - Step #{s_num} `{s_name}`: **{s_concl}**")
        else:
            summary_lines.append("- **Note:** Job ended without a specific failed step recorded.")
        summary_lines.append("")

    return "\n".join(summary_lines).strip()


@mcp.tool()
def diagnose_pipeline_failure(log_excerpt: str) -> dict[str, Any]:
    """Diagnose CI/CD pipeline and DevOps failures using deterministic heuristics.

    Performs root-cause analysis on log excerpts by pattern-matching known failure
    signatures (missing Python modules, pytest failures, lint violations, Docker build
    layer errors, database connectivity problems, or security policy blocks).
    Optionally enriches with local Ollama if available without blocking.

    Args:
        log_excerpt: The captured failure log or console output from CI/CD.

    Returns:
        Structured dictionary with failure_category, suspected_component,
        deterministic_findings, and remediation_checklist.
    """
    findings: list[str] = []
    checklist: list[str] = []
    category = "unclassified_pipeline_error"
    component = "ci_pipeline_step"

    # 1. Missing Python module / ImportError
    module_match = re.search(r"ModuleNotFoundError:\s+No module named\s+['\"]?([a-zA-Z0-9_\.]+)['\"]?", log_excerpt)
    if module_match:
        missing_pkg = module_match.group(1)
        category = "dependency_import_error"
        component = "python_requirements"
        findings.append(f"Missing Python module: '{missing_pkg}'.")
        checklist.extend([
            f"Add '{missing_pkg}' to requirements.txt or requirements-dev.txt.",
            "Verify dependencies are installed via 'pip install -r requirements.txt -r requirements-dev.txt'.",
            "Check for typos in import statement.",
        ])

    # 2. Pytest assertion failures
    elif re.search(r"(?:AssertionError|FAILED tests/|E\s+assert\b)", log_excerpt):
        category = "test_assertion_failure"
        component = "test_suite_or_logic"
        test_matches = re.findall(r"FAILED\s+(tests/[^\s:]+::[^\s]+)", log_excerpt)
        if test_matches:
            findings.append(f"Failing tests identified: {', '.join(test_matches[:5])}.")
        else:
            findings.append("Pytest assertion failure detected in test execution.")
        checklist.extend([
            "Inspect test assertions, diff output, and expected vs actual return values.",
            "Verify mock fixtures and deterministic reference dates (e.g. frozen clocks).",
            "Run 'pytest tests/ -vv' locally to reproduce the failure.",
        ])

    # 3. Linter violations (Ruff or ShellCheck)
    elif (
        re.search(r"(?:ruff check|shellcheck|SC\d{4}|Found \d+ errors?)", log_excerpt)
        and not re.search(r"docker build", log_excerpt, re.IGNORECASE)
    ):
        category = "lint_or_formatting_violation"
        component = "static_code_analysis"
        findings.append("Linter or static analysis check failed.")
        checklist.extend([
            "Run 'ruff check . --fix' to automatically resolve fixable Python lint issues.",
            "Run 'shellcheck scripts/*.sh' to inspect shell script formatting.",
            "Ensure line endings are normalized to LF in accordance with .gitattributes.",
        ])

    # 4. Docker build layer errors
    elif re.search(
        r"(?:ERROR:\s+failed to solve:|failed to compute cache key|executor failed running|non-zero code:\s*\d+)",
        log_excerpt,
    ):
        category = "container_build_failure"
        component = "dockerfile_or_build_context"
        findings.append("Docker container build failed during layer execution.")
        checklist.extend([
            "Check the failing Dockerfile instruction and exit code.",
            "Verify .dockerignore did not exclude necessary source files.",
            "Check user permissions and filesystem ownership (e.g., non-root appuser vs root).",
            "Run 'docker build -t test-image .' locally to debug build context.",
        ])

    # 5. Postgres connection refused
    elif re.search(
        r"(?:could not connect to server: Connection refused|Is the server running on that host|connection to server at .* failed)",
        log_excerpt,
        re.IGNORECASE,
    ):
        category = "database_connection_refused"
        component = "postgres_service_or_network"
        findings.append("Application unable to establish a connection to PostgreSQL.")
        checklist.extend([
            "Verify the PostgreSQL container is running and healthy (pg_isready probe).",
            "In Docker Compose, verify 'depends_on' uses condition 'service_healthy'.",
            "Check that DATABASE_URL uses the service hostname ('db') rather than localhost.",
            "Verify POSTGRES_USER, POSTGRES_PASSWORD, and POSTGRES_DB match in .env.",
        ])

    # 6. Security policy gate / Trivy scan violation
    elif re.search(r"(?:trivy|trivy-results\.json|opa eval|policy violation|CRITICAL vulnerabilities found)", log_excerpt, re.IGNORECASE):
        category = "security_policy_gate_violation"
        component = "vulnerability_scanner_or_opa_policy"
        findings.append("Security scan or OPA policy gate blocked the build pipeline.")
        checklist.extend([
            "Run 'triage_vulnerabilities' on trivy-results.json to review actionable CVE patches.",
            "Update base container image or pin patched dependencies.",
            "Review OPA policy constraints for compliance requirements.",
        ])

    else:
        findings.append("No specific known failure signature matched the log excerpt.")
        checklist.extend([
            "Inspect complete job logs in GitHub Actions.",
            "Reproduce the failing step locally in an isolated environment.",
        ])

    result: dict[str, Any] = {
        "failure_category": category,
        "suspected_component": component,
        "deterministic_findings": findings,
        "remediation_checklist": checklist,
    }

    # Optional graceful Ollama probe (strict 1.0s timeout to prevent hanging)
    ollama_base_url = os.getenv("OLLAMA_BASE_URL", "").rstrip("/")
    if ollama_base_url:
        try:
            probe = httpx.get(f"{ollama_base_url}/api/tags", timeout=1.0)
            result["ollama_available"] = bool(probe.status_code == 200)
        except Exception:
            result["ollama_available"] = False
    else:
        result["ollama_available"] = False

    return result


@mcp.tool()
def triage_vulnerabilities(trivy_report_path: str) -> dict[str, Any]:
    """Triage and prioritize container vulnerabilities from a Trivy JSON report.

    Reads a Trivy scan report, filters strictly for CRITICAL and HIGH severity findings,
    deduplicates CVEs across targets, and prioritizes actionable items with available patches.

    Args:
        trivy_report_path: Filesystem path to the Trivy JSON report file.

    Returns:
        Structured triage summary with raw and deduplicated counts, actionable patch counts,
        and prioritized vulnerability list sorted by severity and patch availability.
    """
    path = Path(trivy_report_path)
    if not path.is_file():
        return {
            "error": f"Report file not found at: {trivy_report_path}",
            "total_raw_findings": 0,
            "unique_high_critical_count": 0,
            "actionable_patches_available": 0,
            "vulnerabilities": [],
        }

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        return {
            "error": f"Failed to parse JSON from {trivy_report_path}: {exc}",
            "total_raw_findings": 0,
            "unique_high_critical_count": 0,
            "actionable_patches_available": 0,
            "vulnerabilities": [],
        }

    total_raw_findings = 0
    cve_map: dict[str, dict[str, Any]] = {}

    results = data.get("Results", [])
    for result in results:
        target_name = result.get("Target", "Unknown Target")
        vulns = result.get("Vulnerabilities", [])
        if not vulns:
            continue
        total_raw_findings += len(vulns)

        for vuln in vulns:
            severity = str(vuln.get("Severity", "")).upper()
            if severity not in {"CRITICAL", "HIGH"}:
                continue

            cve_id = vuln.get("VulnerabilityID", "").strip()
            if not cve_id:
                continue

            pkg_name = vuln.get("PkgName", "")
            installed_ver = vuln.get("InstalledVersion", "")
            fixed_ver = vuln.get("FixedVersion", "")
            title = vuln.get("Title") or vuln.get("Description") or ""
            if len(title) > 120:
                title = title[:117] + "..."

            if cve_id not in cve_map:
                cve_map[cve_id] = {
                    "cve_id": cve_id,
                    "severity": severity,
                    "package": pkg_name,
                    "installed_version": installed_ver,
                    "fixed_version": fixed_ver,
                    "title": title,
                    "targets": [target_name],
                }
            else:
                existing = cve_map[cve_id]
                if target_name not in existing["targets"]:
                    existing["targets"].append(target_name)
                if not existing["fixed_version"] and fixed_ver:
                    existing["fixed_version"] = fixed_ver
                if severity == "CRITICAL" and existing["severity"] != "CRITICAL":
                    existing["severity"] = "CRITICAL"

    # Sorting priority:
    # 1. Severity: CRITICAL (0) before HIGH (1)
    # 2. Actionability: FixedVersion non-empty (0) before empty (1)
    # 3. CVE ID alphabetically
    def sort_key(item: dict[str, Any]) -> tuple[int, int, str]:
        sev_rank = 0 if item["severity"] == "CRITICAL" else 1
        fix_rank = 0 if item["fixed_version"] else 1
        return (sev_rank, fix_rank, item["cve_id"])

    sorted_vulns = sorted(cve_map.values(), key=sort_key)
    actionable_count = sum(1 for v in sorted_vulns if v["fixed_version"])

    return {
        "report_path": str(path),
        "total_raw_findings": total_raw_findings,
        "unique_high_critical_count": len(sorted_vulns),
        "actionable_patches_available": actionable_count,
        "vulnerabilities": sorted_vulns,
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
