## ❌ Maida verdict: fail

**1 blocking check failed** | **1/1 trials passed**

### Behavior vs baseline

- New tool used: `retry_lookup`.
- Steps increased from 1 to 2 (+1).
- Tool calls increased from 1 to 2 (+1).

### Blocking checks

- ❌ **Tool calls violated the policy.** `tool_call_count` -- Observed 2 (baseline 1; allowed at most 1).

### Next steps

- Review the behavioral changes and blocking checks above.
- Inspect the full diff: `maida diff <trace-id> --baseline baseline.json`
- Open the trace locally: `maida view <trace-id>`
- If this change is intentional, comment `/maida accept` on the PR or accept locally: `maida accept <trace-id> --baseline baseline.json --reason "..."`
- Otherwise fix the agent behavior and rerun: `maida run AGENT.py --baseline baseline.json`

<details>
<summary>Passing checks, report-only metrics, and trial evidence</summary>

#### Passing checks

- ✅ **Agent execution stayed healthy.** `agent_process` -- All 1 trial completed successfully.

#### Trial evidence

| Trial | Outcome | Trace | Process exit | Behavior changes |
| ---: | --- | --- | ---: | --- |
| 1 | PASS | `<trace-id>` | 0 | 3 changes |

</details>

---
*Gated by [Maida](https://maida.ai) -- the local-first behavioral regression gate for AI agents.*


### Action merge decision

Mode: **blocking**. GitHub conclusion: **failure**.

Only a behavioral PASS with accepted configuration and successful check publication can authorize this commit.

Configuration: **unchanged**. Policy always comes from the trusted PR base. See the named check for revision and configuration hashes.

---

### Accept this intentional change

After reviewing the diff and trace, a repository maintainer can comment `/maida accept [optional reason]` on this PR.

<details>
<summary><i>Reproducibility Instructions</i></summary>

The `maida view <id>` command in this report refers to this CI job's trace store. That ID is not available on your machine until you record a local run.

From this PR checkout, record a fresh local run and rerun the same assertion inputs:

```bash
python -m pip install maida-ai==0.5.3
MAIDA_REPRO_DIR=$(mktemp -d)
git show <base-sha>:policy.yaml > "$MAIDA_REPRO_DIR/policy.yaml"
git show <base-sha>:baseline.json > "$MAIDA_REPRO_DIR/baseline.json"
maida run agent.py --baseline "$MAIDA_REPRO_DIR/baseline.json" --policy "$MAIDA_REPRO_DIR/policy.yaml"
```

Then run `maida view <local-id>` using the ID produced by your local run.

If the local behavior change is intentional, accept it explicitly and review the baseline diff:

```bash
maida diff <local-id> --baseline baseline.json
maida view <local-id>
maida accept <local-id> --baseline baseline.json --reason why\ this\ behavior\ is\ expected
git diff -- baseline.json
```
</details>


<!-- Sticky Pull Request Comment -->