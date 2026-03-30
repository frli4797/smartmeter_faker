# smartmeter_bridge

[![Open your Home Assistant instance and show the add-on store with this repository URL pre-filled.](https://my.home-assistant.io/badges/supervisor_store.svg)](https://my.home-assistant.io/redirect/supervisor_store/?repository_url=https%3A%2F%2Fgithub.com%2Ffrli4797%2Fsmartmeter_bridge)

This repository is now structured as a Home Assistant add-on repository. The add-on itself lives in [smartmeter_bridge](smartmeter_bridge) and exposes Home Assistant sensor data as a fake Modbus TCP EM420-style meter.

For add-on usage, see [DOCS.md](smartmeter_bridge/DOCS.md). The repository manifest is [repository.yaml](repository.yaml).

`modbus_bridge.py` still supports standalone usage and reads Home Assistant settings from environment variables first, with YAML as an optional fallback.

1. For Docker or Compose, copy `.env.example` to `.env` and set the `HA_*` variables.
2. For local file-based config, copy [homeassistant.yaml.example](smartmeter_bridge/homeassistant.yaml.example) to `homeassistant.yaml`.
3. Start the bridge with `python3 smartmeter_bridge/modbus_bridge.py` or pass `--config /path/to/file.yaml`.

Environment variables take precedence over YAML. `HA_TOKEN_FILE` is also supported for Docker/Kubernetes secret mounts.

## GitHub Actions

[`python-build.yml`](.github/workflows/python-build.yml) installs the pinned dependencies from [`requirements.txt`](smartmeter_bridge/requirements.txt) and verifies that [`modbus_bridge.py`](smartmeter_bridge/modbus_bridge.py) compiles on every push to `main` and on pull requests.

[`docker-build.yml`](.github/workflows/docker-build.yml) builds a multi-architecture Docker image for `linux/amd64`, `linux/arm64`, `linux/arm/v7`, and `linux/arm/v6`. On pushes to `main`, including merged pull requests, it publishes to both `ghcr.io/<owner>/smartmeter-modbus-bridge` and Docker Hub as `<DOCKERHUB_USERNAME>/smartmeter-modbus-bridge` with the `edge` tag. On `v*` tags, it publishes release images with the `latest` tag. On same-repository pull requests, it also publishes `pr-<number>` images to both registries. Manual runs from the Actions tab can publish to GHCR, Docker Hub, or both by selecting the workflow inputs.

GitHub Actions intentionally uses [Dockerfile.standalone](smartmeter_bridge/Dockerfile.standalone) instead of the add-on [Dockerfile](smartmeter_bridge/Dockerfile). The add-on Dockerfile depends on Home Assistant's `BUILD_FROM` mechanism, which is provided by the Supervisor/add-on builder, while the standalone Dockerfile is a regular multi-arch container build for GHCR and Docker Hub.

Set these GitHub repository secrets for Docker Hub publishing:

1. `DOCKERHUB_USERNAME`
2. `DOCKERHUB_TOKEN`

For a manual Docker Hub publish in GitHub Actions, open `Docker Build`, click `Run workflow`, set `publish_to_dockerhub` to `true`, and choose the `image_tag` you want to publish.

## Home Assistant Add-on

This repository root is now a proper add-on repository. Add it to Home Assistant as a custom add-on repository, or copy the whole repository into your Home Assistant add-ons directory.

The add-on manifest is in [config.yaml](smartmeter_bridge/config.yaml), the build settings are in [build.yaml](smartmeter_bridge/build.yaml), and startup is handled by [run.sh](smartmeter_bridge/run.sh).

## Docker

Build the standalone container locally with `docker build -f smartmeter_bridge/Dockerfile.standalone -t smartmeter-modbus-bridge ./smartmeter_bridge`.

The image includes a Docker `HEALTHCHECK` that reports unhealthy if the bridge has not completed a successful Home Assistant refresh within the configured age window. The application also logs its version on startup, and `python3 smartmeter_bridge/modbus_bridge.py --version` prints the current version string.

Use [Dockerfile](smartmeter_bridge/Dockerfile) only for Home Assistant add-on builds. Use [Dockerfile.standalone](smartmeter_bridge/Dockerfile.standalone) for local Docker builds and registry publishing.

Runtime logs are emitted as JSON and include structured events for Home Assistant poll success/failure, Modbus reads, and server lifecycle. Home Assistant polling now uses exponential backoff after failures, capped by `--max-backoff`.

Run it directly with environment variables:

```sh
docker run --rm \
  -p 5020:5020 \
  --env-file .env \
  frli4797/smartmeter-modbus-bridge:latest
```

## Docker Compose

Use [`compose.yaml`](compose.yaml) together with a local `.env` file:

```sh
cp .env.example .env
docker compose up -d
```
