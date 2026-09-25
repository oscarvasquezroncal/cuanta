from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from cuanta.domain.detection import Stack, VerifySignals

MANIFEST_ORDER = (
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "composer.json",
    "Gemfile",
)

LOCKFILES = (
    "package-lock.json",
    "uv.lock",
    "poetry.lock",
)

PRESENCE_PROBES = (
    "tsconfig.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lockb",
    "bun.lock",
    "package-lock.json",
    "uv.lock",
    "poetry.lock",
    "pdm.lock",
    "Pipfile.lock",
    "pytest.ini",
    "conftest.py",
    "tests/conftest.py",
    "mypy.ini",
    ".mypy.ini",
    "pyrightconfig.json",
    "setup.cfg",
    "tox.ini",
    "ruff.toml",
    ".ruff.toml",
    ".eslintrc.json",
    ".eslintrc.js",
    "eslint.config.js",
    "eslint.config.mjs",
    "biome.json",
    "vitest.config.ts",
    "vitest.config.js",
    "jest.config.js",
    "jest.config.ts",
    "manage.py",
    "main.py",
    "app.py",
    "main.go",
    "src/main.rs",
    "src/lib.rs",
    "src/index.ts",
    "src/main.ts",
    "src/index.js",
    "src/main.js",
    "index.js",
    "index.ts",
    "server.js",
    "app.js",
    "tests",
    "Gemfile.lock",
    "composer.lock",
    "mvnw",
    "gradlew",
    "phpstan.neon",
)


@dataclass(frozen=True, slots=True)
class ProjectFiles:
    texts: Mapping[str, str]
    present: frozenset[str]
    test_files: Mapping[str, int] = field(default_factory=dict)
    entry_candidates: tuple[str, ...] = ()
    directory_name: str = "project"


@dataclass(frozen=True, slots=True)
class Partial:
    manifest: str
    name: str = ""
    language: str = "unknown"
    language_version: str = ""
    framework: str = ""
    framework_version: str = ""
    package_manager: str = ""
    runners: tuple[str, ...] = ()
    typecheckers: tuple[str, ...] = ()
    linters: tuple[str, ...] = ()
    builders: tuple[str, ...] = ()
    placeholders: tuple[str, ...] = ()
    entry_points: tuple[str, ...] = ()
    test_command: str = ""
    typecheck_command: str = ""
    build_command: str = ""


_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_PIN = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*==\s*([^\s;,]+)")


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_name(spec: str) -> str:
    match = _NAME.match(spec)
    return _normalize(match.group(1)) if match else ""


def _spec_versions(specs: Iterable[str]) -> dict[str, str]:
    versions: dict[str, str] = {}
    for spec in specs:
        match = _NAME.match(spec)
        if match is None:
            continue
        rest = re.sub(r"^\s*\[[^\]]*\]", "", spec[match.end() :]).split(";", 1)[0]
        versions[_normalize(match.group(1))] = _clean_version(rest)
    return versions


def _strings(values: object) -> list[str]:
    if isinstance(values, list):
        return [item for item in values if isinstance(item, str)]
    return []


