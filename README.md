# safecpp-reviewer

An agentic LLM system that reviews embedded C++ codebases for MISRA / AUTOSAR / cppcoreguidelines compliance, explains violations in context, proposes fixes, and validates them against ground-truth static analyzers.

## Status

Under active development. Phase 0 (infrastructure) complete. Phase 1+ in progress.

## Hardware Setup

- **GPU**: AMD Radeon RX 9070 XT (RDNA 4, gfx1200)
- **OS**: Ubuntu 24.04.4 LTS, kernel 6.17
- **ROCm**: 7.2.2
- **Inference**: Qwen2.5-Coder-7B-Instruct @ Q5_K_M, ~51.7 tok/s

## Quick Start

```bash
git clone https://github.com/<your-username>/safecpp-reviewer.git
cd safecpp-reviewer
make install
make test
```

## License

MIT
