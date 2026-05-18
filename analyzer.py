"""
CAIQ Analyzer
-------------
Evaluates vendor CAIQ responses against CSA CCM v4.1 control specifications
and implementation guidance using the Anthropic Claude API.

Scoring is performed at the domain level — one API call per CCM domain —
using a customizable AI scoring rubric as the system prompt. Results are
written to a JSON file named after the vendor.

Usage:
    python analyzer.py

Requirements:
    - ANTHROPIC_API_KEY set in .env file
    - CSA CCM v4.1 reference file (xlsx)
    - Vendor-completed CAIQ file (xlsx)
    - AI scoring rubric (markdown)
"""

import os
import json
import sys
import anthropic
import pandas as pd
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# Domain weights — all domains are weighted equally by default (1.0).
# Adjust values to reflect your organization's risk priorities.
# For example, set IAM or CEK higher if access control and encryption
# are critical to your risk appetite. Values are normalized automatically,
# so only relative size matters.
# Keys must match domain codes as they appear in CAIQ Question IDs.
# ---------------------------------------------------------------------------
DOMAIN_WEIGHTS = {
    "A&A": 1.0,  # Audit & Assurance
    "AIS": 1.0,  # Application & Interface Security
    "BCR": 1.0,  # Business Continuity Management & Operational Resilience
    "CCC": 1.0,  # Change Control & Configuration Management
    "CEK": 1.0,  # Cryptography, Encryption & Key Management
    "DCS": 1.0,  # Datacenter Security
    "DSP": 1.0,  # Data Security & Privacy Lifecycle Management
    "GRC": 1.0,  # Governance, Risk & Compliance
    "HRS": 1.0,  # Human Resources Security
    "IAM": 1.0,  # Identity & Access Management
    "IPY": 1.0,  # Interoperability & Portability
    "IVS": 1.0,  # Infrastructure & Virtualization Security
    "LOG": 1.0,  # Logging & Monitoring
    "SEF": 1.0,  # Security Incident Management, E-Discovery & Cloud Forensics
    "STA": 1.0,  # Supply Chain Management, Transparency & Accountability
    "TVM": 1.0,  # Threat & Vulnerability Management
    "UEM": 1.0,  # Universal Endpoint Management
}


def prompt_for_path(label: str, default_name: str) -> Path:
    """
    Prompt the user to enter a file path at runtime.
    Shows the default filename as a hint. Keeps asking until a valid path is provided.
    """
    while True:
        raw = input(f"\nPath to {label} [{default_name}]: ").strip()
        if not raw:
            raw = default_name
        path = Path(raw).expanduser()
        if path.exists():
            return path
        print(f"  File not found: {path}  — please try again.")


def load_rubric(path: Path) -> str:
    """
    Load the AI scoring rubric from a markdown file.
    The rubric is passed as the system prompt for every domain API call.
    """
    return path.read_text(encoding="utf- ")


def load_ccm(path: Path) -> dict:
    """
    Load control specifications and implementation guidance from the CCM reference file.

    Reads the 'Implementation Guidelines' sheet (headers on row 3).
    Returns a dict keyed by Control ID (e.g. 'A&A-01'):
      {
        "A&A-01": {
          "domain": "Audit & Assurance",
          "specification": "...",
          "guidance": "..."        # CSP implementation guidance column
        },
        ...
      }
    """
    df = pd.read_excel(
        path, sheet_name="Implementation Guidelines", header=2, dtype=str
    )
    df.fillna("", inplace=True)

    controls = {}
    for _, row in df.iterrows():
        control_id = str(row.get("Control ID", "")).strip()
        if not control_id:
            continue
        controls[control_id] = {
            "domain": str(row.get("Control Domain", "")).strip(),
            "specification": str(row.get("Control Specification", "")).strip(),
            "guidance": str(
                row.get("CSP", "")
            ).strip(),  # CSP column = implementation guidance
        }
    return controls