def _table(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _first(options: Iterable[tuple[str, str]], names: Mapping[str, str]) -> tuple[str, str]:
    for key, label in options:
        if key in names:
            return label, names[key]
    return "", ""


def _clean_version(spec: str) -> str:
    return spec.strip().lstrip("^~=>< v").split(",")[0].strip()


def _lock_versions_toml(text: str) -> dict[str, str]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return {}
    packages = data.get("package", [])
    versions: dict[str, str] = {}
    if isinstance(packages, list):
        for package in packages:
            if isinstance(package, dict):
                name, version = package.get("name"), package.get("version")
                if isinstance(name, str) and isinstance(version, str):
                    versions[_normalize(name)] = version
    return versions


def _lock_versions_npm(text: str) -> dict[str, str]:
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    versions: dict[str, str] = {}
    packages = data.get("packages") if isinstance(data, dict) else None
    if isinstance(packages, dict):
        for key, value in packages.items():
            if key.startswith("node_modules/") and isinstance(value, dict):
                version = value.get("version")
                if isinstance(version, str):
                    versions[key.removeprefix("node_modules/")] = version
    return versions


PY_FRAMEWORKS = (
    ("django", "django"),
    ("fastapi", "fastapi"),
    ("flask", "flask"),
    ("litestar", "litestar"),
    ("starlette", "starlette"),
    ("aiohttp", "aiohttp"),
    ("tornado", "tornado"),
    ("typer", "typer"),
    ("click", "click"),
)

JS_FRAMEWORKS = (
    ("next", "next"),
    ("@nestjs/core", "nestjs"),
    ("@angular/core", "angular"),
    ("nuxt", "nuxt"),
    ("@sveltejs/kit", "sveltekit"),
    ("svelte", "svelte"),
    ("vue", "vue"),
    ("react", "react"),
    ("express", "express"),
    ("fastify", "fastify"),
    ("hono", "hono"),
    ("koa", "koa"),
)

GO_FRAMEWORKS = (
    ("github.com/gin-gonic/gin", "gin"),
    ("github.com/labstack/echo/v4", "echo"),
    ("github.com/gofiber/fiber/v2", "fiber"),
    ("github.com/go-chi/chi/v5", "chi"),
    ("github.com/gorilla/mux", "gorilla/mux"),
    ("github.com/spf13/cobra", "cobra"),
)

RUST_FRAMEWORKS = (
    ("actix-web", "actix-web"),
    ("axum", "axum"),
    ("rocket", "rocket"),
    ("warp", "warp"),
    ("clap", "clap"),
)

PLACEHOLDER_TEST = re.compile(r"no test", re.IGNORECASE)


def _python_prefix(manager: str) -> str:
    return {
        "uv": "uv run ",
        "poetry": "poetry run ",
        "pdm": "pdm run ",
        "pipenv": "pipenv run ",
    }.get(manager, "")


PYTHON_BUILD = {"uv": "uv build", "poetry": "poetry build", "pdm": "pdm build"}


def python_build_command(manager: str) -> str:
    return PYTHON_BUILD.get(manager, "python -m build")


def _python_signals(
    names: Mapping[str, str], tools: Mapping[str, object], files: ProjectFiles, manager: str
) -> dict[str, object]:
    present = files.present
    prefix = _python_prefix(manager)
    runners: list[str] = []
    if (
        "pytest" in names
        or "pytest" in tools
        or {"pytest.ini", "conftest.py", "tests/conftest.py"} & present
    ):
        runners.append("pytest")
    typecheckers: list[str] = []
    if "mypy" in names or "mypy" in tools or {"mypy.ini", ".mypy.ini"} & present:
        typecheckers.append("mypy")
    if "pyright" in names or "pyright" in tools or "pyrightconfig.json" in present:
        typecheckers.append("pyright")
    linters: list[str] = []
    if "ruff" in names or "ruff" in tools or {"ruff.toml", ".ruff.toml"} & present:
        linters.append("ruff")
    for linter in ("flake8", "pylint"):
        if linter in names:
            linters.append(linter)
    return {
        "runners": tuple(runners),
        "typecheckers": tuple(typecheckers),
        "linters": tuple(linters),
        "test_command": f"{prefix}pytest" if runners else "",
        "typecheck_command": f"{prefix}{typecheckers[0]}" if typecheckers else "",
    }


def _python_manager(files: ProjectFiles, has_poetry: bool) -> str:
    present = files.present
    if "uv.lock" in present:
        return "uv"
    if "poetry.lock" in present or has_poetry:
        return "poetry"
    if "pdm.lock" in present:
        return "pdm"
    if "Pipfile.lock" in present:
        return "pipenv"
    return "pip"


def _python_entries(files: ProjectFiles, scripts: Mapping[str, object]) -> tuple[str, ...]:
    entries = [f"{name} = {target}" for name, target in scripts.items() if isinstance(target, str)]
    for candidate in ("manage.py", "main.py", "app.py"):
        if candidate in files.present:
            entries.append(candidate)
    entries.extend(path for path in files.entry_candidates if path.endswith("__main__.py"))
    return tuple(dict.fromkeys(entries))


def parse_pyproject(text: str, files: ProjectFiles) -> Partial | None:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None
    project = _table(data.get("project"))
    tool = _table(data.get("tool"))
    poetry = _table(tool.get("poetry"))
    specs = _strings(project.get("dependencies"))
    for group in _table(project.get("optional-dependencies")).values():
        specs.extend(_strings(group))
    for group in _table(data.get("dependency-groups")).values():
        specs.extend(_strings(group))
    specs.extend(_strings(_table(tool.get("uv")).get("dev-dependencies")))
    names = _spec_versions(specs)
    for key in _table(poetry.get("dependencies")):
        names.setdefault(_normalize(key), "")
    for group in _table(poetry.get("group")).values():
        for key in _table(_table(group).get("dependencies")):
            names.setdefault(_normalize(key), "")
    manager = _python_manager(files, bool(poetry))
    locked: dict[str, str] = {}
    for lockfile in ("uv.lock", "poetry.lock"):
        if lockfile in files.texts:
            locked.update(_lock_versions_toml(files.texts[lockfile]))
    framework, declared = _first(PY_FRAMEWORKS, names)
    signals = _python_signals(names, tool, files, manager)
    build_system = _table(data.get("build-system"))
    backend = build_system.get("build-backend")
    builders = (f"build-system ({backend})",) if isinstance(backend, str) else ()
    python_version = project.get("requires-python") or _table(poetry.get("dependencies")).get(
        "python"
    )
    scripts = _table(project.get("scripts")) or _table(poetry.get("scripts"))
    return Partial(
        manifest="pyproject.toml",
        name=str(project.get("name") or poetry.get("name") or ""),
        language="python",
        language_version=str(python_version or ""),
        framework=framework,
        framework_version=locked.get(framework, declared) if framework else "",
        package_manager=manager,
        runners=_tuple(signals["runners"]),
        typecheckers=_tuple(signals["typecheckers"]),
        linters=_tuple(signals["linters"]),
        builders=builders,
        entry_points=_python_entries(files, scripts),
        test_command=str(signals["test_command"]),
        typecheck_command=str(signals["typecheck_command"]),
        build_command=python_build_command(manager) if builders else "",
    )


def _tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, tuple):
        return tuple(str(item) for item in value)
    return ()


