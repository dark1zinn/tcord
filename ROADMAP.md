# Roadmap spec

Only add or remove from this file after a previous discussion that involves a maintainer of the project<br>
When addressing any of those make it explicit in the PR description

## Main goals (as of now)

- [ ] Setup docker-compose with [Spacebar](https://github.com/spacebarchat/spacebarchat) [(Fosscord)](https://github.com/ctrlkohl/fosscord) container for deveelopment testing
- [ ] TUI in python (no callback bindings)
- [ ] QR code & oAuth2 Authentication (website and QR CODE)
- [ ] TUI callbacks
- [ ] Sending/receiving messages in chats/DMs
- [ ] Server and chat browsing

## Upcoming concerns

- [ ] Voice calls support (Rust glue to deal with this)

### Deferred for later implementation/discussion/planning

> If you have any idea/suggestion please open an issue or reach out in [our Discord server](https://discord.gg/gpQTPTSHgZ) for discussion

- [ ] accessibility implementations (screen readers, coloring, reduced motion, font sizing/style, etc)
- [ ] config file implementation (toml)
- [ ] notifications support
- [ ] terminal image rendering support (probably kitty protocol)
- [ ] nix packaging with options
- [ ] QoL optional widgets (clock, online time&status, activity picker, etc, common everyday use)
- [ ] complete CLI implementation
- [ ] vencord plugins compatibility (probably better to offer an way to make plugins/extensions then port desired ones from vencord)
- [ ] aarch/arm binaries (why not?)
