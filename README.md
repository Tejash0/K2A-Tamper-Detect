# Blockchain-Based CCTV Evidence Verification System

A surveillance evidence verification system that anchors cryptographic hashes of video footage on an Ethereum-compatible chain: Hardhat locally for development, and Polygon Amoy for the public deployment the paper cites. The system uses a **dual-hash strategy** - SHA-256 for exact byte integrity and K2A-Hash, a training-free perceptual hash, for a constant-size content fingerprint - anchored in a Solidity smart contract.

Read the [measured limits](#what-it-does-and-does-not-detect) before describing this as tamper detection. It detects one of the four tamper types evaluated.

## K2A-Hash

**K2A-Hash** is a training-free perceptual hash for video, built from:
- Uniform 8×8 block grid diagonal pixel extraction
- Bit compression against the absolute constant 128
- Temporal extension across 4 sampled frames

It produces **64 significant bits**, stored in a 256-bit `bytes32` field, so the maximum Hamming distance between any two K2A hashes is 64.

SHA-256 detects any byte-level change to the file. K2A-Hash was designed to complement it by responding to visual content rather than bytes, and it does survive re-encoding: a transcoded copy of the same footage differs by 0.58 bits on average, so a legitimately transcoded clip can still be matched back to its anchored original. What it does **not** do is detect tampering in general - see the measured table below before relying on it for anything.

### What it does and does not detect

Measured over 60 UCF-Crime clips (`experiments/results.csv`):

| Change | Mean K2A distance | Detected above the 8-bit threshold |
|---|---|---|
| Re-encoding (CRF 28) | 0.58 | No, by design |
| Frame deletion (40%, x264) | 0.58 | No |
| Frame deletion (40%, mp4v) | 0.67 | No |
| Brightness (+0.3 EQ) | 27.73 | Yes, 57 of 60 clips |
| Text overlay | 1.33 | No |

Frame deletion is reported under both encoders so the edit is separable from the
codec change. At 0.58 under x264 it is **indistinguishable from benign
re-encoding**, which also measures 0.58. Under the same conditions pHash (0.42)
and dHash (0.43) do not detect it either, so this is a property of proportional
frame sampling rather than a K2A-specific defect.

**Known limitations, stated plainly:**
- K2A responds to global intensity change. It does **not** currently detect localised edits, frame deletion, or overlays.
- Deepfake and frame-substitution detection is **untested**. Do not rely on it for that.
- Thresholding against an absolute constant collapses entropy on dark footage: set bits average 22.35 of 64, minimum 1. Across all 1770 unrelated-video pairs the mean distance is 28.68 bits with a minimum of 2, giving a **0.8 per cent false accept rate** at the 8-bit threshold.
- A brightness attack (27.73) and an entirely unrelated video (28.68) are the same distribution, so the system reports a distance but does **not** classify tamper type.
- **Measured against baselines, K2A loses.** The discriminability index `d'` between "a tampered copy of this clip" and "a different clip entirely" is **0.10** for K2A, against **9.61** for pHash and **7.48** for dHash, computed over the same four frames. Reproduce with `ai-service/.venv/bin/python experiments/discriminability.py`. K2A's large brightness distance is decorrelation, not detection.
- The 8-bit threshold is derived from these same 60 clips, so every rate above is in-sample. There is no held-out set and no ROC study.

## Architecture

```
Camera/AI Service → Backend API → SQLite Cache ← → Blockchain (Hardhat EVM)
                                       ↑
                                  Frontend (React)
```

Four components run concurrently:

| Component | Location | Port | Purpose |
|-----------|----------|------|---------|
| Hardhat Blockchain | `blockchain/` (project root) | 8545 | Immutable evidence anchor |
| Backend API | `backend/` | 5000 | Hash ingestion, verification, SQL cache |
| Frontend | `frontend/` | 5173 | Dashboard, verifier, tamper demo |
| AI Service | `ai-service/` | 8000 | K2A-Hash computation, crime detection |

## Prerequisites

- **Node.js 22.** This is a hard constraint, not a preference: `better-sqlite3@11`
  cannot compile against Node 26 (V8 removed `Object::GetPrototype` and
  `Context::GetIsolate`), so `npm rebuild` is not a workaround. `.nvmrc` pins 22
  and `dev.sh` probes for a working Node automatically.
- **Python** 3.10+ with `pip` (the evaluation venv runs 3.14.6)
- **npm** (or pnpm/yarn)
- **tectonic** (optional, for building the paper)

## Quick Start

```bash
./dev.sh up        # chain, deploy, AI service, backend, frontend
./dev.sh status
./dev.sh logs backend      # or chain | ai | frontend
./dev.sh down
```

`dev.sh up` starts the Hardhat chain on 8545, deploys the contract, writes
`backend/.env` with the fresh address, clears the stale SQLite cache, and brings
up the AI service (8000), backend (5000) and frontend (5173).

**Start the services this way rather than by hand.** Restarting the chain gives
the contract a new address and leaves the SQLite cache holding records that no
longer exist on it; `dev.sh` re-wires both, and starting the four processes
manually does not.

Code changes are picked up live. Only a change under `contracts/` needs
`./dev.sh restart`.

## Environment Variables (Backend)

Create `backend/.env`:
```env
PORT=5000
DATABASE_PATH=./database/evidence.db
HARDHAT_NETWORK_URL=http://127.0.0.1:8545
CONTRACT_ADDRESS=<from hardhat ignition deploy output>
PRIVATE_KEY=<hardhat test account private key>
CORS_ORIGIN=http://localhost:5173
```

> **`PRIVATE_KEY` must be the account that deployed the contract.**
> `logEvidence()` is `onlyOwner`, and the owner is fixed at deployment to
> whichever account `DEPLOYER_PRIVATE_KEY` (in the root `.env`, used by
> `hardhat.config.ts`) signs with. If the two keys differ, every
> `POST /api/record` reverts, the record is marked `failed` in SQLite, and the
> reason appears only in the backend log.
>
> The owner is `immutable` and there is no transfer function, so rotating or
> losing the operator key means redeploying the contract. Prior evidence stays
> readable on the old address but no new records can be added to it.

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/record` | Ingest video hash + metadata → SQL + blockchain |
| `POST` | `/api/verify` | Two-level verification (SQL cache + blockchain) |
| `GET`  | `/api/logs`   | Paginated evidence log with status |
| `POST` | `/api/cleanup` | Drop cached records that are not on the current chain |
| `GET`  | `/api/health` | Service and chain connectivity check |

### POST /api/record
```json
{
  "videoHash": "0x<sha256>",
  "cameraId": "CAM-01",
  "timestamp": 1700000000,
  "perceptualHash": "0x<k2a-hash>",
  "eventType": "violence",
  "confidenceScore": 9200
}
```

### POST /api/verify
```json
{ "videoHash": "0x<sha256>" }
```
Returns, when the SHA-256 is found on-chain:
```json
{
  "verified": true,
  "source": "cache+blockchain",
  "k2a_hamming_distance": 0,
  "k2a_verdict": "content_authentic"
}
```
The distance is 0 on this path by construction; see [Verification Logic](#verification-logic).

## Verification Logic

1. **Level 1 (SQL cache):** Sub-second lookup of stored hash
2. **Level 2 (blockchain):** On-chain `verifyEvidence()` call
3. **K2A verdict:** Hamming distance ≤ 8 bits → `content_authentic`; > 8 bits → `content_modified`

**Which hash is actually decisive.** The on-chain record is keyed by the SHA-256
digest, so `verifyEvidence()` returns a record only when the uploaded bytes are
exactly those anchored. K2A is a deterministic function of those bytes, so on
that path the recomputed K2A always equals the stored one and the distance is
always 0 - the K2A verdict there is a consistency check on the pipeline, not an
independent test.

K2A earns its place on the **other** branch. When the SHA-256 does not match, no
record can be retrieved by key, and the backend instead scans its confirmed
records for the smallest K2A distance. That is what lets a re-encoded copy of
anchored footage (0.58 bits) be matched back to its original. The scan is linear
in the number of confirmed records and is served from the local cache, because
the contract has no lookup keyed by perceptual hash.

## Smart Contract

`EvidenceLog.sol` stores per evidence entry:
- `bytes32 videoHash` - SHA-256 of video file
- `bytes32 perceptualHash` - K2A-Hash (content fingerprint)
- `bytes32 reportHash` - SHA-256 of AI forensic report
- `string cameraId`, `uint256 timestamp`, `address uploader`

Write access is restricted to the deploying wallet by an `onlyOwner` modifier over an `immutable` owner address. Measured cost: 374,095 gas per record on average (222,264 to 388,607), 1,025,622 to deploy.

Deployed on the **Polygon Amoy** public testnet (chainId 80002) at
[`0x1721939863cb1a54cC712C03dc828D8751FA109F`](https://amoy.polygonscan.com/address/0x1721939863cb1a54cC712C03dc828D8751FA109F),
block 44,795,368. Deployment there consumed 1,025,622 gas, identical to the local
measurement. The deployment record and the compiled artifact are tracked under
`ignition/deployments/chain-80002/`, so the deployed runtime bytecode can be
checked against the artifact directly; the two differ only in the two
`immutable` slots holding the owner address, which the compiler inlines at
construction.

Note that `timestamp` is caller-supplied. The trustworthy time anchor is `loggedAt`, which the contract sets from `block.timestamp`.

## Project Structure

```
blockchain-cctv/
├── contracts/          # Solidity smart contracts
│   └── EvidenceLog.sol
├── ignition/           # Hardhat Ignition deployment modules
├── backend/            # Node.js + Express API + SQLite
│   ├── server.js
│   └── database/
├── frontend/           # React 18 + Vite + Tailwind
│   └── src/
├── ai-service/         # FastAPI + Python K2A-Hash + Gemini AI
│   └── app/
│       └── utils/
│           └── k2a_hash.py
├── cloud-storage/      # Local clip storage (runtime)
├── docs/res/           # Generated paper figures (git-ignored, see experiments/)
├── experiments/        # Evaluation harness, results.csv, clip manifest
├── test/               # Hardhat contract tests
├── hardhat.config.ts
└── package.json
```

## Running Tests

```bash
# Smart contract tests
npx hardhat test

# With gas reporting
REPORT_GAS=true npx hardhat test
```

## Building the Paper

`paper.tex` lives one directory above this repository and is **not** part of it.

```bash
cd .. && tectonic -X compile paper.tex
```

## Key Design Decisions

- **EVM over Hyperledger Fabric:** JSON-RPC is lightweight vs Docker/Java overhead; Solidity is industry-standard
- **Two-level verification:** SQL for speed (< 1s), blockchain for immutable truth (< 5s)
- **Hash-only storage:** 32 bytes on-chain vs GB-sized videos; preserves privacy
- **K2A + SHA-256 dual hash:** SHA-256 catches byte-level changes exactly. K2A adds a fixed 32-byte content field that survives transcoding, so a re-encoded copy can be matched back to its anchored original. It is **not** a general tamper detector; the measured table above is the honest statement of its reach

## License

Academic project — Mini Project 2026.
