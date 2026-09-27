# Milestone 1: CLAP feasibility

## Result

The minimal technical chain is proven on the test Mac for the pinned upstream revision:

1. `clap-saw-demo` builds as an arm64 macOS `.clap` bundle with a documented compatibility patch limited to its pinned VSTGUI build configuration.
2. Official `clap-validator` 0.4.1 loads and scans the bundle as `org.surge-synth-team.clap-saw-demo`.
3. The repository probe loads the CLAP entry/factory, initializes the plugin, and discovers 10 parameters from `clap.params`.
4. It reads current values and saves 97 bytes through `clap.state`.
5. It changes the discovered “Unison Count” parameter from 3 to 7 through a CLAP process event.
6. It reloads the saved state and verifies that the value returns to 3.

The raw probe snapshot is committed at [`docs/evidence/pr1-clap-probe.json`](evidence/pr1-clap-probe.json). It is feasibility evidence, not the canonical state contract delivered in Milestone 2.

## Pinned inputs

| Component | Revision |
|---|---|
| `abique/clap-saw-demo` | `f33b31fff459d66ac18207ec152b323aaa9306f9` |
| `libs/clap` | `395ab2bc5abd76f613b7ac3e4292b4e315700749` |
| `libs/clap-helpers` | `2bb43c18788c689708ead6f127a2d75e772ab389` |
| `libs/readerwriterqueue` | `b18136b37edd3ee55369ce647e48474337f4b519` |
| `libs/vstgui` | `addf12b9486fd9460a3abf2cf2a2dd96c44ed807` |
| `free-audio/clap-validator` | release 0.4.1; arm64 asset SHA-256 `719a0248ea431718bb7c92c8a0b9a78afa0bb23d692cf452e80973fe8b89282d`; executable SHA-256 `39d19636050bf3cb55362426d335e3b660ae3589503e17004bd5e9b44db652ec` |

Verified local environment: macOS 26.7, arm64, Apple Clang 21.0.0, CMake 4.3.2.

## Build compatibility decision

The upstream revision does not build unmodified with Apple Clang 21. Its pinned VSTGUI omits the standard `<utility>` header needed for `std::move`, uses APIs now diagnosed as deprecated, and appends `-Werror`, converting compatibility warnings into build failures.

Milestone 1 does not alter synth or DSP source. `scripts/build_clap_feasibility.sh` applies the explicit patch at `scripts/patches/clap-saw-demo-vstgui-modern-clang.patch`, which removes only VSTGUI's `-Werror`, and uses `-include utility`. Compiler warnings remain visible.

## Reproduce

Prerequisites: macOS on Apple Silicon, Xcode command-line tools, CMake, Git, and authenticated GitHub CLI.

```bash
scripts/build_clap_feasibility.sh
scripts/run_clap_validation.sh
```

The build script clones into ignored `work/`, checks out the exact upstream commit, initializes pinned submodules, applies the compatibility patch, builds the bundle, compiles the small CLAP probe, and runs the read/change/restore proof. The validator script downloads the pinned official arm64 release, verifies its SHA-256 digest, scans the plugin, and runs the exact passing checks.

## Independent validator evidence

The exact scoped validator run passes:

- `scan-rtld-now`
- `param-default-values`
- `state-reproducibility-basic`
- `state-reproducibility-binary`
- `state-reproducibility-buffered`

This is deliberately not presented as full CLAP conformance.

## Known upstream limitations

- The plugin's `paramsFlush()` implementation is explicitly empty. Official validator test `param-set-events` therefore fails with “After calling `clap_plugin_params::flush()`, the parameter values did not change.” The repository probe uses a valid parameter event during `process()`, which succeeds.
- A broader validator run also produced crashes in descriptor-related tests. Those failures are not hidden, but diagnosing or patching upstream is outside Milestone 1.
- The plugin and validator binaries are ad-hoc signed, not Developer ID signed.
- No audio audition, deterministic rendering, GUI, canonical state schema, or human listening validation is claimed here.

## Boundary for Milestone 2

Milestone 2 may turn discovered plugin facts into a canonical machine-readable state model. It must not copy assumed names or meanings from design mockups; it must continue to query the actual plugin.