def load_caiq(path: Path) -> dict:
    """
    Load vendor CAIQ responses from the vendor-completed CAIQ file.

    Automatically detects the sheet name (any sheet starting with 'CAIQv').
    Headers are on row 2 (header=1).

    Returns a dict keyed by domain code (e.g. 'A&A'), where each value is
    a list of question entries for that domain:
      {
        "A&A": [
          {
            "question_id":  "A&A-01.1",
            "control_id":   "A&A-01",   # stripped of question suffix for CCM join
            "question":     "...",
            "answer":       "Yes",
            "description":  "...",      # vendor's supporting comments
            "ssrm":         "..."       # SSRM Control Ownership (reserved for future use)
          },
          ...
        ],
        ...
      }
    """
    xl = pd.ExcelFile(path)
    caiq_sheet = next((s for s in xl.sheet_names if s.startswith("CAIQv")), None)
    if not caiq_sheet:
        raise ValueError(
            f"No sheet starting with 'CAIQv' found in {path}. Sheets: {xl.sheet_names}"
        )

    df = pd.read_excel(path, sheet_name=caiq_sheet, header=1, dtype=str)
    df.fillna("", inplace=True)

    domains: dict = {}
    for _, row in df.iterrows():
        question_id = str(row.get("Question ID", "")).strip()
        if not question_id:
            continue

        # Domain code is everything before the first "-" (e.g. "A&A" from "A&A-01.1")
        domain_code = question_id.split("-")[0].strip()

        # Filter out junk rows (footer text, copyright notices, etc.)
        # Valid domain codes are short alphanumeric strings, optionally containing "&"
        if not domain_code.replace("&", "").isalnum() or len(domain_code) > 6:
            continue

        # Control ID strips the sub-question suffix to match CCM reference keys
        # e.g. "A&A-01.1" → "A&A-01"
        control_id = question_id.split(".")[0]

        entry = {
            "question_id": question_id,
            "control_id": control_id,
            "question": str(row.get("Question", "")).strip(),
            "answer": str(row.get("CSP CAIQ Answer", "")).strip(),
            "description": str(
                row.get("CSP Implementation Description (Optional/Recommended)", "")
            ).strip(),
            "ssrm": str(
                row.get("SSRM Control Ownership", "")
            ).strip(),  # reserved for future SSRM feature
        }

        domains.setdefault(domain_code, []).append(entry)

    return domains


def extract_vendor_name(client: anthropic.Anthropic, filename: str) -> str:
    """
    Use Claude to extract the vendor or company name from the CAIQ filename.

    CAIQ filenames are inconsistent across vendors, so this handles variations like:
      - 'Azure_and_D365_CAIQv4.0.2_STAR-020922.xlsx'
      - 'axcient_caiq_10_22_2024_OCT-22-2024.xlsx'

    Returns a sanitized string safe for use in a filename.
    Falls back to the raw filename stem if the API call fails.
    """
    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=50,
            temperature=0,
            system=(
                "Extract only the vendor or company name from the filename. "
                "Return only the name, nothing else. No punctuation, no explanation.",
            ),
            messages=[{"role": "user", "content": f"Filename: {filename}"}],
        )
        name = message.content[0].text.strip()
    except Exception:
        name = Path(filename).stem

    # Sanitize: keep only alphanumeric characters, spaces, hyphens, and underscores
    return "".join(
        c if c.isalnum() or c in (" ", "_", "-") else "" for c in name
    ).strip()


def build_user_prompt(domain_code: str, caiq_rows: list, ccm_controls: dict) -> str:
    """
    Build the user-turn prompt for a single domain API call.

    Groups CAIQ questions by control ID, then joins each control group
    with its CCM specification and implementation guidance. This gives
    Claude the full context needed to evaluate vendor responses against
    the actual control requirements — not just the questions in isolation.
    """
    lines = [f"Domain: {domain_code}\n"]

    # Group questions by control ID so spec + guidance appears once per control
    by_control: dict = {}
    for row in caiq_rows:
        by_control.setdefault(row["control_id"], []).append(row)

    for control_id, questions in by_control.items():
        ccm = ccm_controls.get(control_id, {})

        lines.append(f"--- Control: {control_id} ---")

        # CCM context — what the control requires and how it should be implemented
        if ccm.get("specification"):
            lines.append(f"Specification: {ccm['specification']}")
        if ccm.get("guidance"):
            lines.append(f"Implementation Guidance: {ccm['guidance']}")

        # Vendor responses for each sub-question under this control
        for q in questions:
            lines.append(f"\n  Q [{q['question_id']}]: {q['question']}")
            lines.append(f"  Answer: {q['answer']}")
            if q.get("description"):
                lines.append(f"  Description: {q['description']}")

        lines.append("")

    return "\n".join(lines)


def call_claude(
    client: anthropic.Anthropic, system_prompt: str, user_prompt: str
) -> dict:
    """
    Send one domain's prompt to Claude and return the parsed JSON result.

    The system prompt is the AI scoring rubric.
    The user prompt contains CCM spec, implementation guidance, and all
    vendor Q&A for the domain.

    temperature=0.3 provides enough variation to avoid score anchoring
    while maintaining reasonable run-to-run consistency.

    Raises ValueError if the response cannot be parsed as valid JSON.
    """
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        temperature=0.3,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw = message.content[0].text.strip()

    # Strip markdown code fences if the model includes them despite rubric instructions
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Failed to parse Claude response as JSON: {e}\nRaw response:\n{raw}"
        )


