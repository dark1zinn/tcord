# TCORD

That one TUI client you love!<br>
tcord (*ticord*, `/ˈtiː kɔːrd/`) is aimed to be simple and don't do much, for now<br>
the primary goal it has is to get the basics right, giving you the features of daily use<br>
then don't expect much from it other than the default experience hidden in a cute UI

## Wanna contribute?

Issues and PRs are welcome!<br>
Also join our [discord server](https://discord.gg/gpQTPTSHgZ) to discuss about it!<br>
Just make sure to go through the existent issues/prs to avoid duplicates

### Development guide

It is **HIGHLY** recommended that you use an Nix environment + [`direnv`](https://github.com/direnv/direnv)
this makes light-years easier to boot up the needed dependencies including any quirks that might be necessary<br>
If you do have Nix and direnv, just run `direnv allow` at the project root and wait for it to build the environment,
it may be slow on the first time

> ![WARN]
> Please pay close attention on how the project is structured/organized and follow it

On what this project depends to build:
- Langs:
  - Python `Comes with uv in the generated venv`
  - Rust `Comes with Cargo`
- Tools:
  - [Just](https://just.systems/man/en/)
  - [uv](https://docs.astral.sh/uv/getting-started/)
  - [Cargo](https://doc.rust-lang.org/cargo/getting-started/installation.html)
  - gcc `Search for an installation guide for your system of gcc 15.2.0`
  - ccache `Optional but nice to have for faster recompiles`
- Packages:
  - [maturin](https://pypi.org/project/maturin/)
  - [PyO3](https://crates.io/crates/pyo3)
  - [nuitka](https://pypi.org/project/Nuitka/)
  - [click](https://pypi.org/project/click/)
  - [textual](https://pypi.org/project/textual/)
  - `other...`

Then check the `Justfile`s around for a good insight on what to do next<br>
Or `just -l` to get a list of available commands and their respective documentation<br>
> Run `just b` to compile the binary then use `just r -b` to run it
> more information about this part will be added soon, for now this should be enough for you to start

#### Why there is Rust being used you ask?

Glad you asked!<br>
Well, first of all, the majority of this app, as of now, is intended to be in Python<br>
- `Then why don't you keep it 100% Python?`<br>
I could, but for some features/capabilities it's a good option to actually
hand off the heavy work, voicechat handling for example, out of the way so the Python code
is free to manage the app state and ui without the concern of being blocked<br>
- `But why Rust specifically?`<br>
I guess we all know Rust is memory-safe and a pain in the ass until the code is right, then
I (dark1zin) can benefit from that on a way where the compiler will prevent me from shooting myself on the foot<br>
But the actual reason, despite my lack of intelligence, is that it already have very good and stable implementations
to generate the glue code through `maturin` and `PyO3` along with a wide range of *crates* to benefit from, such as `cpal`for audio I/O


## Free for everyone and open-source

Licenced under [MIT](./LICENSE)
