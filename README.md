# DevOps CI/CD Reference Platform

Reusable, validation-only CI building blocks for Terraform, PowerShell, Python, and YAML across GitHub Actions and Azure DevOps.

This repository is a reference implementation for platform engineers, application teams, and consulting clients who need consistent pull-request quality gates without granting a pipeline cloud credentials or deployment authority. It demonstrates explicit interfaces, least-privilege permissions, immutable GitHub Action pins, path-containment checks, and executable policy tests.

## What it solves

Teams often duplicate validation logic across repositories, allow tool versions to drift, or combine safe pull-request checks with privileged deployment steps. This project separates validation from delivery:

- Terraform formatting, backend-free initialization, TFLint, and validation
- PowerShell parsing, module-manifest checks, PSScriptAnalyzer, and Pester
- Python formatting, linting, and Pytest
- Static YAML and policy tests that reject credentials, deployment commands, write permissions, unsafe shell blocks, and path traversal

It does **not** provision infrastructure, authenticate to a cloud, publish packages, or deploy applications.

## Architecture

```text
Consumer repository
├── GitHub Actions caller job ──> reusable workflow ──> hosted validation job
└── Azure pipeline stage ───────> stage template ─────> hosted validation job
                                      │
                                      └── repository-relative, contained paths

This repository
├── .github/workflows/          GitHub reusable workflows and local dispatcher
├── azure-pipelines/templates/  Azure DevOps stage templates
├── examples/                   Azure template composition example
└── tests/                      YAML, interface, and security-policy contracts
```

Each template checks out source without persisting credentials, installs pinned validation tooling where implemented, resolves user-supplied paths, and fails if a resolved path escapes the checkout or working directory.

## Repository map

| Path | Purpose |
| --- | --- |
| `.github/workflows/reusable-terraform.yml` | Terraform validation callable by another GitHub workflow |
| `.github/workflows/reusable-powershell.yml` | PowerShell source and test validation callable by another GitHub workflow |
| `.github/workflows/reusable-python.yml` | Python source and test validation callable by another GitHub workflow |
| `.github/workflows/validate-repository.yml` | Push, pull request, and manual entry point for repository-local examples |
| `azure-pipelines/templates/terraform-validate.yml` | Terraform validation stage for Azure DevOps |
| `azure-pipelines/templates/powershell-test.yml` | PowerShell validation stage for Azure DevOps |
| `azure-pipelines/templates/python-test.yml` | Python validation stage for Azure DevOps |
| `examples/azure-pipelines.yml` | Composition example importing all three Azure templates |
| `tests/test_workflows.py` | Static contract, parser, security, and adversarial tests |

## Prerequisites

For local repository validation:

- Python 3.12 or later
- a virtual environment
- dependencies from the `test` optional dependency group
- `actionlint` 1.7.12 for GitHub workflow linting
- PowerShell 7 to parse embedded PowerShell blocks
- Bash to parse embedded shell blocks

For hosted consumers:

- GitHub Actions enabled for GitHub callers
- an Azure DevOps YAML pipeline and a Linux hosted agent for Azure callers
- the **Terraform Azure DevOps Marketplace extension**, which provides `TerraformInstaller@1`, for the Azure Terraform template
- source trees and test directories that match the supplied paths

## Local validation

From the repository root:

```bash
python -m venv .venv
python -m pip install --upgrade pip
python -m pip install -e ".[test]"
python -m pytest
actionlint .github/workflows/*.yml
```

The test suite parses every YAML document and checks the reusable interfaces, required tool invocations, read-only permissions, immutable action references, strict shell behavior, path containment, and absence of deployment or credential fields.

## GitHub reusable workflows

Reusable workflows are called at the **job** level. They declare `contents: read`, persist no checkout credentials, expose no secrets, and define no outputs.

### Terraform validation

File: `.github/workflows/reusable-terraform.yml`

| Input | Type | Default | Meaning |
| --- | --- | --- | --- |
| `working-directory` | string | `.` | Repository-relative Terraform directory |
| `terraform-version` | string | `1.9.7` | Exact Terraform CLI version |

Output: none. Checks: `terraform fmt -check -recursive`, `terraform init -backend=false -input=false`, TFLint initialization and recursive linting, and `terraform validate`.

```yaml
jobs:
  terraform:
    uses: ./.github/workflows/reusable-terraform.yml
    with:
      working-directory: infrastructure
      terraform-version: "1.9.7"
```

### PowerShell validation

File: `.github/workflows/reusable-powershell.yml`

| Input | Type | Default | Meaning |
| --- | --- | --- | --- |
| `working-directory` | string | `.` | Repository-relative PowerShell source directory |
| `powershell-version` | string | `7.4` | Exact major/minor container runtime channel |
| `test-path` | string | `tests` | Test path relative to `working-directory` |

Output: none. Checks: parser validation, qualifying module manifests, PSScriptAnalyzer errors, and Pester tests. The GitHub workflow verifies that the running PowerShell major/minor matches the requested channel.

```yaml
jobs:
  powershell:
    uses: ./.github/workflows/reusable-powershell.yml
    with:
      working-directory: src/automation
      powershell-version: "7.4"
      test-path: tests
```

### Python validation

File: `.github/workflows/reusable-python.yml`

| Input | Type | Default | Meaning |
| --- | --- | --- | --- |
| `working-directory` | string | `.` | Repository-relative Python project directory |
| `python-version` | string | `3.12` | Exact Python version supplied to `setup-python` |
| `test-path` | string | `tests` | Test path relative to `working-directory` |
| `dependency-file` | string | empty | Optional requirements file relative to `working-directory` |
| `install-project` | boolean | `false` | Install the project before validation |

