# Contributing

Contributions that improve portable, least-privilege validation are welcome.

## Development contract

This repository is validation-only. Contributions must not add:

- deployment, release, publish, or infrastructure-apply steps
- cloud login, service connection, identity federation, or credential inputs
- secret values, tokens, private endpoints, customer identifiers, or production data
- write permissions when read access is sufficient
- unpinned GitHub Actions or unchecked downloaded executables
- path parameters that are used before canonicalization and checkout-boundary validation

Use synthetic fixtures and static contracts. Do not test against live cloud resources or production systems.

## Set up locally

```bash
python -m venv .venv
python -m pip install --upgrade pip
python -m pip install -e ".[test]"
python -m pytest
actionlint .github/workflows/*.yml
```

Python 3.12 or later is required. Embedded shell validation also needs Bash and PowerShell 7.

## Make a change

1. Create a focused branch.
2. Add or update a failing contract test before changing a workflow interface or security rule.
3. Keep workflow inputs explicit, typed, documented, and safely defaulted.
4. Keep GitHub permissions at `contents: read` and checkout credential persistence disabled.
5. Pin third-party execution dependencies and explain how downloaded artifacts are verified.
6. Update `README.md` when behavior, prerequisites, inputs, defaults, checks, limitations, or trust boundaries change.
7. Run the complete local validation suite.

## Required checks

Before opening a pull request:

- run the full Pytest suite
- run `actionlint` against every GitHub workflow
- parse all YAML documents
- compile Python test sources
- parse embedded Bash and PowerShell blocks
- verify action pins, read-only permissions, path containment, and the no-secret/no-deployment policy
- inspect `git diff --check` and the final diff

Describe which checks passed and identify any hosted GitHub or Azure DevOps checks that were not run. Do not claim hosted execution from local static validation.

## Pull requests

Keep pull requests small enough to review. Explain the problem, interface impact, security impact, test evidence, and rollback approach. Never place vulnerability details in a normal pull request; follow `SECURITY.md` instead.

By contributing, you agree that your contribution is licensed under the MIT License.
