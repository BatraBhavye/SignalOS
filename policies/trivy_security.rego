package signalos.security

import rego.v1

# -----------------------------------------------------------------------------
# SignalOS DevSecOps Policy-as-Code Gate for Container Image Vulnerabilities
# Evaluates Trivy JSON scan output (input.Results[].Vulnerabilities[]).
#
# Rules:
# - DENY if any vulnerability has Severity == "CRITICAL" AND a non-empty
#   FixedVersion (actionable critical vulnerability with an available patch).
# - ALLOW if and only if zero actionable violations are found (count(deny) == 0).
# -----------------------------------------------------------------------------

# Default: reject deployment unless explicitly permitted
default allow := false

# Allow deployment when no actionable critical vulnerabilities exist
allow if count(deny) == 0

# Deny and generate descriptive message for each actionable critical vulnerability
deny contains msg if {
    some result in input.Results
    some vuln in result.Vulnerabilities

    vuln.Severity == "CRITICAL"
    is_string(vuln.FixedVersion)
    vuln.FixedVersion != ""

    msg := sprintf(
        "Actionable CRITICAL vulnerability in '%v' (%v): fixed in version '%v' (Installed: '%v')",
        [vuln.PkgName, vuln.VulnerabilityID, vuln.FixedVersion, vuln.InstalledVersion],
    )
}
