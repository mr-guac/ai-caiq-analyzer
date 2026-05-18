# CAIQ Analyzer

A Python-based GRC engineering tool that evaluates vendor CAIQ
(Consensus Assessment Initiative Questionnaire) responses against
CSA CCM v4.1 control specifications and implementation guidance
using the Anthropic Claude API.

The tool performs structured, domain-level analysis — one API call
per CCM domain — producing a weighted risk score, maturity rating,
and analyst-grade flags for each domain. Results are saved to a
vendor-named JSON file for analyst review.

**Cost:** Under $1 per full vendor assessment.  
**Runtime:** Under 2 minutes for a complete 17-domain CAIQ.

---

## What It Does

For each CCM domain the tool:
1. Joins vendor CAIQ responses to the corresponding CCM control
   specification and implementation guidance
2. Sends the combined context to Claude with a customizable AI
   scoring rubric as the system prompt
3. Parses a structured JSON response containing a domain score (1-10),
   maturity rating, summary, and specific analyst flags
4. Applies configurable domain weights to calculate a final
   weighted score

### Scoring Scale
- **7-10: High** — strong, well-evidenced responses with operational specificity
- **4-6: Medium** — controls appear functional but evidence is inconsistent or generic
- **Below 4: Low** — little to no credible evidence; follow up required

---

## Important Caveat

This tool measures **response quality and evidence**, not actual
security maturity. A vendor with world-class security controls can
score poorly if they submit a generic or incomplete CAIQ. A low
score should prompt follow-up and deeper review — not automatic
disqualification.

Testing against publicly available CAIQs showed that many large,
well-known vendors leave description fields blank or submit
boilerplate responses, which the rubric correctly penalizes. This
reflects a widespread industry pattern of treating CAIQs as a
checkbox exercise rather than a substantive assessment.

This is a triage and analysis tool designed to assist the analyst,
not replace them.

---

## Sample Results

Tested against publicly available CAIQs from the CSA STAR registry:

| Vendor     | Score | Maturity |
|------------|-------|----------|
| MS Azure   | 6.59  | Medium   |
| Adobe      | 5.94  | Medium   |
| Cloudflare | 3.82  | Low      |

Cloudflare's low score is a good example of the caveat above —
they left the majority of description fields blank, which the
rubric correctly penalizes. Their actual security posture is
well-regarded.

---

## Project Structure

```
caiq-analyzer/
├── .github/
│   └── workflows/
│       └── lint.yml         # CI/CD linting pipeline
├── analyzer.py              # Main script
├── ai_scoring_rubric.md     # AI evaluation instructions (customizable)
├── requirements.txt         # Python dependencies
├── sample_results.json      # Example output (Microsoft Azure)
├── .env.example             # API key template
├── .gitignore
└── LICENSE
```

---

## Requirements

- Python 3.9+
- Anthropic API key ([console.anthropic.com](https://console.anthropic.com))
- CSA CCM v4.1 reference file (download free from
  [CSA STAR](https://cloudsecurityalliance.org/star))
- Vendor-completed CAIQ file (xlsx)

---

## Setup

1. Clone the repo and navigate to the project directory

2. Create and activate a virtual environment:
```bash
python3 -m venv venv
source venv/bin/activate
```

3. Install dependencies:
```bash
pip3 install -r requirements.txt
```

4. Create a `.env` file from the template:
```bash
cp .env.example .env
```
Then add your Anthropic API key to `.env`:
```
ANTHROPIC_API_KEY=your-api-key-here
```

---

## Usage

```bash
python3 analyzer.py
```

The script will prompt for three file paths:
- CCM reference file (xlsx)
- Vendor CAIQ file (xlsx)
- AI scoring rubric (markdown)

Results are saved to `{VendorName}_results.json` in the
current directory.

---

## Customization

**Domain weights** — adjust relative risk weighting per domain
by editing the `DOMAIN_WEIGHTS` dict at the top of `analyzer.py`.
All weights default to 1.0 (equal weighting). Example — to
prioritize IAM and CEK for a high-trust SaaS vendor assessment:

```python
DOMAIN_WEIGHTS = {
    "IAM": 2.0,
    "CEK": 2.0,
    # all others remain 1.0
    ...
}
```

**AI scoring rubric** — the rubric in `ai_scoring_rubric.md`
defines how Claude evaluates vendor responses. Edit it to match
your organization's risk appetite, add domain-specific guidance,
or adjust scoring thresholds. The rubric is passed as the system
prompt for every API call.

---

## Output Format

Results are saved as JSON with the following structure:

```json
{
  "vendor_name": "Microsoft",
  "final_score": 6.59,
  "final_maturity": "Medium",
  "scored_domains": 17,
  "domain_results": [
    {
      "domain_code": "A&A",
      "domain_name": "Audit & Assurance",
      "score": 6,
      "maturity": "Medium",
      "summary": "...",
      "flags": ["specific concern 1", "specific concern 2"]
    }
  ]
}
```

The output is intended for analyst review, not as a final
deliverable. The flags and summaries are written as internal
analyst notes to guide follow-up questions and risk decisions.

---

## Cost

Each full 17-domain analysis costs approximately $0.60-0.70
using Claude Sonnet. Significantly cheaper than enterprise TPRM
SaaS platforms while producing analyst-grade, explainable output.
At this cost, a $10 API credit covers roughly 15 full vendor
assessments.

---

## CI/CD

This repo includes a GitHub Actions workflow that runs flake8
on every push and pull request to enforce code quality:

```
.github/workflows/lint.yml
```

---

## Limitations & Future Work

- Currently built for CSA CCM v4.1 and the standard CAIQ format.
  Adapting to custom questionnaires would require updating column
  mappings and sheet detection logic — the architecture supports
  this extension.
- SSRM (Shared Security Responsibility Model) column is loaded
  but not yet used — a planned feature will generate a shared
  responsibility summary from Column D CAIQ data.
- Scores may vary slightly between runs due to AI nondeterminism
  (temperature=0.3).
- No HTML or PDF report output — results are JSON only, intended
  for analyst consumption rather than stakeholder reporting.

---

## Background

Built as a GRC engineering portfolio project to demonstrate
practical application of AI APIs in third-party risk management
workflows.

The core insight: take any vendor questionnaire, join each response
to the corresponding control specification and implementation
guidance, and send that combined context to an LLM with a
purpose-built scoring rubric. The result is structured,
analyst-grade output for under a dollar — faster and more
explainable than manual review, and cheaper than enterprise
TPRM SaaS tools.

The CAIQ/CCM implementation is a reference example. The same
architecture could be applied to any questionnaire and
requirements document combination.

The rubric, domain weights, and column mappings are all
externalized and configurable without touching the core logic.