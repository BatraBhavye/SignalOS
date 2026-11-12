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

# Deny and generate descriptive message for each actionable critical vulnerability.
# Uses object.get to safely handle clean targets without Vulnerabilities
# and findings where FixedVersion is omitted by Trivy.
deny contains msg if {
    some result in object.get(input, "Results", [])
    some vuln in object.get(result, "Vulnerabilities", [])

    vuln.Severity == "CRITICAL"
    fixed := object.get(vuln, "FixedVersion", "")
    is_string(fixed)
    fixed != ""

    msg := sprintf(
        "Actionable CRITICAL vulnerability in '%v' (%v): fixed in version '%v' (Installed: '%v')",
        [vuln.PkgName, vuln.VulnerabilityID, fixed, vuln.InstalledVersion],
    )
}
