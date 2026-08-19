export const meta = {
  name: 'verdict-adjudication',
  description: 'Adversarially adjudicate the v7 verdict against Hanish code, then synthesize the build order',
  phases: [
    { title: 'Scout', detail: 'verify each verdict claim against the code' },
    { title: 'Challenge', detail: 'refute or strengthen every claim and fix shape' },
    { title: 'Plan', detail: 'synthesize the ordered build plan' },
  ],
}

const LAWS = [
  'L1 never-raise: process()/capture() never raise into the host',
  'L2 fail-closed: incomplete or damaged evidence must never become false knowledge',
  'L3 append-only: a correction is a new record, never an edit',
  'L4 domain-blind core (no domain vocabulary inside hanish/)',
  'L5 semantics live in the adapter/host layer, never the core',
  'L6 zero-dependency core',
  'L7 first-valid-terminal: the first terminal outcome wins and never changes',
  'L8 exposure: a forecast the team could see is EXPOSED, never calibration',
]

const GATES = 'G1 never-raise, G2 tail-toler, G3 once-only, G4 schema, G5 lattice, G6 scoreable, G7 replay, G8 host-2 adapter (not built), G9 ship'

const SCOUT_SCHEMA = {
  type: 'object',
  properties: {
    claims: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          verdict_claim: { type: 'string' },
          status: { type: 'string', enum: ['confirmed', 'qualified', 'refuted'] },
          evidence: { type: 'string', description: 'file:line and the actual code that proves the status' },
          nuance: { type: 'string', description: 'what the verdict exaggerated, missed, or got backwards' },
          fix_shape: { type: 'string', description: 'if real, the concrete fix and the layer it lives in' },
        },
        required: ['verdict_claim', 'status', 'evidence', 'nuance'],
      },
    },
  },
  required: ['claims'],
}

const CHALLENGE_SCHEMA = {
  type: 'object',
  properties: {
    verdicts: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          name: { type: 'string', description: 'the verdict item being judged' },
          exists: { type: 'boolean' },
          survives: { type: 'boolean', description: 'true only if the finding survives as an actionable build item' },
          note: { type: 'string', description: 'the refutation (with file:line) or the strengthening argument' },
          fix: { type: 'string', description: 'the tightened fix shape and its layer, only when it survives' },
          missed: { type: 'string', description: 'if the verdict missed something the code shows is bigger, name it' },
        },
        required: ['name', 'survives', 'note'],
      },
    },
  },
  required: ['verdicts'],
}

const PLAN_SCHEMA = {
  type: 'object',
  properties: {
    recommendations: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          title: { type: 'string' },
          effort: { type: 'string', enum: ['small', 'medium', 'large'] },
          phase: { type: 'string', enum: ['NOW', 'NEXT', 'LATER'] },
          lawOrGate: { type: 'string' },
          source: { type: 'string', description: 'where the idea came from (verdict item, wave survivor, code finding)' },
          value: { type: 'string' },
        },
        required: ['title', 'effort', 'phase', 'lawOrGate', 'source', 'value'],
      },
    },
    scaffold: { type: 'string', description: 'the build narrative, ordered, honest about what is thin' },
    nextStep: { type: 'string', description: 'the single concrete first move with file paths' },
  },
  required: ['recommendations', 'scaffold', 'nextStep'],
}

const SCOUT_HEAD = `You are a VERDICT SCOUT in a read-only verification wave for HANISH. The repo lives at G:/Hanish (NOT G:/Prospector, which is a stale copy). An external evaluator issued a v7 verdict about this repo. Your job: reopen the actual code and mark each verdict claim confirmed / qualified / refuted, with file:line evidence. Never trust the verdict; trust the file.

Repo facts: lattice time <- past <- future <- present <- adapters. Append-only JSONL ledgers (forecasts.jsonl, evidence.jsonl, outcomes.jsonl) rebuild all state on open. Laws: ${LAWS}. Gates: ${GATES}.`