def calculate_weighted_score(domain_results: list, weights: dict) -> float:
    """
    Calculate the weighted average score across all scored domains.

    Domains with a null score (missing data or API error) are excluded.
    Domains not present in the weights dict default to a weight of 1.0.
    """
    total_weight = 0.0
    weighted_sum = 0.0

    for result in domain_results:
        code = result.get("domain_code", "")
        score = result.get("score")
        if score is None:
            continue
        weight = weights.get(code, 1.0)
        weighted_sum += score * weight
        total_weight += weight

    if total_weight == 0:
        return 0.0

    return round(weighted_sum / total_weight, 2)


def maturity_from_score(score: float) -> str:
    """
    Derive an overall maturity label from the final weighted score.
    Thresholds align with the scoring bands defined in the rubric.
    """
    if score >= 7:
        return "High"
    elif score >= 4:
        return "Medium"
    else:
        return "Low"


def main():
    print("=== CAIQ Analyzer ===")

    # --- File selection ---
    ccm_path = prompt_for_path("CCM reference file", "CCMv4_1_0.xlsx")
    caiq_path = prompt_for_path("Vendor CAIQ file", "CAIQv4_1_0.xlsx")
    rubric_path = prompt_for_path("AI scoring rubric", "ai_scoring_rubric.md")

    # --- Load input files ---
    print("\nLoading files...")
    rubric = load_rubric(rubric_path)
    ccm_controls = load_ccm(ccm_path)
    caiq_domains = load_caiq(caiq_path)

    print(f"  CCM controls loaded:  {len(ccm_controls)}")
    print(f"  CAIQ domains found:   {len(caiq_domains)}")

    # --- Initialize Anthropic client ---
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("\nERROR: ANTHROPIC_API_KEY not found. Add it to your .env file.")
        sys.exit(1)
    client = anthropic.Anthropic(api_key=api_key)

    # --- Identify domains in weight table with no CAIQ data ---
    missing_domains = [d for d in DOMAIN_WEIGHTS if d not in caiq_domains]
    if missing_domains:
        print(
            f"\n  Note: {len(missing_domains)} domain(s) in weight table have no CAIQ data: "
            f"{', '.join(missing_domains)}"
        )

    # --- Score each domain via Claude API ---
    domain_results = []
    total_domains = len(caiq_domains)
    print(f"\nScoring {total_domains} domain(s) via Claude API...\n")

    for i, (domain_code, caiq_rows) in enumerate(caiq_domains.items(), 1):
        print(
            f"  [{i}/{total_domains}] Scoring domain: {domain_code} ({len(caiq_rows)} questions)..."
        )

        user_prompt = build_user_prompt(domain_code, caiq_rows, ccm_controls)

        try:
            result = call_claude(client, rubric, user_prompt)
            domain_results.append(result)
            print(
                f"           Score: {result.get('score', '?')}/10  |  Maturity: {result.get('maturity', '?')}"
            )
        except Exception as e:
            print(f"           ERROR: {e}")
            domain_results.append(
                {
                    "domain_code": domain_code,
                    "domain_name": domain_code,
                    "score": None,
                    "maturity": None,
                    "summary": f"Scoring failed: {e}",
                    "flags": ["API call or parse error — review manually"],
                }
            )

    # --- Append placeholder entries for missing domains ---
    for domain_code in missing_domains:
        domain_results.append(
            {
                "domain_code": domain_code,
                "domain_name": domain_code,
                "score": None,
                "maturity": None,
                "summary": "No CAIQ data found for this domain. Score is incomplete.",
                "flags": [
                    "Domain expected per weight table but absent from CAIQ — verify with vendor"
                ],
            }
        )

    # --- Calculate final weighted score ---
    final_score = calculate_weighted_score(domain_results, DOMAIN_WEIGHTS)
    final_maturity = maturity_from_score(final_score)
    scored_count = sum(1 for r in domain_results if r.get("score") is not None)

    print("\n--- Results ---")
    print(f"  Domains scored:   {scored_count}/{total_domains}")
    print(f"  Weighted score:   {final_score}/10")
    print(f"  Overall maturity: {final_maturity}")
    if missing_domains:
        print(
            f"  ⚠️  Score is INCOMPLETE — missing domains: {', '.join(missing_domains)}"
        )

    # --- Extract vendor name and set output filename ---
    vendor_name = extract_vendor_name(client, caiq_path.name)
    safe_name = vendor_name.replace(" ", "_")
    output_path = Path(f"{safe_name}_results.json")

    # --- Build and write results to JSON ---
    output = {
        "vendor_name": vendor_name,
        "final_score": final_score,
        "final_maturity": final_maturity,
        "scored_domains": scored_count,
        "total_domains_in_caiq": total_domains,
        "missing_domains": missing_domains,
        "score_is_complete": len(missing_domains) == 0,
        "domain_results": domain_results,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"\n  Results saved to: {output_path.resolve()}")
    print("\nDone.")


if __name__ == "__main__":
    main()
