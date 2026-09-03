# AI-Trader architecture

Real implementation map. If this disagrees with the code, the code wins.

Related: [PROJECT_STATUS.md](PROJECT_STATUS.md) · [DEVELOPMENT.md](DEVELOPMENT.md) ·
[DEPLOYMENT.md](DEPLOYMENT.md)

Live trading is not part of this system.

---

## The one-sentence version

Deterministic Python decides everything that touches money; the LLM is given one
job — argue against a trade that has already been priced and sized — and its
answer can only ever prevent a trade, never cause or enlarge one.

---

## Pipeline

```text
events/          release calendar          BLSCPISource
        ↓        official value read TWICE, both reads must agree
        ↓        anything unclear → HOLD

edge/            deterministic probability  probability_from_resolved_value
        ↓        from the published number and the contract's own rule
        ↓        the LLM never computes this

markets/         order book                 PaperPredictionMarket
        ↓        real depth, real spread, partial fills

edge/            net edge                   model − market − fees − spread
        ↓        computed against the ASK, not the mid

edge/            deterministic filtering    OpportunityEngine
        ↓        cheap gates first; nothing weak reaches the model
        ↓        every rejection recorded with its reason

ai/skeptic.py    adversarial review         GrokSkeptic
        ↓        bull / bear / invalidators / PROCEED|PASS
        ↓        schema cannot express a ticker, size, price or venue
        ↓        any failure → PASS

survival/policy  Policy Guardian            downgrade-only, by assertion
        ↓        terminal, venue, source, verification, edge, liquidity,
        ↓        premium, exposure, correlated exposure, daily budgets

contracts/risk   sizing                     from the WHOLE premium
        ↓        binaries have no stop loss

markets/paper    fill                       walks the observed book

contracts/ledger accounting                 premium / max loss / max payout
        ↓
db/records       decision record            inputs, verdicts, outcome
        ↓
analytics/       calibration                Brier, buckets, skill score
        ↓
survival/engine  survival state             equity → ceilings
```

---

## Module map

### Money and time
| Path | Role |
|---|---|
| `money.py` | `Money`, `FxRate`, `convert`. Mixing currencies raises. No implicit rate. |
| `fx/provider.py` | `PinnedFxProvider` (replay, tests, operator pins), `PublicFxFeed` (ECB reference, fails closed). |
| `instruments.py` | An instrument's quote currency and tick size. |
| `clock.py` | `SystemClock` / `FrozenClock`. Nothing else may call `datetime.now`. |

### Survival — the deterministic core
| Path | Role |
|---|---|
| `survival/config.py` | Frozen thresholds and per-state ceilings. **Refuses to construct** a policy where a worse state permits more risk. |
| `survival/engine.py` | The FSM. Equity in, ceilings out. Hysteresis on recovery only. |
| `survival/latch.py` | One-way termination. File **and** database. No release method exists. |
| `survival/policy.py` | The guardian. Raises if it would ever upgrade an action. |
| `costs/ledger.py` | Cost and runway. **Never** an input to sizing. |

### Strategy
| Path | Role |
|---|---|
| `events/base.py` | `ScheduledRelease`, `ReleaseObservation`, `verify_two_reads`. |
| `events/bls.py` | CPI source; `FixtureEventSource` for offline runs. |
| `edge/probability.py` | Deterministic probability, discounted for revision risk. |
| `edge/edge.py` | `edge = p − ask − fees − spread`. |
| `edge/opportunity.py` | Gates and ranking. Limits model spend. |
| `ai/skeptic.py` | The analyst. Sanitises external text; strict schema; failures → PASS. |

### Markets and accounting
| Path | Role |
|---|---|
| `markets/base.py` | `Contract`, `OrderBook`, `PredictionMarketAdapter`. |
| `markets/fees.py` | `roundup_to_cent(m·C·P·(1−P))`. Fees are data. |
| `markets/paper.py` | Depth-aware paper fills. `live` is False with no path to True. |
| `markets/cpi_contracts.py` | Builds the contract ladder from the calendar. |
| `contracts/ledger.py` | Binary accounting. Separate from spot **on purpose**. |
| `contracts/risk.py` | Sizes from total premium; intersects static and survival limits. |
| `paper/*` | The original spot desk: currency-aware ledger, gap-aware fills. |
| `risk/engine.py` | Spot sizing: risk budget ∩ concentration ∩ cash. |

### Orchestration and persistence
| Path | Role |
|---|---|
| `agent/cycle.py` | One full cycle, end to end. |
| `agent/runtime.py` | Durable state. Rebuilds from the database on every start. |
| `db/schema_agent.sql` | The audit trail. |
| `db/records.py` | Reads and writes it, under one lock. |
| `analytics/*` | Calibration and performance, from persisted data only. |
| `replay/recorder.py` | Tapes of **inputs**. Replay has no HTTP client. |
| `benchmark/event_benchmark.py` | Baselines, split by time. |

### Web
| Path | Role |
|---|---|
| `src/lib/agent-api.ts` | Typed client. No fallback data anywhere. |
| `src/lib/api-auth.server.ts` | Shared-secret gate. Unconfigured = refused. |
| `src/lib/api-schema.ts` | zod allow-lists at the boundary. |
| `src/components/*` | Home, survival meter, decisions, decision detail, performance, system. |
| `src/routes/api/*` | GET routes open, POST routes gated. |

---

## Two processes

```text
Browser ──HTTP──► Node (TanStack Start)
                     │  GET  /api/agent /api/performance /api/system /api/decisions
                     │  POST /api/agent/cycle            (token required)
                     │
                     ├─stdio JSON lines─► python3 -m ai_trader rpc      (local)
                     │
                     └─HTTPS + token────► python3 -m ai_trader http     (deployed)
                                             └─ AgentRuntime (durable)
```

Both arrows reach the same `rpc.handle` command surface, so there is one engine
and one ledger whichever transport is in use. The Python process is long-lived
and owns the session; see [DEPLOYMENT.md](DEPLOYMENT.md) for why the frontend
cannot host it.

---

## Where the safety actually lives

| Guarantee | Enforced by | Proven by |
|---|---|---|
| No live trading | `safety.py`, no env override | `test_safety_invariants.py` across Python *and* TypeScript |
| LLM cannot trade | Response schema has no execution field | `test_skeptic.py`, `test_safety_invariants.py` |
| LLM cannot reach execution | `ai/` imports no broker or ledger | AST walk in `test_safety_invariants.py` |
| Losses never raise risk | `SurvivalConfig` refuses such a config | property tests in `test_survival.py` |
| Guardian only downgrades | `_outcome` raises otherwise | 500-case fuzz |
| Terminal is permanent | File + database, no release method | restart and file-deletion tests |
| Costs never affect sizing | Guardian has no cost input | byte-identical verdict + AST walk |
| Simulator never touches the network | No network import | AST walk + monkeypatched socket |
| Mutations need a secret | `api-auth.server.ts` | route-coverage test + live check |

---

## What never happens

- No real-money order, on any path.
- No look-ahead: `_visible_only`, `seen_future`, and replay in recorded order.
- No implicit currency conversion.
- No probability produced by an LLM.
- No fabricated UI state — an unreachable engine is shown as unreachable.
