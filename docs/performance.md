# Performance measurements

Measurements use the Flexiv release-v4.2 submodule graph and an isolated
temporary superproject on the same host. Times cover recursive submodule
initialization but not CMake configuration.

| Strategy | Cold setup | Warm setup | Persistent cache |
| --- | ---: | ---: | ---: |
| Plain recursive full clone | 81.86 s | 81.86 s | none |
| Full mirror plus reference | 81.12 s | 48.45 s | 121.89 MiB |
| Exact-commit shallow cache | 14.69 s | 1.64 s | 3.25 MiB |

The exact-commit cache reduces cold setup time by 82.1% relative to a plain
recursive clone. Warm setup is 96.6% faster than the previous mirror/reference
implementation, and the persistent cache is 97.3% smaller. Git metadata for
two initialized worktrees was 2.20 MiB, so cache plus two worktrees occupied
5.45 MiB.

The optimized implementation:

- keys caches by submodule remote URL, so repeated paths share objects;
- fetches only the gitlink commits required by the superproject;
- fetches independent caches concurrently;
- clones warm worktrees from the local cache without fetching;
- keeps the real remote URL in each resulting submodule checkout.

On 2026-09-07, fs gc --dry-run scanned nine Flexiv build roots in 9.6 seconds
and measured 104.00 GiB. With the default 30-day inactivity threshold it
identified 16.71 GiB in two stale build roots. No files were deleted.
