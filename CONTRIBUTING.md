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
2. Ensure `is_private` is `false` and the URL contains no personal tokens, private Cloudflare Worker endpoints, or credentials.
3. Run `python -m unittest tests.test_core -v` to validate schema and privacy checks.

## Privacy & Security Rules

- **Never commit** `.env`, `sources.private.json`, `private_subs/`, API keys (`NVIDIA_API_KEY`), or Gist tokens.
- Any private node must carry `is_private=True` and be emitted strictly inside `private_subs/` or private Secret Gists.
- All PRs must pass `.github/workflows/ci.yml` (`compileall`, full `unittest` suite, and static secret scan).
