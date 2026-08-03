"""Static, offline safety contracts for the repository's pipeline YAML.

Run ``pytest`` for the eventual complete platform. During phased implementation,
Task 2 uses ``pytest -m "github or policy"`` and Task 3 uses
``pytest -m "azure or policy"``. Helper adversarial tests can be isolated with
``pytest -m selftest``.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
GITHUB_WORKFLOWS = {
    "terraform": Path(".github/workflows/reusable-terraform.yml"),
    "powershell": Path(".github/workflows/reusable-powershell.yml"),
    "python": Path(".github/workflows/reusable-python.yml"),
    "repository": Path(".github/workflows/validate-repository.yml"),
}
AZURE_PIPELINES = {
    "terraform": Path("azure-pipelines/templates/terraform-validate.yml"),
    "powershell": Path("azure-pipelines/templates/powershell-test.yml"),
    "python": Path("azure-pipelines/templates/python-test.yml"),
    "example": Path("examples/azure-pipelines.yml"),
}
EXPECTED_YAML = (*GITHUB_WORKFLOWS.values(), *AZURE_PIPELINES.values())
PARAMETER_EXPRESSION = re.compile(
    r"\$\{\{\s*(?P<scope>inputs|parameters)\s*"
    r"(?:\.\s*(?P<dot>[A-Za-z_][\w-]*)|"
    r"\[\s*['\"](?P<bracket>[^'\"]+)['\"]\s*\])\s*\}\}",
    re.IGNORECASE,
)
PATH_VARIABLE = re.compile(
    r"\$(?:(?:env:)?(?:WORKING_DIRECTORY|TEST_PATH)|"
    r"\{(?:WORKING_DIRECTORY|TEST_PATH)\})",
    re.IGNORECASE,
)


class GithubYamlLoader(yaml.SafeLoader):
    """Parse GitHub's YAML 1.2 booleans while retaining SafeLoader semantics.

    PyYAML follows YAML 1.1 by default and otherwise turns the workflow key
    ``on`` into boolean ``True``. A copied resolver avoids mutating PyYAML's
    process-wide SafeLoader configuration.
    """


GithubYamlLoader.yaml_implicit_resolvers = copy.deepcopy(
    yaml.SafeLoader.yaml_implicit_resolvers
)
for first_character, resolvers in GithubYamlLoader.yaml_implicit_resolvers.items():
    GithubYamlLoader.yaml_implicit_resolvers[first_character] = [
        resolver for resolver in resolvers if resolver[0] != "tag:yaml.org,2002:bool"
    ]
GithubYamlLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|false)$", re.IGNORECASE),
    list("tTfF"),
)


def yaml_paths() -> list[Path]:
    return sorted(
        (
            path
            for pattern in ("*.yml", "*.yaml")
            for path in REPOSITORY_ROOT.rglob(pattern)
            if ".git" not in path.parts and ".tools" not in path.parts
        ),
        key=lambda path: path.as_posix(),
    )


def load_yaml(relative_path: Path) -> Any:
    absolute_path = REPOSITORY_ROOT / relative_path
    assert absolute_path.is_file(), (
        f"Required pipeline file is missing: {relative_path.as_posix()}"
    )
    with absolute_path.open(encoding="utf-8") as stream:
        document = yaml.load(stream, Loader=GithubYamlLoader)
    assert isinstance(document, Mapping), (
        f"{relative_path.as_posix()} must contain one YAML mapping document"
    )
    return document


def walk(node: Any, trail: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], Any]]:
    yield trail, node
    if isinstance(node, Mapping):
        for key, value in node.items():
            yield from walk(value, (*trail, str(key)))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from walk(value, (*trail, str(index)))


def mapping_at(document: Mapping[str, Any], key: str, source: Path) -> Mapping[str, Any]:
    value = document.get(key)
    assert isinstance(value, Mapping), f"{source.as_posix()}: '{key}' must be a mapping"
    return value


def expressions(document: Any) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for _trail, value in walk(document):
        if not isinstance(value, str):
            continue
        for match in PARAMETER_EXPRESSION.finditer(value):
            found.append((match.group("scope").lower(), match.group("dot") or match.group("bracket")))
    return found


def wired_expression_names(
    document: Any, scope: str, allowed_containers: set[str]
) -> set[str]:
    """Return expressions wired through executable configuration, not labels/comments."""
    names: set[str] = set()
    for trail, value in walk(document):
        if not isinstance(value, str) or not allowed_containers.intersection(trail):
            continue
        for match in PARAMETER_EXPRESSION.finditer(value):
            if match.group("scope").lower() == scope.lower():
                names.add(match.group("dot") or match.group("bracket"))
    return names


def occurrence_is_quoted(line: str, start: int) -> bool:
    """Return whether a shell variable occurrence is inside single/double quotes."""
    prefix = line[:start]
    single_open = len(re.findall(r"(?<!\\)'", prefix)) % 2 == 1
    double_open = len(re.findall(r'(?<!\\)"', prefix)) % 2 == 1
    return single_open or double_open


def executable_lines(command_text: str) -> list[str]:
    """Return command lines, excluding comments and here-document/string payloads."""
    lines: list[str] = []
    heredoc_end: str | None = None
    powershell_here_end: str | None = None
    block_comment = False
    for raw_line in command_text.splitlines():
        stripped = raw_line.strip()
        if heredoc_end is not None:
            if stripped == heredoc_end:
                heredoc_end = None
            continue
        if powershell_here_end is not None:
            if stripped == powershell_here_end:
                powershell_here_end = None
            continue
        if block_comment:
            if "#>" in stripped:
                block_comment = False
            continue
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("<#"):
            block_comment = "#>" not in stripped
            continue
        here_string = re.search(r"@(['\"])\s*$", stripped)
        if here_string:
            powershell_here_end = f"{here_string.group(1)}@"
        heredoc = re.search(
            r"<<-?\s*(?P<quote>['\"]?)(?P<delimiter>[A-Za-z_][\w-]*)"
            r"(?P=quote)(?:\s+\d*(?:>>?|<<?)\s*\S+)*(?:\s+#.*)?$",
            stripped,
        )
        if heredoc:
            heredoc_end = heredoc.group("delimiter")
        lines.append(stripped)
    return lines


def has_executable_invocation(command_text: str, pattern: str) -> bool:
    return any(re.search(pattern, line, re.IGNORECASE) for line in executable_lines(command_text))


def has_canonical_containment(command_text: str, path_name: str) -> bool:
    """Require a canonical-path variable in a later checkout-boundary comparison."""
    shell_name = path_name.replace("-", "_").upper()
    canonical_patterns = [
        re.compile(
            rf"(?m)^\s*(?P<variable>[A-Za-z_]\w*)\s*=\s*['\"]?\$\(\s*"
            rf"realpath\b[^\r\n]*\$(?:\{{)?{re.escape(shell_name)}(?:\}})?[^\r\n]*\)",
            re.IGNORECASE,
        ),
        re.compile(
            rf"(?mi)^\s*\$(?P<variable>[A-Za-z_]\w*)\s*=\s*[^\r\n]*"
            rf"(?:Resolve-Path|GetFullPath)[^\r\n]*\$(?:env:)?{re.escape(shell_name)}\b",
        ),
    ]
    canonical = next(
        (match for pattern in canonical_patterns if (match := pattern.search(command_text))),
        None,
    )
    if canonical is None:
        return False
    variable = re.escape(canonical.group("variable"))
    tail = command_text[canonical.end() :]
    canonical_reference = rf"\$(?:\{{)?{variable}(?:\}})?"
    root_names = "GITHUB_WORKSPACE|BUILD_SOURCESDIRECTORY"
    root_reference = rf"\$(?:(?:env:)?(?:{root_names})|\{{(?:{root_names})\}})"
    boundary_patterns = [
        rf"case\s+['\"]?{canonical_reference}['\"]?\s+in"
        rf"[\s\S]{{0,400}}{root_reference}[^\r\n]*/\*",
        rf"\[\[[^\r\n]*{canonical_reference}[^\r\n]*(?:==|!=)"
        rf"[^\r\n]*{root_reference}[^\r\n]*/\*",
        rf"{canonical_reference}\.StartsWith\s*\([^\r\n]*{root_reference}",
    ]
    return any(re.search(pattern, tail, re.IGNORECASE) for pattern in boundary_patterns)


def assert_named_checks(document: Mapping[str, Any], source: Path) -> str:
    jobs = mapping_at(document, "jobs", source)
    names: list[str] = []
    for job_key, job in jobs.items():
        assert isinstance(job, Mapping), f"{source.as_posix()}: job {job_key!r} must be a mapping"
        assert str(job.get("name", "")).strip(), f"{source.as_posix()}: job {job_key!r} needs a name"
        names.append(str(job["name"]))
        steps = job.get("steps", [])
        if isinstance(steps, list):
            for index, step in enumerate(steps):
                if isinstance(step, Mapping) and ("run" in step or "uses" in step):
                    assert str(step.get("name", "")).strip(), (
                        f"{source.as_posix()}: {job_key}.steps[{index}] needs a name"
                    )
                    names.append(str(step["name"]))
    return "\n".join(names)


def assert_contents_read(document: Mapping[str, Any], source: Path) -> None:
    permissions = mapping_at(document, "permissions", source)
    assert permissions == {"contents": "read"}, (
        f"{source.as_posix()}: top-level permissions must be exactly contents: read; "
        f"found {permissions!r}"
    )


def assert_input_contract(
    workflow_call: Mapping[str, Any], source: Path, expected_names: set[str]
) -> None:
    inputs = mapping_at(workflow_call, "inputs", source)
    assert set(inputs) == expected_names, (
        f"{source.as_posix()}: workflow_call inputs must be {sorted(expected_names)}; "
        f"found {sorted(map(str, inputs))}"
    )
    for name, specification in inputs.items():
        assert isinstance(specification, Mapping), (
            f"{source.as_posix()}: input {name!r} must be a mapping"
        )
        expected_type = "boolean" if name == "install-project" else "string"
        assert specification.get("type") == expected_type, (
            f"{source.as_posix()}: input {name!r} must declare type: {expected_type}"
        )
        assert "description" in specification, (
            f"{source.as_posix()}: input {name!r} needs a description"
        )
        if name == "working-directory":
            assert specification.get("default") == ".", (
                f"{source.as_posix()}: working-directory must default to '.'"
            )
            assert specification.get("required") is False
        elif name == "test-path":
            assert specification.get("required") is False
            assert isinstance(specification.get("default"), str)
        elif name == "dependency-file":
            assert specification.get("required") is False
            assert specification.get("default") == ""
        elif name == "install-project":
            assert specification.get("required") is False
            assert specification.get("default") is False
        else:
            assert specification.get("required") is False
            assert str(specification.get("default", "")).strip(), (
                f"{source.as_posix()}: {name!r} must have a non-empty pinned default"
            )


def run_blocks(document: Any) -> Iterator[tuple[tuple[str, ...], str, str]]:
    for trail, node in walk(document):
        if not isinstance(node, Mapping):
            continue
        if "run" in node:
            command = node["run"]
            assert isinstance(command, str), f"{'.'.join(trail)}.run must be a string"
            yield trail, str(node.get("shell", "")).lower(), command
        for azure_key, shell in (("bash", "bash"), ("pwsh", "pwsh"), ("powershell", "powershell")):
            if azure_key in node:
                command = node[azure_key]
                assert isinstance(command, str), (
                    f"{'.'.join((*trail, azure_key))} must be a string"
                )
                yield (*trail, azure_key), shell, command
        if "script" in node and (not trail or trail[-1] != "inputs"):
            command = node["script"]
            assert isinstance(command, str), f"{'.'.join((*trail, 'script'))} must be a string"
            yield (*trail, "script"), "bash", command
        task = str(node.get("task", "")).lower()
        inputs = node.get("inputs")
        if isinstance(inputs, Mapping) and isinstance(inputs.get("script"), str):
            if task.startswith("bash@"):
                yield (*trail, "inputs", "script"), "bash", inputs["script"]
            elif task.startswith(("powershell@", "powershellcore@")):
                yield (*trail, "inputs", "script"), "pwsh", inputs["script"]


@pytest.mark.parametrize(
    "relative_path",
    [
        *(pytest.param(path, marks=pytest.mark.github) for path in GITHUB_WORKFLOWS.values()),
        *(pytest.param(path, marks=pytest.mark.azure) for path in AZURE_PIPELINES.values()),
    ],
    ids=lambda path: path.as_posix(),
)
def test_required_pipeline_file_exists_and_parses(relative_path: Path) -> None:
    load_yaml(relative_path)


@pytest.mark.policy
def test_every_repository_yaml_parses_as_a_mapping() -> None:
    paths = yaml_paths()
    assert paths, "The repository must contain pipeline YAML files"
    errors: list[str] = []
    for path in paths:
        relative = path.relative_to(REPOSITORY_ROOT)
        try:
            load_yaml(relative)
        except (AssertionError, yaml.YAMLError) as error:
            errors.append(f"{relative.as_posix()}: {error}")
    assert not errors, "YAML parse/shape errors:\n" + "\n".join(errors)


@pytest.mark.github
@pytest.mark.parametrize(
    ("name", "expected_inputs"),
    [
        ("terraform", {"working-directory", "terraform-version"}),
        ("powershell", {"working-directory", "powershell-version", "test-path"}),
        (
            "python",
            {
                "working-directory",
                "python-version",
                "test-path",
                "dependency-file",
                "install-project",
            },
        ),
    ],
)
def test_reusable_github_workflow_contract(name: str, expected_inputs: set[str]) -> None:
    source = GITHUB_WORKFLOWS[name]
    workflow = load_yaml(source)
    assert_contents_read(workflow, source)
    triggers = mapping_at(workflow, "on", source)
    assert set(triggers) == {"workflow_call"}, (
        f"{source.as_posix()}: reusable workflow must trigger only on workflow_call"
    )
    workflow_call = triggers["workflow_call"]
    assert isinstance(workflow_call, Mapping)
    assert "secrets" not in workflow_call, (
        f"{source.as_posix()}: validation workflows must not accept secrets"
    )
    assert_input_contract(workflow_call, source, expected_inputs)
    assert mapping_at(workflow, "jobs", source), f"{source.as_posix()}: jobs cannot be empty"
    used_inputs = wired_expression_names(workflow, "inputs", {"env", "with"})
    assert expected_inputs <= used_inputs, (
        f"{source.as_posix()}: declared inputs are not actually used: "
        f"{sorted(expected_inputs - used_inputs)}"
    )


@pytest.mark.github
def test_repository_validation_workflow_contract() -> None:
    source = GITHUB_WORKFLOWS["repository"]
    workflow = load_yaml(source)
    assert_contents_read(workflow, source)
    triggers = mapping_at(workflow, "on", source)
    assert set(triggers) == {"pull_request", "push", "workflow_dispatch"}, (
        f"{source.as_posix()}: expected pull_request, push, and workflow_dispatch triggers"
    )
    jobs = mapping_at(workflow, "jobs", source)
    referenced = {
        str(job.get("uses"))
        for job in jobs.values()
        if isinstance(job, Mapping) and "uses" in job
    }
    expected = {
        "./.github/workflows/reusable-terraform.yml",
        "./.github/workflows/reusable-powershell.yml",
        "./.github/workflows/reusable-python.yml",
    }
    assert expected <= referenced, (
        f"{source.as_posix()}: validation jobs must call all reusable workflows; "
        f"missing {sorted(expected - referenced)}"
    )
    expected_with = {
        "./.github/workflows/reusable-terraform.yml": {
            "working-directory",
            "terraform-version",
        },
        "./.github/workflows/reusable-powershell.yml": {
            "working-directory",
            "powershell-version",
            "test-path",
        },
        "./.github/workflows/reusable-python.yml": {
            "working-directory",
            "python-version",
            "test-path",
            "dependency-file",
            "install-project",
        },
    }
    for job_key, job in jobs.items():
        if not isinstance(job, Mapping) or str(job.get("uses")) not in expected_with:
            continue
        supplied = job.get("with")
        assert isinstance(supplied, Mapping), f"{source.as_posix()}: job {job_key} needs with inputs"
        assert set(supplied) == expected_with[str(job["uses"])], (
            f"{source.as_posix()}: job {job_key} must pass exactly "
            f"{sorted(expected_with[str(job['uses'])])}"
        )


@pytest.mark.github
@pytest.mark.parametrize(
    ("name", "command_patterns", "check_name_patterns"),
    [
        (
            "terraform",
            [
                r"^(?:&\s*)?terraform\s+fmt\b.*(?:-check|--check)",
                r"^(?:&\s*)?terraform\s+init\b.*-backend=false",
                r"^(?:&\s*)?terraform\s+validate\b",
                r"^(?:&\s*)?tflint\b",
            ],
            [r"format", r"init", r"lint", r"validate"],
        ),
        (
            "powershell",
            [
                r"^\[System\.Management\.Automation\.Language\.Parser\]::ParseFile\b",
                r"^(?:&\s*)?Test-ModuleManifest\b",
                r"^(?:&\s*)?Invoke-ScriptAnalyzer\b",
                r"^(?:&\s*)?Invoke-Pester\b",
            ],
            [r"pars", r"manifest", r"analy|lint", r"pester|test"],
        ),
        (
            "python",
            [
                r"^(?:python\s+-m\s+)?ruff\s+format\b.*--check",
                r"^(?:python\s+-m\s+)?ruff\s+check\b",
                r"^(?:python\s+-m\s+)?pytest\b",
            ],
            [r"format", r"lint|ruff", r"test|pytest"],
        ),
    ],
)
def test_reusable_workflows_execute_required_validation_phases(
    name: str, command_patterns: list[str], check_name_patterns: list[str]
) -> None:
    source = GITHUB_WORKFLOWS[name]
    workflow = load_yaml(source)
    command_corpus = "\n".join(command for _trail, _shell, command in run_blocks(workflow))
    check_names = assert_named_checks(workflow, source)
    for pattern in command_patterns:
        assert has_executable_invocation(command_corpus, pattern), (
            f"{source.as_posix()}: no validation command matches {pattern!r}"
        )
    for pattern in check_name_patterns:
        assert re.search(pattern, check_names, re.IGNORECASE), (
            f"{source.as_posix()}: no named check identifies phase {pattern!r}"
        )


@pytest.mark.github
def test_reviewed_github_workflow_safety_regressions() -> None:
    terraform = load_yaml(GITHUB_WORKFLOWS["terraform"])
    powershell = load_yaml(GITHUB_WORKFLOWS["powershell"])
    python = load_yaml(GITHUB_WORKFLOWS["python"])

    terraform_commands = "\n".join(
        command for _trail, _shell, command in run_blocks(terraform)
    )
    init_position = terraform_commands.find("tflint --init")
    lint_position = terraform_commands.find("tflint --recursive")
    assert 0 <= init_position < lint_position, "TFLint plugins must initialize before linting"

    powershell_commands = "\n".join(
        command for _trail, _shell, command in run_blocks(powershell)
    )
    assert re.search(
        r"\$safeTestPath\.StartsWith\(\"\$safePath/\"",
        powershell_commands,
        re.IGNORECASE,
    )
    assert "Import-PowerShellDataFile" in powershell_commands
    assert "ModuleVersion" in powershell_commands
    assert re.search(r"RootModule|NestedModules", powershell_commands)
    assert "SkipPublisherCheck" not in powershell_commands

    python_commands = "\n".join(
        command for _trail, _shell, command in run_blocks(python)
    )
    assert re.search(
        r'case\s+"\$safe_test_path"\s+in\s*\n\s*"\$safe_path"\|"\$safe_path"/\*',
        python_commands,
    )
    assert re.search(
        r'case\s+"\$safe_dependency_file"\s+in\s*\n\s*"\$safe_path"\|"\$safe_path"/\*',
        python_commands,
    )
    assert 'python -m pip install --requirement "$safe_dependency_file"' in python_commands
    assert 'python -m pip install "$safe_path"' in python_commands
    validation_patterns = (
        r"^ruff\s+format\b",
        r"^ruff\s+check\b",
        r"^pytest\b",
    )
    for pattern in validation_patterns:
        matching_blocks = [
            command
            for _trail, _shell, command in run_blocks(python)
            if has_executable_invocation(command, pattern)
        ]
        assert len(matching_blocks) == 1
        executable = "\n".join(executable_lines(matching_blocks[0]))
        validation = re.search(pattern, executable, re.MULTILINE)
        assert validation is not None
        assert executable.rfind('cd -- "$safe_path"', 0, validation.start()) >= 0


@pytest.mark.azure
@pytest.mark.parametrize(
    ("name", "expected_parameters"),
    [
        ("terraform", {"workingDirectory", "terraformVersion"}),
        ("powershell", {"workingDirectory", "powershellVersion", "testPath"}),
        ("python", {"workingDirectory", "pythonVersion", "testPath"}),
    ],
)
def test_azure_template_parameter_contract(name: str, expected_parameters: set[str]) -> None:
    source = AZURE_PIPELINES[name]
    template = load_yaml(source)
    parameters = template.get("parameters")
    assert isinstance(parameters, list), f"{source.as_posix()}: parameters must be a list"
    by_name = {
        parameter.get("name"): parameter
        for parameter in parameters
        if isinstance(parameter, Mapping)
    }
    assert set(by_name) == expected_parameters, (
        f"{source.as_posix()}: expected parameters {sorted(expected_parameters)}; "
        f"found {sorted(str(name) for name in by_name)}"
    )
    for parameter_name, parameter in by_name.items():
        assert parameter.get("type") == "string", (
            f"{source.as_posix()}: parameter {parameter_name!r} must use type: string"
        )
        assert str(parameter.get("default", "")).strip(), (
            f"{source.as_posix()}: parameter {parameter_name!r} needs a safe default"
        )
    assert any(key in template for key in ("stages", "jobs", "steps")), (
        f"{source.as_posix()}: template must expose stages, jobs, or steps"
    )
    used_parameters = wired_expression_names(template, "parameters", {"env", "inputs"})
    assert expected_parameters <= used_parameters, (
        f"{source.as_posix()}: declared parameters are not actually used: "
        f"{sorted(expected_parameters - used_parameters)}"
    )


@pytest.mark.azure
@pytest.mark.parametrize(
    ("name", "patterns"),
    [
        (
            "terraform",
            [
                r"^(?:&\s*)?terraform\s+fmt\b.*(?:-check|--check)",
                r"^(?:&\s*)?terraform\s+init\b.*-backend=false",
                r"^(?:&\s*)?terraform\s+validate\b",
                r"^(?:&\s*)?tflint\b",
            ],
        ),
        (
            "powershell",
            [
                r"^\[System\.Management\.Automation\.Language\.Parser\]::ParseFile\b",
                r"^(?:&\s*)?Test-ModuleManifest\b",
                r"^(?:&\s*)?Invoke-ScriptAnalyzer\b",
                r"^(?:&\s*)?Invoke-Pester\b",
            ],
        ),
        (
            "python",
            [
                r"^(?:python\s+-m\s+)?ruff\s+format\b.*--check",
                r"^(?:python\s+-m\s+)?ruff\s+check\b",
                r"^(?:python\s+-m\s+)?pytest\b",
            ],
        ),
    ],
)
def test_azure_templates_execute_required_validation_phases(
    name: str, patterns: list[str]
) -> None:
    source = AZURE_PIPELINES[name]
    template = load_yaml(source)
    commands = "\n".join(command for _trail, _shell, command in run_blocks(template))
    display_names = "\n".join(
        str(value)
        for trail, value in walk(template)
        if trail and trail[-1].lower() == "displayname"
    )
    assert display_names.strip(), f"{source.as_posix()}: validation steps need displayName values"
    for pattern in patterns:
        assert has_executable_invocation(commands, pattern), (
            f"{source.as_posix()}: no validation command matches {pattern!r}"
        )


@pytest.mark.azure
def test_azure_example_imports_all_validation_templates() -> None:
    source = AZURE_PIPELINES["example"]
    example = load_yaml(source)
    serialized = yaml.safe_dump(example, sort_keys=True)
    for template in (
        "terraform-validate.yml",
        "powershell-test.yml",
        "python-test.yml",
    ):
        assert template in serialized, (
            f"{source.as_posix()}: example must reference {template}"
        )


def validation_policy_violations(document: Mapping[str, Any], source: Path) -> list[str]:
    forbidden_exact_keys = {
        "secret",
        "secrets",
        "credential",
        "credentials",
        "idtoken",
        "oidc",
        "serviceconnection",
        "connectedservicenamearm",
        "clientsecret",
        "accesskey",
        "awsaccesskeyid",
        "awssecretaccesskey",
        "azuresubscription",
        "password",
        "passwords",
        "token",
        "tokens",
        "apikey",
        "privatekey",
    }
    forbidden_key_suffixes = ("secret", "password", "token", "apikey", "privatekey")
    forbidden_text = re.compile(
        r"(?:terraform\s+apply\b|azure/login|configure-aws-credentials|"
        r"az\s+(?:deployment|webapp\s+deploy)\b|aws\s+cloudformation\s+deploy\b|"
        r"kubectl\s+(?:apply|create|delete)\b|helm\s+(?:install|upgrade|uninstall)\b|"
        r"\bdeploy(?:ment)?\s+(?:job|stage)\b)",
        re.IGNORECASE,
    )
    safe_validation_actions = {
        "actions/checkout",
        "actions/setup-python",
        "actions/setup-node",
        "hashicorp/setup-terraform",
        "terraform-linters/setup-tflint",
    }
    deployment_task = re.compile(
        r"(?:AzureResourceManagerTemplateDeployment|AzureWebApp|KubernetesManifest|"
        r"HelmDeploy|AzureFunctionApp)@",
        re.IGNORECASE,
    )
    violations: list[str] = []
    for trail, value in walk(document):
        location = ".".join(trail) or "<root>"
        key = re.sub(r"[^a-z0-9]", "", trail[-1].lower()) if trail else ""
        if key in forbidden_exact_keys or any(key.endswith(suffix) for suffix in forbidden_key_suffixes):
            violations.append(f"{source.as_posix()}:{location} uses a credential field")
        if key in {"environment", "environments", "deployment", "deployments"}:
            violations.append(f"{source.as_posix()}:{location} defines a deployment target")
        if (
            len(trail) >= 2
            and trail[-2].lower() in {"jobs", "stages"}
            and re.search(r"deploy", trail[-1], re.IGNORECASE)
        ):
            violations.append(f"{source.as_posix()}:{location} defines a deployment job/stage")
        if (
            trail
            and trail[-1].lower() in {"job", "stage"}
            and isinstance(value, str)
            and re.search(r"deploy", value, re.IGNORECASE)
        ):
            violations.append(f"{source.as_posix()}:{location} names a deployment job/stage")
        if isinstance(value, str) and forbidden_text.search(value):
            violations.append(f"{source.as_posix()}:{location} contains a deployment command")
        if trail and trail[-1].lower() == "uses" and isinstance(value, str):
            action = value.split("@", maxsplit=1)[0].lower()
            action_node: Any = document
            for component in trail[:-1]:
                action_node = (
                    action_node[int(component)]
                    if isinstance(action_node, list)
                    else action_node[component]
                )
            if (
                re.search(r"(?:deploy|release|publish)", action, re.IGNORECASE)
                and action not in safe_validation_actions
            ):
                violations.append(f"{source.as_posix()}:{location} invokes deployment action")
            if action == "docker/login-action":
                violations.append(f"{source.as_posix()}:{location} invokes Docker login action")
            if action == "docker/build-push-action" and isinstance(action_node, Mapping):
                action_inputs = action_node.get("with", {})
                push = action_inputs.get("push", False) if isinstance(action_inputs, Mapping) else False
                if push is True or str(push).strip().lower() == "true":
                    violations.append(f"{source.as_posix()}:{location} enables Docker push")
        if trail and trail[-1].lower() == "task" and isinstance(value, str):
            if deployment_task.search(value):
                violations.append(f"{source.as_posix()}:{location} invokes deployment task")
            if re.match(r"Docker@", value, re.IGNORECASE):
                task_node = document
                for component in trail[:-1]:
                    task_node = task_node[int(component)] if isinstance(task_node, list) else task_node[component]
                task_inputs = task_node.get("inputs", {}) if isinstance(task_node, Mapping) else {}
                docker_command = (
                    str(task_inputs.get("command", "")).strip().lower()
                    if isinstance(task_inputs, Mapping)
                    else ""
                )
                if docker_command in {"push", "login", "buildandpush"}:
                    violations.append(f"{source.as_posix()}:{location} invokes unsafe Docker task")
    return violations


@pytest.mark.policy
def test_all_yaml_is_validation_only_and_has_no_credentials() -> None:
    violations: list[str] = []
    for path in yaml_paths():
        relative = path.relative_to(REPOSITORY_ROOT)
        violations.extend(validation_policy_violations(load_yaml(relative), relative))
    assert not violations, "Validation-only policy violations:\n" + "\n".join(violations)


@pytest.mark.github
@pytest.mark.policy
def test_github_workflows_never_grant_write_permissions() -> None:
    violations: list[str] = []
    for path in sorted((REPOSITORY_ROOT / ".github/workflows").glob("*.y*ml")):
        relative = path.relative_to(REPOSITORY_ROOT)
        workflow = load_yaml(relative)
        assert_contents_read(workflow, relative)
        for trail, value in walk(workflow):
            if not trail or trail[-1] != "permissions":
                continue
            if not isinstance(value, Mapping):
                violations.append(
                    f"{relative.as_posix()}:{'.'.join(trail)} must be a permissions mapping"
                )
                continue
            for permission, access in value.items():
                if str(access).lower() in {"write", "write-all"}:
                    violations.append(
                        f"{relative.as_posix()}:{'.'.join(trail)}.{permission} grants {access}"
                    )
    assert not violations, "Write permission grants are forbidden:\n" + "\n".join(violations)


@pytest.mark.policy
def test_shell_blocks_are_strict_and_do_not_interpolate_parameters() -> None:
    violations: list[str] = []
    for path in yaml_paths():
        relative = path.relative_to(REPOSITORY_ROOT)
        document = load_yaml(relative)
        for trail, shell, command in run_blocks(document):
            location = f"{relative.as_posix()}:{'.'.join(trail)}"
            if PARAMETER_EXPRESSION.search(command):
                violations.append(f"{location} directly interpolates an untrusted parameter")
            if shell.startswith("bash") or re.search(r"(^|/)bash\b", shell):
                if not re.search(r"(?m)^\s*set -euo pipefail\s*$", command):
                    violations.append(f"{location} lacks 'set -euo pipefail'")
            elif shell.startswith("pwsh") or shell.startswith("powershell"):
                if not re.search(
                    r"(?mi)^\s*\$ErrorActionPreference\s*=\s*['\"]Stop['\"]\s*$",
                    command,
                ):
                    violations.append(f"{location} lacks ErrorActionPreference Stop")
            else:
                violations.append(f"{location} must declare shell: bash or shell: pwsh")
    assert not violations, "Unsafe shell blocks:\n" + "\n".join(violations)


@pytest.mark.policy
def test_path_parameters_are_passed_via_environment_and_quoted() -> None:
    violations: list[str] = []
    for path in yaml_paths():
        relative = path.relative_to(REPOSITORY_ROOT)
        document = load_yaml(relative)
        commands: list[str] = []
        for trail, _shell, command in run_blocks(document):
            commands.append(command)
            for line_number, line in enumerate(command.splitlines(), start=1):
                for match in PATH_VARIABLE.finditer(line):
                    if not occurrence_is_quoted(line, match.start()):
                        violations.append(
                            f"{relative.as_posix()}:{'.'.join(trail)}:{line_number} "
                            f"uses unquoted {match.group(0)!r}"
                        )
        required_paths = {
            GITHUB_WORKFLOWS["terraform"]: {"working-directory"},
            GITHUB_WORKFLOWS["powershell"]: {"working-directory"},
            GITHUB_WORKFLOWS["python"]: {"working-directory"},
            AZURE_PIPELINES["terraform"]: {"working-directory"},
            AZURE_PIPELINES["powershell"]: {"working-directory", "test-path"},
            AZURE_PIPELINES["python"]: {"working-directory", "test-path"},
        }.get(relative, set())
        if required_paths:
            command_text = "\n".join(commands)
            for path_name in required_paths:
                if not has_canonical_containment(command_text, path_name):
                    violations.append(
                        f"{relative.as_posix()} must canonicalize {path_name}, then enforce "
                        "a checkout-root prefix boundary"
                    )
        for trail, value in walk(document):
            if (
                trail
                and trail[-1] == "working-directory"
                and isinstance(value, str)
                and any(
                    scope == "inputs" and name.lower() == "working-directory"
                    for scope, name in expressions({"value": value})
                )
            ):
                violations.append(
                    f"{relative.as_posix()}:{'.'.join(trail)} uses an unvalidated path directly"
                )
    assert not violations, "Unsafe path handling:\n" + "\n".join(violations)


@pytest.mark.selftest
def test_github_yaml_loader_preserves_on_as_a_string_key() -> None:
    parsed = yaml.load("on:\n  workflow_call:\npermissions:\n  contents: read\n", Loader=GithubYamlLoader)
    assert "on" in parsed
    assert True not in parsed


@pytest.mark.selftest
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("${{ inputs.working-directory }}", [("inputs", "working-directory")]),
        ("${{ inputs['test-path'] }}", [("inputs", "test-path")]),
        ('${{ inputs["python-version"] }}', [("inputs", "python-version")]),
        ("${{ parameters.workingDirectory }}", [("parameters", "workingDirectory")]),
        ("${{ parameters ['testPath'] }}", [("parameters", "testPath")]),
    ],
)
def test_parameter_expression_parser_handles_dot_and_bracket_syntax(
    value: str, expected: list[tuple[str, str]]
) -> None:
    assert expressions({"value": value}) == expected


@pytest.mark.selftest
def test_input_usage_must_be_wired_not_merely_mentioned() -> None:
    document = {
        "name": "${{ inputs.working-directory }}",
        "jobs": {
            "validate": {
                "steps": [
                    {
                        "env": {"TEST_PATH": "${{ inputs['test-path'] }}"},
                        "run": 'pytest "$TEST_PATH"',
                    }
                ]
            }
        },
    }
    assert wired_expression_names(document, "inputs", {"env", "with"}) == {"test-path"}


@pytest.mark.selftest
def test_each_path_variable_occurrence_must_be_quoted() -> None:
    safe = 'realpath -- "$WORKING_DIRECTORY" "$TEST_PATH"'
    unsafe = 'realpath -- $WORKING_DIRECTORY "$TEST_PATH"'
    assert all(occurrence_is_quoted(safe, match.start()) for match in PATH_VARIABLE.finditer(safe))
    assert not all(
        occurrence_is_quoted(unsafe, match.start()) for match in PATH_VARIABLE.finditer(unsafe)
    )


@pytest.mark.selftest
def test_validation_tool_names_in_echo_comments_and_here_documents_do_not_count() -> None:
    adversarial = """# terraform validate
