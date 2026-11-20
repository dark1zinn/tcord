set export

alias b := tcord::build
alias r := tcord::run

# Resource limits for every compile: 4 parallel jobs, lowered CPU priority.
CARGO_BUILD_JOBS := "4"
PYO3_PYTHON := justfile_directory() + "/.venv/bin/python3"
_nice := "nice -n 10 env -u VIRTUAL_ENV"

# App build recipes
mod tcord './pkgs/tcord/mod.just'

# List recipes
default:
    @just --list

# Abort (after cleaning stale caches) when the disk is nearly full
disk-guard:
    scripts/disk-guard.sh

# Install every workspace package and the dev tooling
sync: disk-guard
    {{_nice}} uv sync --all-packages --group dev

# Build and install the Rust extension into the workspace venv
core: disk-guard
    {{_nice}} uv run --no-sync maturin develop -j 4 --uv --locked -m pkgs/tcord-core/Cargo.toml

# Format Rust and Python sources
fmt:
    cargo fmt --all
    {{_nice}} uv run --no-sync ruff format .

# Run every linter and type checker
lint: disk-guard
    cargo fmt --all --check
    {{_nice}} cargo clippy -j 4 --workspace --all-targets -- -D warnings
    {{_nice}} uv run --no-sync ruff check .
    {{_nice}} uv run --no-sync ruff format --check .
    {{_nice}} uv run --no-sync ty check

# Audit Rust dependencies (advisories, licenses, sources)
deny:
    cargo deny check

# Run the Rust engine tests
test-rust: disk-guard
    {{_nice}} cargo test -j 4 -p tcord-engine

# Build the extension and run the Python test suites
test-py: core
    {{_nice}} uv run --no-sync pytest

_locale := "pkgs/tcord/src/tcord/locale"

# Extract translatable strings into the catalog template
i18n-extract:
    {{_nice}} uv run --no-sync pybabel extract -F pkgs/tcord/babel.cfg -k _ -k N_ -k ngettext:1,2 -k pgettext:1c,2 --no-location --sort-output --project=tcord -o {{_locale}}/tcord.pot pkgs/tcord/src

# Merge the template into every language catalog
i18n-update:
    {{_nice}} uv run --no-sync pybabel update -i {{_locale}}/tcord.pot -d {{_locale}} -D tcord --no-fuzzy-matching --ignore-pot-creation-date

# Compile catalogs into the .mo files shipped with the package
i18n-compile:
    {{_nice}} uv run --no-sync pybabel compile -d {{_locale}} -D tcord --statistics

# Fail when compiled catalogs are out of date
i18n-check: i18n-compile
    git diff --exit-code -- {{_locale}}

# Everything CI checks
check: lint deny test-rust test-py i18n-check

# Create a new pkg
[arg('name', pattern='^[a-zA-Z0-9_-]+$')]
[arg('what', pattern='glue|lib')]
new what name:
    mkdir -p ./pkgs/{{name}}
    cd ./pkgs/{{name}} && {{
        if what == "glue" {
            "uv run maturin init --bindings pyo3"
        } else {
            "uv init --lib ."
        }
    }}
