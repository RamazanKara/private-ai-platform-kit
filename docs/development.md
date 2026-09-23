# Developer workflow

Use this guide to change the services, SDK, automation, or documentation from a local
checkout. The [repository map](repository-map.md) explains where each kind of change
belongs. To deploy the platform, follow the [local quickstart](quickstart.md).

## Prerequisites

Run the commands below from the repository root in Linux or WSL, using Git, Bash,
Make, and Python 3.12 or newer with `venv` support. CI and runtime images use Python
3.14; use that version when reproducing a CI-only failure. The separately distributed
Python SDK supports Python 3.11 and newer.

Service tests use fake runtimes and temporary data. They do not need Docker,
Kubernetes, a GPU, or downloaded model weights. Initial environment setup installs
hash-pinned packages and needs access to the package index, or a populated local
package cache.

## First development check

```bash
make test
make quality
```

`make test` runs repository tooling tests, gateway tests, first-party SDK tests, and
RAG tests. The service test scripts create their own environments under
`src/inference-gateway/.venv` and `src/rag-service/.venv`.

`make quality` installs Ruff and mypy into `.venv-quality`. It lints the repository's
Python code, checks formatting under `src/`, and type-checks each service separately.
You do not need to activate any of these environments.

For a documentation-only change:

```bash
make docs-install
make repo-hygiene
make docs-build
```

The build writes the site to `site/`. Use `make docs-serve` to preview it locally.

## Choose checks for the change

| Change | Focused checks | Generated files to review |
| --- | --- | --- |
| Gateway behavior | `make test-gateway`, `make quality` | API or configuration contracts when the public surface changes |
| RAG behavior | `make test-rag`, `make rag-eval-check`, `make quality` | API or configuration contracts when the public surface changes |
| First-party SDK | `make test-gateway` includes its suite; see the focused command below | Package version for a release |
| Repository automation | `make test-scripts`, `make repo-hygiene`, `make quality` | Reports only when intentionally updating sample formats |
| Helm charts or cluster values | `make chart-docs`, `make config-contract`, `make production-check` | Chart value tables and configuration contracts |
| Documentation or runbooks | `make repo-hygiene`, `make docs-build` | No generated site files are committed |
| Runtime dependencies or Dockerfiles | `make dependency-lock-check`, `make image-scan`, `make repo-security-scan` | Hashed lockfiles and image scan evidence |

After focused checks, run `make validate` for the default repository gate. It also
requires Helm and uses available optional tools for schema, policy, and security
checks. CI runs `make validate-full`, which requires the strict toolchain, then
`make eval-local` and `make coverage`. The documentation site has its own strict
build workflow.

See [getting started](getting-started.md) for toolchain profiles and
[evidence and validation](proof.md) for release evidence requirements.

## Run a focused test

After the initial `make test`, use the relevant service's Python interpreter:

```bash
src/inference-gateway/.venv/bin/python -m pytest -q \
  src/inference-gateway/tests/test_gateway_auth_policy.py

src/rag-service/.venv/bin/python -m pytest -q \
  src/rag-service/tests/test_rag_service.py

PYTHONPATH=sdk/python src/inference-gateway/.venv/bin/python -m pytest -q \
  sdk/python/tests
```

The two services both expose a package named `app`. Keep their tests in separate
Python processes; collecting both service directories in one pytest invocation can
import the wrong package. `make test` handles this separation.

Repository tooling tests need only Python and Git:

```bash
make test-scripts
```

These tests live in `scripts/tests/` and use temporary Git repositories. Add tests
there for reusable tooling behavior; use the service suites for HTTP behavior.
Gateway test helpers live in `src/inference-gateway/tests/gateway_support.py`.
Use explicit `Settings`, fake backends, and temporary paths to keep tests independent
of a running cluster and local credentials.

## Update contracts at their source

The generated files capture reviewed interfaces. Edit the implementation or chart
values first, regenerate the relevant output, then review the diff:

```bash
make api-contract-update
make config-contract-update
make chart-docs-update
make dashboard-update
```

Run only the generators relevant to the change. Their check targets are
`make api-contract`, `make config-contract`, `make chart-docs`, and
`make dashboard-check`. Do not fix a failed check by hand-editing the snapshot.

Keep runtime requirements separate from test requirements and regenerate hashed
locks when changing pins. The runtime images install `requirements.lock`; service
tests install `requirements-dev.lock`. The root tooling requirements have their
own locks. See the [repository map](repository-map.md#sources-and-generated-files)
for the full list of generated surfaces.

## Write and preview documentation

Put task instructions in `docs/`, operator procedures in `runbooks/`, and code
navigation beside the implementation. Explain prerequisites, commands, expected
results, and recovery steps. Link to an existing contract or runbook instead of
copying a configuration table into another page.

Add new site pages to `mkdocs.yml` and link them from the relevant documentation
index. Site pages can use relative links to other pages under `docs/`; use repository
URLs when linking to source files outside the site.

Edit runbooks in the root `runbooks/` directory. `scripts/docs-build.sh` mirrors
them into `docs/runbooks/` for a build and removes the mirror afterward. Restart
`make docs-serve` after editing a root runbook so it copies the updated source.
The MkDocs hook keeps each mirrored page's edit link pointed at its source.

`make repo-hygiene` checks tracked and new, unignored Markdown files, including
service READMEs. It checks inline local links and images for file existence and
recognizes Make target references in code. It does not validate remote URLs,
reference-style Markdown links, or heading anchors; preview the affected pages too.

## Common development problems

| Symptom | Next step |
| --- | --- |
| `python3 -m venv` fails | Install the matching Python venv support for your Linux distribution, then rerun the target. |
| Tests import the wrong `app` module | Run the gateway and RAG suites in separate interpreter processes. |
| `mkdocs not found` | Run `make docs-install`, then `make docs-build`. |
| A generated contract check fails | Review the source change, run the corresponding update target, and inspect its diff. |
| `make validate-full` reports missing tools | Run `make toolchain-doctor TOOLCHAIN_PROFILE=strict` and follow the [validation toolchain runbook](https://github.com/RamazanKara/private-ai-platform-kit/blob/main/runbooks/validation-toolchain.md). |

`make clean` removes generated reports, site output, caches, and service test
environments. Save any evidence you need before using it. `make clean-all` also
removes downloaded tools and the documentation and quality environments.
