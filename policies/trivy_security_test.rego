package signalos.security_test

import rego.v1
import data.signalos.security

# Test 1: Actionable CRITICAL CVE with an available fix must trigger deny and reject allow
test_deny_actionable_critical_cve if {
    mock_report := {
        "Results": [
            {
                "Target": "signalos:ci (debian 12.8)",
                "Vulnerabilities": [
                    {
                        "VulnerabilityID": "CVE-2023-9999",
                        "PkgName": "libssl3",
                        "InstalledVersion": "3.0.11-1",
                        "FixedVersion": "3.0.11-2",
                        "Severity": "CRITICAL",
                    },
                ],
            },
        ],
    }

    denials := security.deny with input as mock_report
    count(denials) == 1

    not security.allow with input as mock_report
}

# Test 2: Clean report, omitted Vulnerabilities key, and omitted FixedVersion must not deny
test_allow_clean_or_unfixable_report if {
    mock_report := {
        "Results": [
            # Target with NO Vulnerabilities key (typical Trivy clean target output)
            {
                "Target": "clean-base-layer (debian 12.8)",
            },
            # Target with CRITICAL finding having NO FixedVersion key and a fixable HIGH finding
            {
                "Target": "app-layer (python:3.11-slim)",
                "Vulnerabilities": [
                    {
                        "VulnerabilityID": "CVE-2023-1111",
                        "PkgName": "linux-base",
                        "InstalledVersion": "4.5",
                        "Severity": "CRITICAL",
                        # Notice: FixedVersion key is completely omitted
                    },
                    {
                        "VulnerabilityID": "CVE-2023-2222",
                        "PkgName": "curl",
                        "InstalledVersion": "7.88.1",
                        "FixedVersion": "7.88.2",
                        "Severity": "HIGH",
                    },
                ],
            },
        ],
    }

    denials := security.deny with input as mock_report
    count(denials) == 0

    security.allow with input as mock_report
}

# Test 3: Completely empty input report must safely evaluate to allow
test_allow_empty_report if {
    empty_report := {}

    denials := security.deny with input as empty_report
    count(denials) == 0

    security.allow with input as empty_report
}
