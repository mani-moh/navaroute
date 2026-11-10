# Contributing to PattN

Contributions to upstream sources, parsers, healthcheck improvements, and discovery heuristics are welcome.

## Development Setup

PattN uses Python `>= 3.10` with zero mandatory third-party dependencies in the core pipeline.

```bash
# Clone the repository
git clone https://github.com/mani-moh/pattn.git
cd pattn

# Optional: configure local environment variables (git-ignored)
cp .env.example .env

# Run byte-compilation and full unit/integration test suite
python -m compileall -q src scripts tests
python -m unittest discover -s tests -v
```

## Adding New Public Sources

To add a curated public proxy source, edit `sources.json`:
1. Provide a unique `id`, descriptive `name`, and public `url`.
2. Ensure the URL contains no personal tokens, credentials, or non-public endpoints.
3. Run `python -m unittest tests.test_core -v` to validate source loading and schema checks.

## Security Rules

- **Never commit** `.env`, `local_subs/`, or API keys (`NVIDIA_API_KEY`).
- All PRs must pass `.github/workflows/ci.yml` (`compileall`, full `unittest` suite, and static secret scan).
