# Developer quickstart

```
python tools/dev.py setup            # .venv with the app, test and lint packages
python tools/dev.py test --changed   # the tests for what you changed (inner loop)
python tools/dev.py check            # what CI checks: lint, tests, shared UI, docs
python tools/dev.py run              # the Windows app from source
```

- The loop, recipes for common changes and the code conventions: [developer guide](https://github.com/minhajuddinm/vox/blob/main/documentation/16-developer-guide.md).
- Where everything is: [AGENTS.md](https://github.com/minhajuddinm/vox/blob/main/AGENTS.md) and the [repository tree](https://github.com/minhajuddinm/vox/blob/main/documentation/03-repo-tree.md).
- How it fits together: [architecture](https://github.com/minhajuddinm/vox/blob/main/documentation/02-architecture.md). Why it is built this way: [decisions](https://github.com/minhajuddinm/vox/blob/main/documentation/decisions/README.md).
- How to contribute: [CONTRIBUTING](https://github.com/minhajuddinm/vox/blob/main/CONTRIBUTING.md). Security reports: [SECURITY](https://github.com/minhajuddinm/vox/blob/main/SECURITY.md).
