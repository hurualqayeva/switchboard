# ADR-0002: vLLM for the self-hosted inference tier

Date: 2026-10-30
Status: accepted

## Context

Options for serving an open-weight model on one A10G: vLLM, Hugging Face TGI, Ollama,
llama.cpp server, SGLang.

## Decision

vLLM with an AWQ-quantised base model and `--enable-lora` for multi-adapter serving.
llama.cpp server is used locally on CPU behind the same OpenAI-compatible adapter.

## Consequences

- vLLM has the most mature multi-LoRA serving, continuous batching, prefix caching and a
  Prometheus metrics endpoint that KEDA can scale on.
- It is the de facto answer in senior interviews, so the experience transfers.
- Ollama and llama.cpp are simpler but do not multiplex LoRA adapters or expose queue
  depth metrics suitable for autoscaling.
- SGLang is competitive on throughput; revisit if benchmarks warrant a comparison section.
- Cost: vLLM images are large (model weights baked in); cold start must be measured and
  may need an EBS snapshot. Tracked as a phase 5 ADR.
