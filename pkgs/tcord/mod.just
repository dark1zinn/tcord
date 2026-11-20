# Build a single-file binary (4 jobs, lowered priority, disk guarded)
build:
    ../../scripts/disk-guard.sh
    nice -n 10 env -u VIRTUAL_ENV uv run --no-sync nuitka --jobs=4 --mode=onefile --output-dir=dist --include-package=tcord --include-package=client --include-package=tcord_core --include-package-data=tcord ./src/tcord

# Run the app (`-b` runs the built binary)
[arg('bin', short, value='b')]
run bin='f' *args:
    {{
        if bin == "b" {
            "./dist/tcord.bin "+args
        } else {
            "env -u VIRTUAL_ENV uv run --no-sync tcord "+args
        }
    }}