const CLUSTERS = [
  {
    key: 'ci-host0',
    prompt: SCOUT_HEAD + `

CLUSTER: the CI + receipts claims. Verify against:
- G:/Hanish/.github/workflows/ci.yml (the Actions workflow)
- G:/Hanish/hanish/adapters/ci.py (CIAdapter: checks_result, finalize, observable_specs)
- G:/Hanish/.gitignore (does it exclude harness/root/?)
- G:/Hanish/README.md (does it promise the CI-first calibration-fill "runs whether the operator shows up"? Is that structurally true today?)

Verdict claims to verify:
1. "CI is not really Host 0 yet. The workflow does checkout/setup/install/lint/pytest/demo and nothing else. There is NO Hanish capture/finalize step wiring the real GitHub Actions result into a persistent Hanish evidence stream. Hanish is tested by CI but CI does not genuinely host Hanish."
2. "The CI-first promise - calibration buckets fill automatically because it runs unattended - is not happening yet."
3. "The first self-forecast's evidence is unpublished: harness/root/ is gitignored, so the digest's HIT claim can be read but the forecast/evidence/seal/outcome records cannot be independently inspected. The verdict proposes immutable experiments/receipts/<run>/ with forecast.json, evidence.jsonl, outcome.json, manifest.json, README.md."
4. NOTE the matrix: the workflow runs 4 python versions, each a separate job. Record what you see.`,
  },
  {
    key: 'economic',
    prompt: SCOUT_HEAD + `

CLUSTER 2: the economic-architecture claims. Verify against:
- G:/Hanish/hanish/past/ledger.py:139 append_observation_once
- G:/Hanish/hanish/present/substrate.py:184 capture, :225 process, :242 _resolve_from_evidence, :295 _sweep_expired
- G:/Hanish/harness/HARNESS.md and harness/digest.md (search for any recorded P6 / PRESENT-flight / scan open thread)

Verdict claims to verify:
1. "capture() ends at append_observation_once() which acquires the cross-process lock, scans the ENTIRE evidence ledger for the dedup key, then does an fsync write. A new observation costs O(N) under the lock." IMPORTANT: substrate.capture checks event.dedup_key in self._seen (an in-memory set) FIRST. Trace precisely which path is O(1) (warm _seen) and which is O(N) under the lock (cold process, first sight of an envelope). A fresh process ingesting N new envelopes pays O(N) per append = O(N^2) cold-ingest. Confirm or qualify.
2. "Resolution is indexed on forecasts but still scans observations: every process() iterates for obs in self._observations from the beginning. process1 reads 1-100, process2 reads 1-101, process3 reads 1-102. The target is a cursor so process2 reads only 101 onward - bounded O(new evidence + watchers)."
3. The permanent invariant the verdict proposes: "host capture latency must never equal ledger history size."
4. Whether the harness's own docs already carry an open PRESENT/P6 thread on this (search harness/). Name the flight.`,
  },
  {
    key: 'identity-exposure',
    prompt: SCOUT_HEAD + `

CLUSTER 3: source-sequencing, exposure, and world_ref claims. Verify against:
- G:/Hanish/hanish/adapters/ci.py (self._seq dict, _next_seq, finalize, world_ref)
- G:/Hanish/hanish/present/substrate.py (calibration_eligible on outcomes; world_ref_capability)
- G:/Hanish/harness/orchestrator.md:21 (a forecast the team can see is EXPOSED, not calibration)
- G:/Hanish/harness/root/forecasts.jsonl + outcomes.jsonl (the real f_c57a1c3bc61f record)
- G:/Hanish/.github/workflows/ci.yml (4-python matrix = 4 fresh adapter processes for the same git subject)

Verdict claims to verify:
1. "source_seq lives in adapter process memory (self._seq dict), so a restart resets it to 1. Completeness assumes source_seq 1..N is ONE coherent stream. A restarted adapter can emit a final_source_seq that only reflects events since restart, so an incomplete stream can look complete - false MISS." IMPORTANT: the matrix means 4 separate jobs already emit required_checks for the SAME git commit subject today, each starting source_seq at 1 with final_source_seq=1 per seal. Is that a live defect NOW? Trace what the substrate sees (4 observations same subject, source_seq all 1, event_id differs by run_id, seals final_source_seq=1 each) and what _stream_complete concludes.
2. "The first self-forecast is probably not BLIND. The director forecast 'flight-past lands G1-G5,G7' then directed workers whose assignment was exactly land G1-G5,G7 - a causal loop. Useful operational outcome, not a clean calibration sample. Reclassify EXPOSED unless workers were blinded." The record says exposure BLIND + calibration_eligible true. Assess whether the classification is defensible and what structural exposure enforcement looks like.
3. "world_ref hashes commit|workflow|lockfile into world:<16-hex> and stores ONLY the hash, but declares REPLAYABLE. The digest is not reversible - workflow_sha and lockfile_sha are lost. So it is IDENTIFIABLE, not REPLAYABLE, unless the three coordinates are persisted. Fix: a WorldManifest (immutable, content-addressed) and the hash becomes a hash OF the manifest."`,
  },
  {
    key: 'arch-plan',
    prompt: SCOUT_HEAD + `

CLUSTER 4: the v7-stage table, the triad, and plan-ordering. Verify against:
- package layout: hanish/past, hanish/future, hanish/present, hanish/adapters
- G:/Hanish/README.md (the deliberately-absent list)
- G:/Hanish/harness/waves/memory-scan.md (the earlier memory wave: 8 surviving scaffold opportunities)
- G:/Hanish/harness/orchestrator.md, harness/digest.md

Verdict claims to verify:
1. The v7 stage table: V0.0 PASS; V0.1 STRONG-INCOMPLETE; V0.2 IN PROGRESS (guardian started); V0.3-V0.6 NOT STARTED; V0.7 USD temporal correctly deferred; V0.8 intervention provenance only coarse BLIND/EXPOSED; V0.9 deferred. Confirm each row against the code.
2. "The triad is not implemented: hanish/past/future/present are eloquent NAMES, but Moneta (retrospective learned memory), Octavius (composed situational state), Prospect (prospective trajectory model) do not exist inside. There is no model generating future distributions from past+present."
3. The README's deliberately-absent list (decay, calibration buckets, tiers, drift, model calls, distributed transport, InterventionEvent, USD) is accurate.
4. Plan-composition: the verdict's 7-step order (1 wire CI as host; 2 publish receipts; 3 remove history-size dependence from capture AND processing; 4 fix sequencing across restart + Host-Omega; 5 make exposure structural; 6 world_ref manifest; 7 then V0.3 agent-predicts-before-CI) vs the memory wave's plan (NOW: seed-variance determinism gate + hash-chain root commitment; NEXT: writer-must-match-reader seam/G8 + pending-adjudication queue + forced-promotion cap + lexical first-valid gate; LATER: copy-verify migration). Do the two plans compose or conflict? Tension to resolve: wiring CI (item 1) BEFORE fixing seq authority (item 4) bakes broken multi-process seq into the first real data stream. Does that ordering hold?`,
  },
]

