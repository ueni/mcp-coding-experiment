# Branch Swarm Benchmark Report

Generated: 2026-03-08T20:11:17.274964+00:00
Base ref: `HEAD`

## Leaderboard

| Rank | Strategy | Score | Quality | Primary Metric (lower is better) |
|---:|---|---:|---:|---:|
| 1 | Compile Warmup | 5 | 1/1 | 95.000 |
| 2 | Baseline | 0 | 1/1 | 100.000 |

## Compile Warmup

- Branch: `swarm/compile-warmup`
- Setup OK: `true`
- Quality: `1/1`
- Primary metric: `95.000`

```text
setup | python -m compileall -q toolchain/dev | rc=0 | 0.125s
quality | python -m py_compile toolchain/dev/server.py | rc=0 | 0.096s
benchmark | python -c "print(95.0)" | rc=0 | 0.013s | out=95.0
```

## Baseline

- Branch: `swarm/baseline`
- Setup OK: `true`
- Quality: `1/1`
- Primary metric: `100.000`

```text
quality | python -m py_compile toolchain/dev/server.py | rc=0 | 0.091s
benchmark | python -c "print(100.0)" | rc=0 | 0.013s | out=100.0
```
