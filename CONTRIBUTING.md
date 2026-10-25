# Contributing

Contributions are welcome through GitHub issues and pull requests.

By submitting a contribution, you agree that it may be distributed under the
project's [MIT License](LICENSE). Only submit code, data, and assets that you
have the right to contribute. Do not commit molecular datasets, private study
results, credentials, local `.env` files, or third-party executables.

Before opening a pull request, run:

```bash
conda run -n iScreeM python -m pytest backend/tests -q
cd frontend
npm ci
npm run build
```

If a change adds or updates a dependency, update
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) and retain all required
upstream notices.