def parse_requirements(text: str, files: ProjectFiles) -> Partial:
    names: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        pinned = _PIN.match(line)
        if pinned:
            names[_normalize(pinned.group(1))] = pinned.group(2)
            continue
        name = _requirement_name(line)
        if name:
            names[name] = ""
    framework, version = _first(PY_FRAMEWORKS, names)
    manager = _python_manager(files, False)
    signals = _python_signals(names, {}, files, manager)
    return Partial(
        manifest="requirements.txt",
        language="python",
        framework=framework,
        framework_version=version,
        package_manager=manager,
        runners=_tuple(signals["runners"]),
        typecheckers=_tuple(signals["typecheckers"]),
        linters=_tuple(signals["linters"]),
        entry_points=_python_entries(files, {}),
        test_command=str(signals["test_command"]),
        typecheck_command=str(signals["typecheck_command"]),
    )


def _js_manager(data: Mapping[str, object], present: frozenset[str]) -> str:
    declared = data.get("packageManager")
    if isinstance(declared, str) and "@" in declared:
        return declared.split("@", 1)[0]
    for lockfile, manager in (
        ("pnpm-lock.yaml", "pnpm"),
        ("yarn.lock", "yarn"),
        ("bun.lockb", "bun"),
        ("bun.lock", "bun"),
        ("package-lock.json", "npm"),
    ):
        if lockfile in present:
            return manager
    return "npm"


def _js_entries(data: Mapping[str, object], files: ProjectFiles) -> tuple[str, ...]:
    entries: list[str] = []
    for key in ("main", "module"):
        value = data.get(key)
        if isinstance(value, str):
            entries.append(value)
    binaries = data.get("bin")
    if isinstance(binaries, str):
        entries.append(binaries)
    elif isinstance(binaries, dict):
        entries.extend(value for value in binaries.values() if isinstance(value, str))
    for candidate in (
        "src/index.ts",
        "src/main.ts",
        "src/index.js",
        "src/main.js",
        "index.ts",
        "index.js",
        "server.js",
        "app.js",
    ):
        if candidate in files.present:
            entries.append(candidate)
    return tuple(dict.fromkeys(entries))


