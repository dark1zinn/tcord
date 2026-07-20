
[arg('what', pattern='glue|lib')]
new what name:
    {{
        if what == "glue" {
            "mkdir -p ./pkgs/"+name+" && cd ./pkgs/"+name+" && uv run maturin init --bindings pyo3"
        } else if what == "lib" {
            "mkdir -p ./pkgs/"+name+" && cd ./pkgs/"+name+" && uv init --lib ."
        } else {
            "echo Uncaught!"
        }
    }}
