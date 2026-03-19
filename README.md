# smartmeter_faker

`modbus_bridge.py` reads Home Assistant settings from environment variables first, with YAML as an optional fallback.

1. For Docker or Compose, copy [.env.example](/Users/fredriklilja/Development/smartmeter_faker/.env.example) to `.env` and set the `HA_*` variables.
2. For local file-based config, copy [homeassistant.yaml.example](/Users/fredriklilja/Development/smartmeter_faker/homeassistant.yaml.example) to `homeassistant.yaml`.
3. Start the bridge with `python3 modbus_bridge.py` or pass `--config /path/to/file.yaml`.

Environment variables take precedence over YAML. `HA_TOKEN_FILE` is also supported for Docker/Kubernetes secret mounts.

## GitHub Actions

[`python-build.yml`](/Users/fredriklilja/Development/smartmeter_faker/.github/workflows/python-build.yml) installs the pinned dependencies from [`requirements.txt`](/Users/fredriklilja/Development/smartmeter_faker/requirements.txt) and verifies that `modbus_bridge.py` compiles on every push to `main` and on pull requests.

[`docker-build.yml`](/Users/fredriklilja/Development/smartmeter_faker/.github/workflows/docker-build.yml) builds a multi-architecture Docker image for `linux/amd64`, `linux/arm64`, `linux/arm/v7`, and `linux/arm/v6`. On pushes to `main`, including merged pull requests, it publishes to both `ghcr.io/<owner>/smartmeter-faker` and Docker Hub as `<DOCKERHUB_USERNAME>/smartmeter-faker`. On `v*` tags, it also publishes to Docker Hub. On pull requests, it only validates that the multi-arch build succeeds. Manual runs from the Actions tab can publish to GHCR, Docker Hub, or both by selecting the workflow inputs.

Set these GitHub repository secrets for Docker Hub publishing:

1. `DOCKERHUB_USERNAME`
2. `DOCKERHUB_TOKEN`

For a manual Docker Hub publish in GitHub Actions, open `Docker Build`, click `Run workflow`, set `publish_to_dockerhub` to `true`, and choose the `image_tag` you want to publish.

## Docker

Build the container locally with `docker build -t smartmeter-faker .`.

The image includes a Docker `HEALTHCHECK` that reports unhealthy if the bridge has not completed a successful Home Assistant refresh within the configured age window. The application also logs its version on startup, and `python3 modbus_bridge.py --version` prints the current version string.

Runtime logs are emitted as JSON and include structured events for Home Assistant poll success/failure, Modbus reads, and server lifecycle. Home Assistant polling now uses exponential backoff after failures, capped by `--max-backoff`.

Run it directly with environment variables:

```sh
docker run --rm \
  -p 5020:5020 \
  --env-file .env \
  smartmeter-faker:latest
```

## Docker Compose

Use [`compose.yaml`](/Users/fredriklilja/Development/smartmeter_faker/compose.yaml) together with a local `.env` file:

```sh
cp .env.example .env
docker compose up --build
```