def parse_package_json(text: str, files: ProjectFiles) -> Partial | None:
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    deps: dict[str, str] = {}
    for key in ("dependencies", "devDependencies", "optionalDependencies"):
        section = data.get(key)
        if isinstance(section, dict):
            deps.update({name: str(spec) for name, spec in section.items()})
    scripts_raw = data.get("scripts")
    scripts = (
        {key: str(value) for key, value in scripts_raw.items()}
        if isinstance(scripts_raw, dict)
        else {}
    )
    present = files.present
    manager = _js_manager(data, present)
    locked = _lock_versions_npm(files.texts.get("package-lock.json", ""))
    typescript = "typescript" in deps or "tsconfig.json" in present
    test_script = scripts.get("test", "")
    placeholder = bool(test_script) and bool(PLACEHOLDER_TEST.search(test_script))
    runners: list[str] = []
    for runner in ("vitest", "jest", "mocha", "ava"):
        if runner in deps or re.search(rf"\b{runner}\b", test_script):
            runners.append(runner)
    if not runners and re.search(r"node\s+--test", test_script):
        runners.append("node --test")
    if placeholder and not runners:
        placeholders: tuple[str, ...] = (f'"test": "{test_script}"',)
    else:
        placeholders = ()
    typecheckers: list[str] = []
    if "typescript" in deps:
        typecheckers.append("tsc")
    linters = [
        label
        for key, label in (("eslint", "eslint"), ("@biomejs/biome", "biome"), ("oxlint", "oxlint"))
        if key in deps
    ]
    builders = (f"{manager} run build",) if "build" in scripts else ()
    framework, spec = _first(JS_FRAMEWORKS, deps)
    run = "npm test" if manager == "npm" else f"{manager} test"
    if runners and (not test_script or placeholder):
        run = f"npx {runners[0]}" + (" run" if runners[0] == "vitest" else "")
    if "typecheck" in scripts:
        typecheck_command = f"{manager} run typecheck"
    elif typecheckers:
        typecheck_command = "npx tsc --noEmit"
    else:
        typecheck_command = ""
    engines = data.get("engines")
    node = engines.get("node") if isinstance(engines, dict) else None
    language_version = locked.get("typescript", _clean_version(deps.get("typescript", "")))
    if not typescript:
        language_version = f"node {node}" if isinstance(node, str) else ""
    return Partial(
        manifest="package.json",
        name=str(data.get("name") or ""),
        language="typescript" if typescript else "javascript",
        language_version=language_version,
        framework=framework,
        framework_version=locked.get(
            next((key for key, label in JS_FRAMEWORKS if label == framework), ""),
            _clean_version(spec),
        )
        if framework
        else "",
        package_manager=manager,
        runners=tuple(runners),
        typecheckers=tuple(typecheckers),
        linters=tuple(linters),
        builders=builders,
        placeholders=placeholders,
        entry_points=_js_entries(data, files),
        test_command=run if runners else "",
        typecheck_command=typecheck_command,
        build_command=builders[0] if builders else "",
    )


_GO_REQUIRE = re.compile(r"^\s*(?:require\s+)?([\w.\-/]+\.[\w.\-/]+)\s+(v[\w.\-+]+)", re.MULTILINE)


def parse_go_mod(text: str, files: ProjectFiles) -> Partial:
    module = re.search(r"^module\s+(\S+)", text, re.MULTILINE)
    version = re.search(r"^go\s+(\S+)", text, re.MULTILINE)
    requires = {match.group(1): match.group(2) for match in _GO_REQUIRE.finditer(text)}
    framework, framework_version = _first(GO_FRAMEWORKS, requires)
    has_tests = files.test_files.get("go", 0) > 0
    entries = [path for path in files.entry_candidates if path.endswith("main.go")]
    return Partial(
        manifest="go.mod",
        name=module.group(1).rsplit("/", 1)[-1] if module else "",
        language="go",
        language_version=version.group(1) if version else "",
        framework=framework,
        framework_version=framework_version,
        package_manager="go modules",
        runners=("go test",) if has_tests else (),
        typecheckers=("go build",),
        linters=("go vet",),
        entry_points=tuple(entries),
        test_command="go test ./..." if has_tests else "",
        typecheck_command="go build ./...",
        build_command="go build ./...",
    )


