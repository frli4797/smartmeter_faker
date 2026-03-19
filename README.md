# smartmeter_faker

`modbus_bridge.py` now reads the Home Assistant connection settings and entity IDs from a YAML file.

1. Copy [homeassistant.yaml.example](/Users/fredriklilja/Development/smartmeter_faker/homeassistant.yaml.example) to `homeassistant.yaml`.
2. Set `homeassistant.url`, `homeassistant.token`, and the entity IDs under `homeassistant.entities`.
3. Start the bridge with `python3 modbus_bridge.py` or pass `--config /path/to/file.yaml`.

`--ha-url` and `--ha-token` still work as runtime overrides for the YAML values.

## GitHub Actions

[`python-build.yml`](/Users/fredriklilja/Development/smartmeter_faker/.github/workflows/python-build.yml) installs the pinned dependencies from [`requirements.txt`](/Users/fredriklilja/Development/smartmeter_faker/requirements.txt) and verifies that `modbus_bridge.py` compiles on every push to `main` and on pull requests.

[`docker-build.yml`](/Users/fredriklilja/Development/smartmeter_faker/.github/workflows/docker-build.yml) builds a multi-architecture Docker image for `linux/amd64`, `linux/arm64`, `linux/arm/v7`, and `linux/arm/v6`. On pushes to `main`, it publishes to `ghcr.io/<owner>/smartmeter-faker`. On `v*` tags, it publishes to Docker Hub as `<DOCKERHUB_USERNAME>/smartmeter-faker`. On pull requests, it only validates that the multi-arch build succeeds.

Set these GitHub repository secrets for Docker Hub publishing:

1. `DOCKERHUB_USERNAME`
2. `DOCKERHUB_TOKEN`

## Docker

Build the container locally with `docker build -t smartmeter-faker .`.

The image includes a Docker `HEALTHCHECK` that reports unhealthy if the bridge has not completed a successful Home Assistant refresh within the configured age window. The application also logs its version on startup, and `python3 modbus_bridge.py --version` prints the current version string.

Run it with your config mounted into the container:

```sh
docker run --rm \
  -p 5020:5020 \
  -v "$(pwd)/homeassistant.yaml:/app/homeassistant.yaml:ro" \
  smartmeter-faker:latest
```
