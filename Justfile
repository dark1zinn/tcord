
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
