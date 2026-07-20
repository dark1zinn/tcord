
build:
    uv run nuitka --mode=onefile --output-dir=dist ./src/tcord

[arg('bin', short, value='b')]
run bin='f' *args:
    {{
        if bin == "b" {
            "./dist/tcord.bin "+args
        } else {
            "uv run main "+args
        }
    }}
