from __future__ import annotations

from cuanta.domain.detection import VerifyTier, verify_tier
from cuanta.domain.manifests import ProjectFiles, detect_stack


def _files(
    texts: dict[str, str], present: set[str] | None = None, tests: dict[str, int] | None = None
) -> ProjectFiles:
    return ProjectFiles(
        texts=texts,
        present=frozenset(present or set()) | frozenset(texts),
        test_files=tests or {},
        directory_name="demo",
    )


def test_requirements_txt() -> None:
    stack, name = detect_stack(
        _files({"requirements.txt": "Django==5.0.1\npytest\n# comment\n-r base.txt\n"})
    )
    assert stack.framework == "django"
    assert stack.framework_version == "5.0.1"
    assert stack.test_runner == "pytest"
    assert name == "demo"
    decision = verify_tier(stack.signals)
    assert decision.tier is VerifyTier.STRONG
    assert decision.evidence == "pytest, no typecheck"


def test_poetry_pyproject() -> None:
    text = '[tool.poetry]\nname = "svc"\n[tool.poetry.dependencies]\npython = "^3.12"\nflask = "^3"\n[tool.poetry.group.dev.dependencies]\npytest = "*"\npyright = "*"\n'
    stack, name = detect_stack(_files({"pyproject.toml": text}))
    assert name == "svc"
    assert stack.package_manager == "poetry"
    assert stack.framework == "flask"
    assert stack.test_command == "poetry run pytest"
    assert verify_tier(stack.signals).tier is VerifyTier.STRONG


def test_cargo() -> None:
    text = '[package]\nname = "tool"\nedition = "2021"\n[dependencies]\naxum = "0.7"\n'
    stack, _ = detect_stack(_files({"Cargo.toml": text}, {"src/main.rs", "tests"}))
    assert stack.language == "rust"
    assert stack.framework == "axum"
    assert stack.entry_points == ("src/main.rs",)
    assert verify_tier(stack.signals).tier is VerifyTier.STRONG


def test_maven_and_gradle() -> None:
    pom = "<project><artifactId>app</artifactId><properties><java.version>21</java.version></properties><dependency><artifactId>junit-jupiter</artifactId></dependency></project>"
    stack, name = detect_stack(_files({"pom.xml": pom}))
    assert (stack.language, stack.language_version, name) == ("java", "21", "app")
    assert verify_tier(stack.signals).tier is VerifyTier.STRONG
    gradle = 'plugins { id "org.jetbrains.kotlin.jvm" }\ntest { useJUnitPlatform() }\n'
    kotlin, _ = detect_stack(_files({"build.gradle.kts": gradle}, {"gradlew"}))
    assert kotlin.language == "kotlin"
    assert kotlin.test_command == "./gradlew test"


def test_composer_and_gemfile() -> None:
    composer = '{"name":"acme/site","require":{"php":"^8.2","laravel/framework":"^11.0"},"require-dev":{"phpunit/phpunit":"^10","phpstan/phpstan":"^1"}}'
    stack, _ = detect_stack(_files({"composer.json": composer}))
    assert (stack.language, stack.framework) == ("php", "laravel")
    assert verify_tier(stack.signals).tier is VerifyTier.STRONG
    gemfile = "source 'https://rubygems.org'\nruby '3.3.0'\ngem 'rails', '7.1.3'\ngem 'rspec-rails'\ngem 'rubocop'\n"
    ruby, _ = detect_stack(_files({"Gemfile": gemfile}))
    assert (ruby.language, ruby.language_version, ruby.framework_version) == (
        "ruby",
        "3.3.0",
        "7.1.3",
    )
    assert ruby.test_command == "bundle exec rspec"
    ruby_tier = verify_tier(ruby.signals)
    assert ruby_tier.tier is VerifyTier.STRONG
    assert ruby_tier.evidence == "rspec + rubocop, no typecheck"


def test_monorepo_prefers_backend_with_runner() -> None:
    package = '{"name":"ui","dependencies":{"react":"18.0.0"},"scripts":{"build":"vite build"}}'
    pyproject = '[project]\nname = "api"\ndependencies = ["fastapi", "pytest", "mypy"]\n'
    stack, name = detect_stack(_files({"package.json": package, "pyproject.toml": pyproject}))
    assert stack.language == "python"
    assert stack.manifests == ("package.json", "pyproject.toml")
    assert name == "ui"


def test_vitest_package_with_pnpm() -> None:
    package = '{"name":"lib","packageManager":"pnpm@9.1.0","scripts":{"test":"vitest run","typecheck":"tsc -b"},"devDependencies":{"vitest":"^1.6.0","typescript":"^5.5.0"}}'
    stack, _ = detect_stack(_files({"package.json": package}))
    assert stack.package_manager == "pnpm"
    assert stack.test_runner == "vitest"
    assert stack.test_command == "pnpm test"
    assert stack.typecheck_command == "pnpm run typecheck"


def test_malformed_manifests_do_not_crash() -> None:
    stack, _ = detect_stack(
        _files({"package.json": "{", "pyproject.toml": "= ="}, tests={"ext:ts": 2})
    )
    assert stack.language == "typescript"
    assert not stack.verified