Output: none. Checks: Ruff format, Ruff lint, and Pytest. When `install-project` is true, `pyproject.toml` or `setup.py` must exist. Consumer dependencies are installed before the exact validation-tool versions so a dependency file cannot silently replace the validators.

```yaml
jobs:
  python:
    uses: ./.github/workflows/reusable-python.yml
    with:
      working-directory: services/api
      python-version: "3.12"
      test-path: tests
      dependency-file: requirements.txt
      install-project: true
```

### Cross-repository use after publication

The following form is a **future example only**. The `@v1` reference will not exist until this repository is published and a reviewed `v1` tag is created. Until then, use the repository-local examples above or reference a real immutable commit SHA after publication.

```yaml
jobs:
  terraform:
    uses: joehonny/devops-cicd-reference-platform/.github/workflows/reusable-terraform.yml@v1
    with:
      working-directory: infrastructure
```

For stronger supply-chain control, consumers should use a full reviewed commit SHA instead of a mutable tag.

## Azure DevOps templates

These files are **stage templates**. Import them under the root `stages` key, as demonstrated in `examples/azure-pipelines.yml`. They declare no output variables and accept no service connection or credential parameters.

### Terraform template

File: `azure-pipelines/templates/terraform-validate.yml`

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `workingDirectory` | string | `.` | Repository-relative Terraform directory |
| `terraformVersion` | string | `1.9.7` | Version passed to `TerraformInstaller@1` |

Output: none. This template requires the Terraform Azure DevOps Marketplace extension because `TerraformInstaller@1` is not built into this repository. TFLint 0.58.1 is downloaded over HTTPS and verified against its published release checksum before installation.

```yaml
stages:
  - template: azure-pipelines/templates/terraform-validate.yml
    parameters:
      workingDirectory: infrastructure
      terraformVersion: "1.9.7"
```

### PowerShell template

File: `azure-pipelines/templates/powershell-test.yml`

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `workingDirectory` | string | `.` | Repository-relative PowerShell directory |
| `powershellVersion` | string | `7.4` | Minimum acceptable installed PowerShell version |
| `testPath` | string | `tests` | Test path relative to `workingDirectory` |

Output: none. Unlike the GitHub container workflow, `powershellVersion` is a **minimum-version check**, not an exact runtime selector. The hosted agent supplies PowerShell. The template fails when the installed version is older than the requested value.

```yaml
stages:
  - template: azure-pipelines/templates/powershell-test.yml
    parameters:
      workingDirectory: src/automation
      powershellVersion: "7.4"
      testPath: tests
```

### Python template

File: `azure-pipelines/templates/python-test.yml`

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `workingDirectory` | string | `.` | Repository-relative Python project directory |
| `pythonVersion` | string | `3.12` | Version specification passed to `UsePythonVersion@0` |
| `testPath` | string | `tests` | Test path relative to `workingDirectory` |
| `dependencyFile` | string | empty | Optional requirements file relative to `workingDirectory` |
| `installProject` | boolean | `false` | Install project metadata before validation |

Output: none.

```yaml
stages:
  - template: azure-pipelines/templates/python-test.yml
    parameters:
      workingDirectory: services/api
      pythonVersion: "3.12"
      testPath: tests
      dependencyFile: requirements.txt
      installProject: true
```

## Trust boundaries and security model

- **Read-only checkout:** GitHub workflows grant only `contents: read`; GitHub and Azure checkouts set credential persistence to false.
- **No secrets contract:** Interfaces do not accept secrets, cloud identities, service connections, tokens, or deployment environments. Do not add them to callers for these validation jobs.
- **Untrusted paths:** Working directories, test paths, and dependency-file paths are treated as untrusted input. Resolved canonical paths must remain within the checkout, and nested paths must remain within their selected working directory.
- **Untrusted repository code:** Validation executes code and dependency installation from the checked-out revision. Run pull requests from forks with platform-default secret restrictions, require review before running privileged workflows, and never attach deployment credentials to these jobs.
- **Fork behavior:** Fork pull requests can perform validation using the fork checkout but should receive no repository secrets. Maintainers must review dependency files and build metadata because installation can execute third-party or project-controlled code.
- **Supply chain:** GitHub Actions use full immutable commit SHAs with readable version comments. Tool versions are pinned. Azure TFLint installation validates its archive checksum. Review automated dependency updates before merging.
- **Permissions:** Reusable workflows do not request write permission. Callers should not widen permissions unless a separately reviewed use case requires it.
- **Validation only:** Policy tests reject known deployment commands, credential-shaped fields, deployment targets, and write grants. This is defense in depth, not a substitute for branch protection and human review.

## Limitations and verification status

Verified locally:

- all repository YAML parses with the project loader
- Python policy and contract tests pass
- GitHub workflows pass `actionlint`
- embedded Bash and PowerShell blocks parse
- action references are immutable and workflow permissions remain read-only
- documentation paths, links, headings, and fenced code blocks are internally consistent

Not verified in this local implementation:

- GitHub-hosted execution of the reusable workflows
- Azure DevOps server-side template compilation
- Azure DevOps hosted-agent execution
- cloud, deployment, credential, or service-connection behavior, because none is in scope
- consumer projects beyond the synthetic/static contracts in this repository

The manual `validate-repository.yml` examples are gated by `run-validations`. Its example source directories are reference placeholders and must exist before enabling that input.

## Maintenance status

Status: actively maintained reference implementation. Dependency updates are checked monthly. Interface or security-policy changes should include updated tests and documentation. See `CONTRIBUTING.md` for the validation contract and `SECURITY.md` for private reporting guidance.

## License

MIT. See `LICENSE`.
