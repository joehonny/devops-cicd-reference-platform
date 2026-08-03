"""Static, offline safety contracts for the repository's pipeline YAML."""

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
        assert specification.get("type") == "string", (
            f"{source.as_posix()}: input {name!r} must declare type: string"
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


@pytest.mark.parametrize("relative_path", EXPECTED_YAML, ids=lambda path: path.as_posix())
def test_required_pipeline_file_exists_and_parses(relative_path: Path) -> None:
    load_yaml(relative_path)


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


@pytest.mark.parametrize(
    ("name", "expected_inputs"),
    [
        ("terraform", {"working-directory", "terraform-version"}),
        ("powershell", {"working-directory", "powershell-version", "test-path"}),
        ("python", {"working-directory", "python-version", "test-path"}),
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


def test_all_yaml_is_validation_only_and_has_no_credentials() -> None:
    forbidden_key = re.compile(
        r"(?:secret|credential|id-token|oidc|service.?connection|"
        r"connectedServiceNameARM|client.?secret|access.?key|subscription)",
        re.IGNORECASE,
    )
    forbidden_text = re.compile(
        r"(?:terraform\s+apply\b|azure/login|configure-aws-credentials|"
        r"az\s+(?:deployment|webapp\s+deploy)\b|aws\s+cloudformation\s+deploy\b|"
        r"kubectl\s+(?:apply|create|delete)\b|\bdeploy(?:ment)?\s+(?:job|stage)\b)",
        re.IGNORECASE,
    )
    violations: list[str] = []
    for path in yaml_paths():
        relative = path.relative_to(REPOSITORY_ROOT)
        document = load_yaml(relative)
        for trail, value in walk(document):
            location = ".".join(trail) or "<root>"
            if trail and forbidden_key.search(trail[-1]):
                violations.append(f"{relative.as_posix()}:{location} uses a credential field")
            if trail and trail[-1].lower() in {"deployment", "deployments"}:
                violations.append(f"{relative.as_posix()}:{location} defines a deployment")
            if isinstance(value, str) and value.lower() == "write":
                violations.append(f"{relative.as_posix()}:{location} grants write permission")
            if (
                trail
                and trail[-1].lower() in {"job", "stage"}
                and isinstance(value, str)
                and re.search(r"deploy", value, re.IGNORECASE)
            ):
                violations.append(f"{relative.as_posix()}:{location} names a deployment job/stage")
            if isinstance(value, str) and forbidden_text.search(value):
                violations.append(f"{relative.as_posix()}:{location} contains a deployment command")
    assert not violations, "Validation-only policy violations:\n" + "\n".join(violations)


def test_github_workflows_never_grant_write_permissions() -> None:
    violations: list[str] = []
    for path in sorted((REPOSITORY_ROOT / ".github/workflows").glob("*.y*ml")):
        relative = path.relative_to(REPOSITORY_ROOT)
        workflow = load_yaml(relative)
        assert_contents_read(workflow, relative)
        for trail, value in walk(workflow):
            if trail and trail[-1] == "permissions" and value != {"contents": "read"}:
                violations.append(f"{relative.as_posix()}:{'.'.join(trail)} = {value!r}")
            if isinstance(value, str) and value.lower() == "write":
                violations.append(f"{relative.as_posix()}:{'.'.join(trail)} grants write")
    assert not violations, "Write permission grants are forbidden:\n" + "\n".join(violations)


def test_shell_blocks_are_strict_and_do_not_interpolate_parameters() -> None:
    violations: list[str] = []
    interpolation = re.compile(r"\$\{\{\s*(?:inputs|parameters)\.", re.IGNORECASE)
    for path in yaml_paths():
        relative = path.relative_to(REPOSITORY_ROOT)
        document = load_yaml(relative)
        for trail, shell, command in run_blocks(document):
            location = f"{relative.as_posix()}:{'.'.join(trail)}"
            if interpolation.search(command):
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


def test_path_parameters_are_passed_via_environment_and_quoted() -> None:
    violations: list[str] = []
    path_variable = re.compile(
        r"\$(?:(?:env:)?(?:WORKING_DIRECTORY|TEST_PATH)|"
        r"\{(?:WORKING_DIRECTORY|TEST_PATH)\})",
        re.IGNORECASE,
    )
    quoted_path = re.compile(
        r"(?:['\"]\$(?:(?:env:)?(?:WORKING_DIRECTORY|TEST_PATH)|"
        r"\{(?:WORKING_DIRECTORY|TEST_PATH)\})['\"]|"
        r"Join-Path\s+[^\r\n]*(?:WORKING_DIRECTORY|TEST_PATH))",
        re.IGNORECASE,
    )
    for path in yaml_paths():
        relative = path.relative_to(REPOSITORY_ROOT)
        document = load_yaml(relative)
        commands: list[str] = []
        for trail, _shell, command in run_blocks(document):
            commands.append(command)
            if path_variable.search(command) and not quoted_path.search(command):
                violations.append(
                    f"{relative.as_posix()}:{'.'.join(trail)} uses an unquoted path variable"
                )
        if relative in {
            GITHUB_WORKFLOWS["terraform"],
            GITHUB_WORKFLOWS["powershell"],
            GITHUB_WORKFLOWS["python"],
            AZURE_PIPELINES["terraform"],
            AZURE_PIPELINES["powershell"],
            AZURE_PIPELINES["python"],
        }:
            command_text = "\n".join(commands)
            if not re.search(r"(?:realpath|Resolve-Path|GetFullPath)", command_text, re.IGNORECASE):
                violations.append(f"{relative.as_posix()} does not canonicalize path parameters")
            if not re.search(
                r"(?:GITHUB_WORKSPACE|BUILD_SOURCESDIRECTORY)", command_text, re.IGNORECASE
            ):
                violations.append(f"{relative.as_posix()} does not constrain paths to the checkout")
        for trail, value in walk(document):
            if (
                trail
                and trail[-1] == "working-directory"
                and isinstance(value, str)
                and re.search(r"\$\{\{\s*inputs\.working-directory", value, re.IGNORECASE)
            ):
                violations.append(
                    f"{relative.as_posix()}:{'.'.join(trail)} uses an unvalidated path directly"
                )
    assert not violations, "Unsafe path handling:\n" + "\n".join(violations)


def test_github_yaml_loader_preserves_on_as_a_string_key() -> None:
    parsed = yaml.load("on:\n  workflow_call:\npermissions:\n  contents: read\n", Loader=GithubYamlLoader)
    assert "on" in parsed
    assert True not in parsed