def parse_cargo(text: str, files: ProjectFiles) -> Partial | None:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None
    package = _table(data.get("package"))
    deps = {
        **dict.fromkeys(_table(data.get("dependencies")), ""),
        **dict.fromkeys(_table(data.get("dev-dependencies")), ""),
    }
    for key, value in _table(data.get("dependencies")).items():
        if isinstance(value, str):
            deps[key] = value
        elif isinstance(value, dict) and isinstance(value.get("version"), str):
            deps[key] = str(value["version"])
    framework, framework_version = _first(RUST_FRAMEWORKS, deps)
    has_tests = files.test_files.get("rust", 0) > 0 or "tests" in files.present
    entries = tuple(path for path in ("src/main.rs", "src/lib.rs") if path in files.present)
    return Partial(
        manifest="Cargo.toml",
        name=str(package.get("name") or ""),
        language="rust",
        language_version=str(package.get("rust-version") or package.get("edition") or ""),
        framework=framework,
        framework_version=_clean_version(framework_version),
        package_manager="cargo",
        runners=("cargo test",) if has_tests else (),
        typecheckers=("cargo check",),
        builders=("cargo build",),
        entry_points=entries,
        test_command="cargo test" if has_tests else "",
        typecheck_command="cargo check",
        build_command="cargo build",
    )


def parse_pom(text: str, files: ProjectFiles) -> Partial:
    artifact = re.search(r"<artifactId>([^<]+)</artifactId>", text)
    java = re.search(r"<(?:java\.version|maven\.compiler\.source)>([^<]+)<", text)
    tool = "./mvnw" if "mvnw" in files.present else "mvn"
    has_junit = "junit" in text or files.test_files.get("java", 0) > 0
    spring = re.search(r"spring-boot[^<]*</artifactId>\s*<version>([^<]+)<", text)
    return Partial(
        manifest="pom.xml",
        name=artifact.group(1) if artifact else "",
        language="java",
        language_version=java.group(1) if java else "",
        framework="spring-boot" if "spring-boot" in text else "",
        framework_version=spring.group(1) if spring else "",
        package_manager="maven",
        runners=("junit",) if has_junit else (),
        typecheckers=(f"{tool} compile",),
        builders=(f"{tool} package",),
        test_command=f"{tool} test" if has_junit else "",
        typecheck_command=f"{tool} compile",
        build_command=f"{tool} package",
    )


def parse_gradle(text: str, files: ProjectFiles, manifest: str) -> Partial:
    tool = "./gradlew" if "gradlew" in files.present else "gradle"
    kotlin = manifest.endswith(".kts") or "kotlin" in text
    has_tests = "junit" in text.lower() or "useJUnitPlatform" in text
    return Partial(
        manifest=manifest,
        language="kotlin" if kotlin else "java",
        framework="spring-boot" if "org.springframework.boot" in text else "",
        package_manager="gradle",
        runners=("junit",) if has_tests else (),
        typecheckers=(f"{tool} compileJava",) if not kotlin else (f"{tool} compileKotlin",),
        builders=(f"{tool} build",),
        test_command=f"{tool} test" if has_tests else "",
        typecheck_command=f"{tool} classes",
        build_command=f"{tool} build",
    )


def parse_composer(text: str, _files: ProjectFiles) -> Partial | None:
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    deps: dict[str, str] = {}
    for key in ("require", "require-dev"):
        section = data.get(key)
        if isinstance(section, dict):
            deps.update({name: str(spec) for name, spec in section.items()})
    runners = [
        label
        for key, label in (("phpunit/phpunit", "phpunit"), ("pestphp/pest", "pest"))
        if key in deps
    ]
    typecheckers = [
        label
        for key, label in (("phpstan/phpstan", "phpstan"), ("vimeo/psalm", "psalm"))
        if key in deps
    ]
    framework, version = _first(
        (("laravel/framework", "laravel"), ("symfony/framework-bundle", "symfony")), deps
    )
    return Partial(
        manifest="composer.json",
        name=str(data.get("name") or ""),
        language="php",
        language_version=_clean_version(deps.get("php", "")),
        framework=framework,
        framework_version=_clean_version(version),
        package_manager="composer",
        runners=tuple(runners),
        typecheckers=tuple(typecheckers),
        linters=("php-cs-fixer",) if "friendsofphp/php-cs-fixer" in deps else (),
        test_command=f"vendor/bin/{runners[0]}" if runners else "",
        typecheck_command=f"vendor/bin/{typecheckers[0]}" if typecheckers else "",
    )


_GEM = re.compile(r"""^\s*gem\s+['"]([^'"]+)['"](?:\s*,\s*['"]([^'"]+)['"])?""", re.MULTILINE)