echo "terraform fmt -check"
printf '%s' 'terraform init -backend=false'
cat <<'COMMANDS' >/dev/null
tflint
terraform validate
COMMANDS
cat <<COMMANDS_2 # ignored validation text
terraform validate
COMMANDS_2
"""
    assert not has_executable_invocation(adversarial, r"^(?:&\s*)?terraform\s+validate\b")
    assert not has_executable_invocation(adversarial, r"^(?:&\s*)?tflint\b")
    assert has_executable_invocation("terraform validate", r"^(?:&\s*)?terraform\s+validate\b")


@pytest.mark.selftest
def test_containment_requires_canonicalization_before_boundary_check() -> None:
    safe = """safe_path="$(realpath -- "$WORKING_DIRECTORY")"
case "$safe_path" in
  "$GITHUB_WORKSPACE"/*) ;;
  *) exit 2 ;;
esac
"""
    keyword_only = 'realpath "$WORKING_DIRECTORY"\necho "$GITHUB_WORKSPACE"'
    reversed_order = """case "$WORKING_DIRECTORY" in
  "$GITHUB_WORKSPACE"/*) ;;
esac
safe_path="$(realpath -- "$WORKING_DIRECTORY")"
"""
    compares_original = """safe_path="$(realpath -- "$WORKING_DIRECTORY")"
case "$WORKING_DIRECTORY" in
  "$GITHUB_WORKSPACE"/*) ;;
  *) exit 2 ;;
esac
"""
    derived_root_alias = """root_alias="$(realpath -- "$GITHUB_WORKSPACE/../")"
safe_path="$(realpath -- "$WORKING_DIRECTORY")"
case "$safe_path" in
  "$root_alias"/*) ;;
  *) exit 2 ;;
esac
"""
    assert has_canonical_containment(safe, "working-directory")
    assert not has_canonical_containment(safe, "test-path")
    assert not has_canonical_containment(keyword_only, "working-directory")
    assert not has_canonical_containment(reversed_order, "working-directory")
    assert not has_canonical_containment(compares_original, "working-directory")
    assert not has_canonical_containment(derived_root_alias, "working-directory")


@pytest.mark.selftest
def test_policy_allows_build_only_docker_and_arbitrary_write_text() -> None:
    safe = {
        "steps": [
            {"uses": "actions/checkout@v4"},
            {"uses": "docker/build-push-action@v6", "with": {"push": False}},
            {
                "task": "Docker@2",
                "inputs": {
                    "command": "build",
                    "repository": "login-service",
                    "Dockerfile": "Dockerfile",
                },
            },
            {"note": "write"},
        ]
    }
    assert validation_policy_violations(safe, Path("safe.yml")) == []


@pytest.mark.selftest
@pytest.mark.parametrize(
    "document",
    [
        {"inputs": {"password": "value"}},
        {"inputs": {"release_token": "value"}},
        {"inputs": {"api-key": "value"}},
        {"inputs": {"private_key": "value"}},
        {"steps": [{"uses": "vendor/publish-results@v1"}]},
        {"steps": [{"uses": "docker/login-action@v3"}]},
        {"steps": [{"uses": "docker/build-push-action@v6", "with": {"push": True}}]},
        {"steps": [{"task": "Docker@2", "inputs": {"command": "buildAndPush"}}]},
        {"steps": [{"task": "Docker@2", "inputs": {"command": "login"}}]},
    ],
)
def test_policy_rejects_credential_and_delivery_bypasses(document: Mapping[str, Any]) -> None:
    assert validation_policy_violations(document, Path("unsafe.yml"))
