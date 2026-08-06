# 📊 MindCache Benchmark & System Evaluation

MindCache was evaluated on the **BEAM QA Benchmark**—a rigorous long-term memory benchmark designed to test retrieval performance across multi-session conversation histories at 1M and 10M token context windows (300 manually graded questions across 15 conversations).

---

## 📈 Benchmark Overview

| Metric | Result |
| :--- | ---: |
| **Benchmark** | BEAM QA |
| **Evaluated Questions** | 300 |
| **Conversations** | 15 |
| **Context Windows** | 1M / 10M |
| **Avg. Retrieval Latency** | **1.08 s** |
| **Overall Performance** | **Best overall among evaluated systems** |

---

## 🥊 System Comparison

| System | BEAM Benchmark Summary | Representative Follow-up Evaluation | Memory & Retrieval Architecture |
| :--- | :--- | :--- | :--- |
| **MindCache** | 🥇 **Best overall performance*** | Outperformed Mem0 across all manually analyzed conversations | Living topic hierarchy, decision tracking & incremental summaries |
| **Mem0** | 🥈 **Competitive baseline** | Lower rubric scores and fewer passing answers across runs | Flat Memory Store |

*\* Based on our evaluation of the BEAM benchmark (300 questions across 15 conversations). Full methodology and category breakdowns are described in the accompanying [design article](https://medium.com/@faisaliitian/building-mindcache-designing-an-agentic-memory-system-for-long-term-ai-7359e0cf6e2a?sharedUserId=faisaliitian).*

---

## 🔬 Representative Follow-up Evaluation

To better understand the impact of the final architectural refinements, we manually evaluated representative BEAM conversations after completing the final retrieval architecture (hierarchical summaries, decision-anchor retrieval, retrieval budgeting, and hierarchical path indexing).

Each conversation was evaluated using **two complementary metrics**:

- **Strict Pass Rate** — A question was counted as a pass only if **all required rubric items were satisfied**. A strong answer missing a single required item was counted as a failure.
- **Rubric Coverage** — The percentage of all rubric items satisfied across the evaluation. This captures partial correctness even when a question does not meet the strict pass threshold.

| Conversation | Mem0 | MindCache | Improvement |
| :--- | :--- | :--- | :--- |
| **Conversation 1** | 45% pass (9/20) · 49.1% rubric coverage | **60% pass (12/20) · 69.545% rubric coverage** | +15 pp pass rate · +20.4 pp rubric coverage |
| **Conversation 2** | 45% pass (9/20) · 50.2% rubric coverage | **65% pass (13/20) · 61.2% rubric coverage** | +20 pp pass rate · +11.0 pp rubric coverage |

> **Observation:** Across both evaluated conversations, MindCache consistently outperformed Mem0 on both strict pass rate and rubric coverage. Conversation 1 reached a 60% pass rate (12/20) with 69.5% rubric coverage, while Conversation 2 achieved a 65% pass rate (13/20) with 61.2% rubric coverage, demonstrating MindCache's superior retrieval and structured contextual reasoning.

---

## 🎯 Performance by Task Category

| Task Category | Winner | Score (MindCache vs Mem0) | Details |
| :--- | :---: | :---: | :--- |
| **Instruction Following** | 🥇 **MindCache** | **1.00 vs 0.50** | Adheres strictly to context directives and retrieval constraints. |
| **Summarization** | 🥇 **MindCache** | **0.75 vs 0.46** | Leverages bottom-up hierarchical summaries for broad queries. |
| **Contradiction Resolution** | 🥇 **MindCache** | **0.56 vs 0.38** | Effectively resolves evolving choices and updated facts. |
| **Multi-Session Reasoning** | 🥇 **MindCache** | **0.92 vs 0.83** | Excels at connecting evidence across separate session histories. |
| **Knowledge Update** | 🤝 **Tie** | **0.50 vs 0.50** | Both systems effectively manage fact evolution over multi-turn interactions. |
| **Information Extraction** | 🥇 **Mem0** | **0.40 vs 0.45** | MindCache remains conservative to refrain from hallucinating specifics when context is ambiguous. |
| **Temporal Reasoning** | 🤝 **Tie** | **0.875 vs 0.875** | Both systems accurately reconstruct multi-month timelines and event sequences. |
| **Preference Following** | 🥇 **Mem0** | **0.79 vs 0.84** | Mem0 maintained a slight edge in retrieving direct raw user preference statements. |

---

## 🏗️ Architectural Findings Evidence

During development, five core architectural decisions were evaluated and retained:

| Finding | Evaluation Evidence | Evidence Level |
| :--- | :--- | :--- |
| **Hierarchical summaries** | Typically 4–6 activations per conversation; consistently improved retrieval quality | 📊 **Quantitatively Supported** |
| **Decision anchors** | Manual retrieval analysis across development | 🔍 **Observed in Development** |
| **Hierarchical path indexing** | Manual inspection of retrieved evidence across representative queries | 🔍 **Observed in Development** |
| **Four memory types** | Architectural schema refinement | 🏗️ **Design Rationale** |
| **Memory type quotas** | Iterative architectural refinement | 🏗️ **Design Rationale** |

---

## ⚡ Strengths & Remaining Failure Modes

### Key Strengths
- **Multi-session synthesis**: Seamlessly bridges facts across months of conversation history.
- **Chronological tracking**: Accurately tracks sequence of events and evolving preferences.
- **Broad topic coverage**: Hierarchical summaries answer high-level overview questions effectively.

### ⚠️ Remaining Failure Modes
- **Fine-grained evidence loss**: Compressing long subtrees into high-level summaries can occasionally drop specific minor details required by exact-match test rubrics.
- **Ambiguity resolution**: Cautious retrieval logic sometimes opts for uncertainty/abstention when multiple candidate memories overlap, costing points on strict exact-match benchmarks.