def parse_gemfile(text: str, _files: ProjectFiles) -> Partial:
    gems = {match.group(1): match.group(2) or "" for match in _GEM.finditer(text)}
    ruby = re.search(r"""^\s*ruby\s+['"]([^'"]+)['"]""", text, re.MULTILINE)
    runners = [
        label
        for key, label in (("rspec", "rspec"), ("rspec-rails", "rspec"), ("minitest", "minitest"))
        if key in gems
    ]
    runners = list(dict.fromkeys(runners))
    typecheckers = [label for label in ("sorbet", "steep") if label in gems]
    return Partial(
        manifest="Gemfile",
        language="ruby",
        language_version=ruby.group(1) if ruby else "",
        framework="rails" if "rails" in gems else "",
        framework_version=_clean_version(gems.get("rails", "")),
        package_manager="bundler",
        runners=tuple(runners),
        typecheckers=tuple(typecheckers),
        linters=("rubocop",) if "rubocop" in gems else (),
        test_command="bundle exec rspec"
        if "rspec" in runners
        else ("bin/rails test" if runners else ""),
        typecheck_command="bundle exec srb tc" if "sorbet" in typecheckers else "",
    )


Parser = Callable[[str, ProjectFiles], Partial | None]

PARSERS: dict[str, Parser] = {
    "package.json": parse_package_json,
    "pyproject.toml": parse_pyproject,
    "requirements.txt": parse_requirements,
    "go.mod": parse_go_mod,
    "Cargo.toml": parse_cargo,
    "pom.xml": parse_pom,
    "build.gradle": lambda text, files: parse_gradle(text, files, "build.gradle"),
    "build.gradle.kts": lambda text, files: parse_gradle(text, files, "build.gradle.kts"),
    "composer.json": parse_composer,
    "Gemfile": parse_gemfile,
}

_EXTENSION_LANGUAGES = (
    ("python", "py"),
    ("typescript", "ts"),
    ("javascript", "js"),
    ("go", "go"),
    ("rust", "rs"),
)


def _merge(values: Iterable[tuple[str, ...]]) -> tuple[str, ...]:
    merged: list[str] = []
    for group in values:
        merged.extend(group)
    return tuple(dict.fromkeys(merged))


def detect_stack(files: ProjectFiles) -> tuple[Stack, str]:
    partials: list[Partial] = []
    for manifest in MANIFEST_ORDER:
        text = files.texts.get(manifest)
        if text is None:
            continue
        parsed = PARSERS[manifest](text, files)
        if parsed is not None:
            partials.append(parsed)
    if not partials:
        counts = files.test_files
        language = next(
            (name for name, key in _EXTENSION_LANGUAGES if counts.get(f"ext:{key}", 0) > 0),
            "unknown",
        )
        return Stack(language=language, verified=False), files.directory_name
    primary = partials[0]
    if primary.manifest == "package.json" and len(partials) > 1 and not primary.runners:
        backend = next((partial for partial in partials[1:] if partial.runners), None)
        if backend is not None:
            primary = backend
    signals = VerifySignals(
        test_runners=_merge(partial.runners for partial in partials),
        typecheckers=_merge(partial.typecheckers for partial in partials),
        linters=_merge(partial.linters for partial in partials),
        builders=_merge(partial.builders for partial in partials),
        placeholder_tests=_merge(partial.placeholders for partial in partials),
    )
    stack = Stack(
        language=primary.language,
        language_version=primary.language_version,
        framework=primary.framework,
        framework_version=primary.framework_version,
        package_manager=primary.package_manager,
        test_runner=primary.runners[0]
        if primary.runners
        else (signals.test_runners[:1] or ("",))[0],
        entry_points=_merge(partial.entry_points for partial in [primary, *partials]),
        manifests=tuple(partial.manifest for partial in partials),
        verified=True,
        signals=signals,
        test_command=primary.test_command
        or next((partial.test_command for partial in partials if partial.test_command), ""),
        typecheck_command=primary.typecheck_command
        or next(
            (partial.typecheck_command for partial in partials if partial.typecheck_command), ""
        ),
        build_command=primary.build_command
        or next((partial.build_command for partial in partials if partial.build_command), ""),
    )
    name = next((partial.name for partial in partials if partial.name), files.directory_name)
    return stack, name