const CHALLENGE_PROMPT = `You are an ADVERSARIAL CHALLENGER in a verdict-adjudication wave for HANISH (G:/Hanish). A scout verified the external v7 verdict's claims against the code. Your job: REFUTE. Nothing survives unless you are forced.

First reopen the actual files yourself - the scout may have over-claimed or under-claimed. For each of the scout's verdict claims decide: does the finding survive as a real build item for HANISH? Test the fix shape against the laws and gates: never-raise, fail-closed (incomplete evidence never becomes a verdict), append-only, domain-blind core, semantics in the adapter layer, zero-dep core, first-valid-terminal wins and never changes, exposure is not calibration. Any fix that drags a dependency into the core, leaks domain vocabulary into hanish/, launders absence into MISS, violates append-only, or only duplicates an existing mechanism is dead. Cite file:line. Also note anything the verdict itself missed that the code shows is a bigger risk.

Scout claims:
`

const PLAN_PROMPT = `You are the SYNTHESIZER of a verdict-adjudication wave for HANISH (G:/Hanish). An external v7 verdict and the earlier memory-scan wave (harness/waves/memory-scan.md) have both been adversarially verified. Below are the per-cluster challenge verdicts. Produce the definitive ordered build plan.

Hard constraints: solo-dev lean. The eight laws are inviolable. The core (hanish/) stays domain-blind and zero-dep. The triad (Moneta + Octa + Prospect) is deliberately NOT next - the measurement instrument must be trustworthy first. Every recommendation names its phase, effort, law/gate, and an acceptance check (a test or gate that proves it). A recommendation that only makes the repo bigger without making a law or gate enforceable is discarded.

Surviving challenge verdicts by cluster:
`

// ---------------- execution ----------------

phase('Scout')
const results = await pipeline(
  CLUSTERS,
  async (item) => agent(item.prompt, { label: 'scout:' + item.key, phase: 'Scout', schema: SCOUT_SCHEMA }),
  async (claims, item) => agent(CHALLENGE_PROMPT + JSON.stringify(claims), { label: 'challenge:' + item.key, phase: 'Challenge', schema: CHALLENGE_SCHEMA })
)

phase('Plan')
const byCluster = results.filter(Boolean).map((r, i) => ({ cluster: CLUSTERS[i].key, verdicts: r.verdicts }))
const plan = await agent(PLAN_PROMPT + JSON.stringify(byCluster), { label: 'plan', phase: 'Plan', schema: PLAN_SCHEMA })

return { summary: 'v7 verdict adjudicated: 4 scouts, 4 challengers, 1 synthesizer', agentCount: 9, result: { byCluster, plan } }
