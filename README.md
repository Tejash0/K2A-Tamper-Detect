# Blockchain-Based CCTV Evidence Verification System

A tamper-proof surveillance verification system that stores cryptographic hashes of video footage on a local Ethereum blockchain. The system uses a **dual-hash strategy** - SHA-256 for exact integrity and K2A-Hash, a perceptual hash, for content-level change detection - anchored immutably in a Solidity smart contract.

## K2A-Hash

**K2A-Hash** is a training-free perceptual hash for video, built from:
- Uniform 8×8 block grid diagonal pixel extraction
- Bit compression against the absolute constant 128
- Temporal extension across 4 sampled frames

It produces **64 significant bits**, stored in a 256-bit `bytes32` field, so the maximum Hamming distance between any two K2A hashes is 64.

SHA-256 detects any byte-level change to the file. K2A-Hash is intended to complement it by responding to changes in visual content rather than bytes, and it survives re-encoding: a transcoded copy of the same footage differs by 0.58 bits on average.

### What it does and does not detect

Measured over 60 UCF-Crime clips (`experiments/results.csv`):

| Change | Mean K2A distance | Detected above the 8-bit threshold |
|---|---|---|
| Re-encoding (CRF 28) | 0.58 | No, by design |
| Frame deletion (40%) | 0.67 | No |
| Brightness (+0.3 EQ) | 27.73 | Yes, 57 of 60 clips |
| Text overlay | 1.33 | No |

**Known limitations, stated plainly:**
- K2A responds to global intensity change. It does **not** currently detect localised edits, frame deletion, or overlays.
- Deepfake and frame-substitution detection is **untested**. Do not rely on it for that.
- Thresholding against an absolute constant collapses entropy on dark footage: set bits average 22.35 of 64, minimum 1. Across all 1770 unrelated-video pairs the mean distance is 28.68 bits with a minimum of 2, giving a **0.8 per cent false accept rate** at the 8-bit threshold.
- A brightness attack (27.73) and an entirely unrelated video (28.68) are the same distribution, so the system reports a distance but does **not** classify tamper type.

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

- **Node.js** v18+ (v25 recommended)
- **Python** 3.10+ with `pip`
- **npm** (or pnpm/yarn)
- **pdflatex** (optional, for building the paper)

## Quick Start (3 Terminals)

### Terminal 1 — Blockchain Node
```bash
# Start local Hardhat EVM
npx hardhat node

# In a separate shell, deploy the contract (first time only):
npx hardhat ignition deploy ./ignition/modules/EvidenceLog.ts --network localhost
```

### Terminal 2 — Backend API
```bash
cd backend
npm install

# Copy and edit environment variables
cp .env.example .env
# Set CONTRACT_ADDRESS from the deployment output above
# Set PRIVATE_KEY to a Hardhat test account key

node server.js
```

### Terminal 3 — Frontend
```bash
cd frontend
npm install
npm run dev
# Opens at http://localhost:5173
```

### Optional: AI Service
```bash
cd ai-service
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

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
Returns:
```json
{
  "verified": true,
  "level": "blockchain",
  "k2a_hamming_distance": 3,
  "k2a_verdict": "content_authentic"
}
```

## Verification Logic

1. **Level 1 (SQL cache):** Sub-second lookup of stored hash
2. **Level 2 (blockchain):** On-chain `verifyEvidence()` call
3. **K2A verdict:** Hamming distance ≤ 8 bits → `content_authentic`; > 8 bits → `content_modified`

## Smart Contract

`EvidenceLog.sol` stores per evidence entry:
- `bytes32 videoHash` - SHA-256 of video file
- `bytes32 perceptualHash` - K2A-Hash (content fingerprint)
- `bytes32 reportHash` - SHA-256 of AI forensic report
- `string cameraId`, `uint256 timestamp`, `address uploader`

Write access is restricted to the deploying wallet by an `onlyOwner` modifier over an `immutable` owner address. Measured cost: 374,095 gas per record on average (222,264 to 388,607), 1,025,622 to deploy.

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

```bash
cd docs/paper
pdflatex paper.tex
bibtex paper
pdflatex paper.tex
pdflatex paper.tex
```

## Key Design Decisions

- **EVM over Hyperledger Fabric:** JSON-RPC is lightweight vs Docker/Java overhead; Solidity is industry-standard
- **Two-level verification:** SQL for speed (< 1s), blockchain for immutable truth (< 5s)
- **Hash-only storage:** 32 bytes on-chain vs GB-sized videos; preserves privacy
- **K2A + SHA-256 dual hash:** SHA-256 catches byte-level changes; K2A catches semantic content changes

## License

Academic project — Mini Project 2026.
