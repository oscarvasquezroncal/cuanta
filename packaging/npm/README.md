# cuanta

Run the real cuanta CLI and TUI from npm:

```sh
npx cuanta --version
npm install --global cuanta
npm install --save-dev cuanta
npx cuanta --help
```

Requires Node 18 or newer and either uv on PATH or Python 3.12/3.13. No npm dependencies or lifecycle scripts run at installation. The first invocation builds a private runtime from the included wheel and hash-pinned requirements. uv may fetch managed Python 3.13; the launcher never installs uv itself. With Python alone it uses venv and pip.

Runtime cache: Windows `%LOCALAPPDATA%/cuanta/npm/<version>`, macOS `~/Library/Caches/cuanta/npm/<version>`, Linux `${XDG_CACHE_HOME:-~/.cache}/cuanta/npm/<version>`. Set `CUANTA_RUNTIME_DIR` to override the cache root; the version is appended. Set `CUANTA_LAUNCHER_DEBUG=1` for resolution diagnostics on stderr. The two newest owned runtime versions are retained. To remove runtimes, delete this cache directory; uninstall the npm package normally.

Arguments, exit codes and console input/output reach cuanta directly. Preparation progress is written to stderr in English or Spanish by locale, keeping stdout clean for JSON and pipes. SIGINT, SIGTERM and SIGHUP are forwarded to the running child. Concurrent first runs share an atomic lock; incomplete runtimes rebuild before use.

Source and maintainer documentation: https://github.com/oscarvasquezroncal/cuanta
