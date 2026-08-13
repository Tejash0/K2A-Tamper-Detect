import express from "express";
import cors from "cors";
import multer from "multer";
import crypto from "crypto";
import { ethers } from "ethers";
import Database from "better-sqlite3";
import dotenv from "dotenv";
import path from "path";
import { fileURLToPath } from "url";
import fs from "fs";

dotenv.config();

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Configuration
const PORT = process.env.PORT || 5000;
const DATABASE_PATH = process.env.DATABASE_PATH || "./database/evidence.db";
const HARDHAT_NETWORK_URL =
  process.env.HARDHAT_NETWORK_URL || "http://127.0.0.1:8545";
const CONTRACT_ADDRESS = process.env.CONTRACT_ADDRESS;
const PRIVATE_KEY = process.env.PRIVATE_KEY;

// K2A hash properties.
// The hash occupies a 256-bit bytes32 field but carries only 64 significant
// bits (4 sampled frames x 16 blocks), so no Hamming distance can exceed 64.
const K2A_MAX_DISTANCE = 64;
// Empirically calibrated on the 60-clip evaluation set. At or below this
// distance the K2A verdict is content_authentic.
const K2A_AUTHENTIC_THRESHOLD = 8;

// Cloud storage directories
const CLOUD_STORAGE_ROOT = path.resolve(__dirname, "..", "cloud-storage");
const CLIPS_DIR = path.join(CLOUD_STORAGE_ROOT, "clips");
const REPORTS_DIR = path.join(CLOUD_STORAGE_ROOT, "reports");

// Ensure cloud storage directories exist
[CLIPS_DIR, REPORTS_DIR].forEach((dir) => {
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
});

// Contract ABI (updated with new fields)
const CONTRACT_ABI = [
  "function logEvidence(bytes32 _videoHash, string _cameraId, uint256 _timestamp, bytes32 _reportHash, bytes32 _perceptualHash, string _aiModelVersion, uint256 _confidenceScore, string _eventType, string _clipCloudURI) external",
  "function verifyEvidence(bytes32 _videoHash) external view returns (bool exists, uint256 timestamp, uint256 loggedAt, string cameraId, bytes32 reportHash, bytes32 perceptualHash, string eventType, uint256 confidenceScore)",
  "function getEvidence(bytes32 _videoHash) external view returns (tuple(bytes32 videoHash, string cameraId, uint256 timestamp, address uploader, uint256 blockNumber, uint256 loggedAt, bytes32 reportHash, bytes32 perceptualHash, string aiModelVersion, uint256 confidenceScore, string eventType, string clipCloudURI))",
  "function getEvidenceCount() external view returns (uint256 count)",
  "function getEvidenceHashAtIndex(uint256 _index) external view returns (bytes32 hash)",
  "event EvidenceLogged(bytes32 indexed videoHash, string cameraId, uint256 timestamp, address indexed uploader, uint256 blockNumber, bytes32 reportHash, bytes32 perceptualHash, string eventType, uint256 confidenceScore)",
];

// Initialize Express
const app = express();

app.use(
  cors({
    origin: ["http://localhost:5173", "http://127.0.0.1:5173"],
    methods: ["GET", "POST"],
    credentials: true,
  })
);
app.use(express.json());

// Configure video storage directory
const VIDEOS_DIR = path.resolve(__dirname, "videos");
if (!fs.existsSync(VIDEOS_DIR)) {
  fs.mkdirSync(VIDEOS_DIR, { recursive: true });
}

const storage = multer.diskStorage({
  destination: (req, file, cb) => cb(null, VIDEOS_DIR),
  filename: (req, file, cb) => {
    const timestamp = Date.now();
    const ext = path.extname(file.originalname) || ".mp4";
    cb(null, `evidence_${timestamp}${ext}`);
  },
});

// Disk storage — for /api/record (persists file, triggers AI pipeline)
const upload = multer({
  storage: storage,
  limits: { fileSize: 500 * 1024 * 1024 },
});

// Memory storage — for /api/verify (never writes to disk, no AI trigger, no clip/report generated)
const uploadMemory = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 500 * 1024 * 1024 },
});

// Initialize SQLite database
const db = new Database(path.resolve(__dirname, DATABASE_PATH));
db.pragma("journal_mode = WAL");

db.exec(`
  CREATE TABLE IF NOT EXISTS evidence_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_hash TEXT NOT NULL UNIQUE,
    camera_id TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    file_path TEXT,
    file_size INTEGER,
    blockchain_tx TEXT,
    block_number INTEGER,
    uploader TEXT,
    detection_type TEXT,
    report_hash TEXT,
    perceptual_hash TEXT,
    ai_model_version TEXT,
    confidence_score INTEGER DEFAULT 0,
    event_type TEXT,
    clip_cloud_uri TEXT,
    status TEXT CHECK(status IN ('pending', 'confirmed', 'failed')) DEFAULT 'pending',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
  );
`);

// Add new columns if they don't exist (for existing databases)
const newColumns = [
  "file_path TEXT",
  "file_size INTEGER",
  "detection_type TEXT",
  "report_hash TEXT",
  "perceptual_hash TEXT",
  "ai_model_version TEXT",
  "confidence_score INTEGER DEFAULT 0",
  "event_type TEXT",
  "clip_cloud_uri TEXT",
];
for (const col of newColumns) {
  try {
    db.exec(`ALTER TABLE evidence_log ADD COLUMN ${col}`);
  } catch (e) {
    /* exists */
  }
}

// Create indexes after columns exist
db.exec(`
  CREATE INDEX IF NOT EXISTS idx_video_hash ON evidence_log(video_hash);
  CREATE INDEX IF NOT EXISTS idx_status ON evidence_log(status);
  CREATE INDEX IF NOT EXISTS idx_camera_id ON evidence_log(camera_id);
  CREATE INDEX IF NOT EXISTS idx_event_type ON evidence_log(event_type);
  CREATE INDEX IF NOT EXISTS idx_report_hash ON evidence_log(report_hash);
`);

// Prepared statements
const insertEvidence = db.prepare(`
  INSERT INTO evidence_log (video_hash, camera_id, timestamp, file_path, file_size, detection_type,
    report_hash, perceptual_hash, ai_model_version, confidence_score, event_type, clip_cloud_uri, status)
  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
`);

const updateEvidenceConfirmed = db.prepare(`
  UPDATE evidence_log
  SET status = 'confirmed', blockchain_tx = ?, block_number = ?, uploader = ?
  WHERE video_hash = ?
`);

const updateEvidenceFailed = db.prepare(`
  UPDATE evidence_log SET status = 'failed' WHERE video_hash = ?
`);

const getEvidenceByHash = db.prepare(`
  SELECT * FROM evidence_log WHERE video_hash = ?
`);

const getAllEvidence = db.prepare(`
  SELECT * FROM evidence_log ORDER BY created_at DESC
`);

// Initialize blockchain connection
let provider;
let wallet;
let contract;

async function initBlockchain() {
  try {
    provider = new ethers.JsonRpcProvider(HARDHAT_NETWORK_URL);

    if (!PRIVATE_KEY) {
      console.error("ERROR: PRIVATE_KEY not set in .env file");
      return false;
    }
    if (!CONTRACT_ADDRESS) {
      console.error("ERROR: CONTRACT_ADDRESS not set in .env file");
      return false;
    }

    wallet = new ethers.Wallet(PRIVATE_KEY, provider);
    contract = new ethers.Contract(CONTRACT_ADDRESS, CONTRACT_ABI, wallet);

    const network = await provider.getNetwork();
    console.log(`Connected to blockchain network: Chain ID ${network.chainId}`);
    console.log(`Contract address: ${CONTRACT_ADDRESS}`);
    console.log(`Wallet address: ${wallet.address}`);

    return true;
  } catch (error) {
    console.error("Failed to initialize blockchain connection:", error.message);
    return false;
  }
}

function calculateHash(buffer) {
  return "0x" + crypto.createHash("sha256").update(buffer).digest("hex");
}

/**
 * Compute K2A perceptual hash by delegating to the AI service.
 * Single authoritative implementation lives in ai-service/app/utils/k2a_hash.py.
 * Returns ethers.ZeroHash if AI service is unavailable — callers treat this as
 * "no perceptual hash" and k2aHammingDistance() returns null safely.
 */
async function calculatePerceptualHash(
  buffer,
  filename = "video.mp4",
  mimetype = "video/mp4"
) {
  try {
    const formData = new FormData();
    formData.append("video", new File([buffer], filename, { type: mimetype }));
    const res = await fetch("http://localhost:8000/api/ai/k2a", {
      method: "POST",
      body: formData,
    });
    if (!res.ok) throw new Error(`AI service returned ${res.status}`);
    const { k2a_hash } = await res.json();
    return k2a_hash;
  } catch (err) {
    console.error(`K2A via AI service failed: ${err.message}`);
    return ethers.ZeroHash;
  }
}

function isValidHash(hash) {
  return /^0x[a-fA-F0-9]{64}$/.test(hash);
}

/**
 * Compute Hamming distance between two K2A hashes.
 *
 * K2A hashes are stored in a 256-bit bytes32 field but carry only 64
 * significant bits (4 sampled frames x 16 blocks), so bits 64-255 are always
 * zero and the returned distance can never exceed K2A_MAX_DISTANCE.
 *
 * Returns count of differing bits (0 = identical content, higher = more modified).
 */
function k2aHammingDistance(hash1, hash2) {
  if (
    !hash1 ||
    !hash2 ||
    hash1 === ethers.ZeroHash ||
    hash2 === ethers.ZeroHash
  )
    return null;
  const h1 = BigInt(hash1);
  const h2 = BigInt(hash2);
  const xor = h1 ^ h2;
  return xor
    .toString(2)
    .split("")
    .filter((b) => b === "1").length;
}

/**
 * Classify the type of tampering based on perceptual Hamming distance and file size change.
 * Called when SHA-256 does not match (file is not the original).
 *
 * LIMITATION - read before extending this function. Measured over the 60-clip
 * evaluation set, a brightness attack produces a mean distance of 27.73 bits
 * while two entirely unrelated videos produce 28.68. Those are the same
 * distribution, so no band boundary above the authenticity threshold can
 * separate "edited" from "different video". The bands below are a coarse,
 * best-effort hint for the operator UI and must not be presented as a
 * validated classifier. The K2A_AUTHENTIC_THRESHOLD boundary is the only
 * decision this hash actually supports.
 */
function analyzeTamperType(hammingDist, uploadedSize, storedSize) {
  const sizeDiffRatio =
    storedSize && storedSize > 0
      ? Math.abs(uploadedSize - storedSize) / storedSize
      : null;

  // Signed ratio: negative means the upload shrank. Needed because a file that
  // grew is not evidence of compression, however large the change.
  const sizeShrinkRatio =
    storedSize && storedSize > 0
      ? (storedSize - uploadedSize) / storedSize
      : null;

  const sizeInfo =
    sizeDiffRatio !== null
      ? {
          uploadedBytes: uploadedSize,
          originalBytes: storedSize,
          changePercent: Math.round(sizeDiffRatio * 100),
          direction: uploadedSize > storedSize ? "larger" : "smaller",
        }
      : null;

  if (hammingDist === null) {
    return {
      type: "unknown",
      severity: "unknown",
      attributable: false,
      description: "No perceptual hash stored — cannot classify edit type",
      sizeInfo,
    };
  }
  if (hammingDist === 0) {
    if (sizeDiffRatio !== null && sizeDiffRatio > 0.05) {
      return {
        type: "reencoded",
        severity: "low",
        attributable: true,
        description:
          "Content identical but re-encoded — codec, container, or bitrate changed",
        sizeInfo,
      };
    }
    return {
      type: "metadata_modified",
      severity: "low",
      attributable: true,
      description:
        "Video frames unchanged — only metadata, timestamps, or container tags modified",
      sizeInfo,
    };
  }
  if (hammingDist <= K2A_AUTHENTIC_THRESHOLD) {
    // At or below the threshold the K2A verdict is content_authentic (see the
    // verdict equation in the paper). The file changed at byte level, but the
    // visual content is within tolerance, so nothing here may claim a visual
    // edit was detected.
    if (sizeShrinkRatio !== null && sizeShrinkRatio > 0.3) {
      return {
        type: "quality_degradation",
        severity: "low",
        attributable: true,
        description:
          "Visual content within the authenticity threshold, but the file shrank sharply — consistent with heavy compression or downsampling",
        sizeInfo,
      };
    }
    return {
      type: "visual_adjustment",
      severity: "low",
      attributable: true,
      description:
        "Visual content within the authenticity threshold — the difference is byte-level (re-encoding, container, or minor adjustment)",
      sizeInfo,
    };
  }
  // Single band above the threshold, deliberately not given a causal name.
  // Naming it after an edit type would be unsupported: measured means are
  // 1.33 bits for a text overlay, 27.73 for a brightness attack and 28.68 for
  // an entirely unrelated video, so this range is dominated by unrelated
  // content and cannot identify what was done.
  //
  // The theoretical maximum is K2A_MAX_DISTANCE (64), so major_alteration
  // (>80) and unrelated_content (>180) were unreachable dead code and have
  // been removed along with the old overlay_or_filter / content_splice split.
  return {
    type: "content_differs",
    severity: "high",
    attributable: false,
    description:
      "Visual content differs beyond the authenticity threshold. K2A cannot determine what was changed, nor distinguish a heavy edit from an unrelated video, at this distance",
    sizeInfo,
  };
}

/**
 * Copy clip to cloud storage
 */
function storeClip(sourcePath, videoHash) {
  try {
    const ext = path.extname(sourcePath) || ".mp4";
    const filename = `${videoHash.slice(2, 18)}_${Date.now()}${ext}`;
    const destPath = path.join(CLIPS_DIR, filename);
    fs.copyFileSync(sourcePath, destPath);
    return `local://cloud-storage/clips/${filename}`;
  } catch (e) {
    console.error("Failed to store clip:", e.message);
    return "";
  }
}

// API Routes

/**
 * POST /api/record
 */
app.post("/api/record", upload.single("video"), async (req, res) => {
  try {
    if (!req.file) {
      return res
        .status(400)
        .json({ success: false, error: "No video file provided" });
    }

    const cameraId = req.body.cameraId;
    if (!cameraId || cameraId.trim() === "") {
      return res
        .status(400)
        .json({ success: false, error: "Camera ID is required" });
    }

    const detectionType = req.body.detectionType || "manual";
    const filePath = req.file.path;
    const fileBuffer = fs.readFileSync(filePath);
    const videoHash = calculateHash(fileBuffer);
    const fileSize = req.file.size;
    const timestamp = Math.floor(Date.now() / 1000);

    const reportHash = req.body.reportHash || ethers.ZeroHash;
    const perceptualHash =
      req.body.perceptualHash && req.body.perceptualHash !== ethers.ZeroHash
        ? req.body.perceptualHash
        : await calculatePerceptualHash(
            fileBuffer,
            req.file.originalname,
            req.file.mimetype
          );
    const aiModelVersion = req.body.aiModelVersion || "";
    const confidenceScore = parseInt(req.body.confidenceScore) || 0;
    const eventType = req.body.eventType || detectionType;

    // Store clip to cloud storage
    const clipCloudURI = storeClip(filePath, videoHash);

    console.log(
      `Recording evidence: hash=${videoHash}, cameraId=${cameraId}, eventType=${eventType}`
    );

    const existing = getEvidenceByHash.get(videoHash);
    if (existing) {
      return res.status(409).json({
        success: false,
        error: "Evidence with this hash already exists",
        existing: {
          videoHash: existing.video_hash,
          cameraId: existing.camera_id,
          status: existing.status,
        },
      });
    }

    try {
      insertEvidence.run(
        videoHash,
        cameraId.trim(),
        timestamp,
        filePath,
        fileSize,
        detectionType,
        reportHash,
        perceptualHash,
        aiModelVersion,
        confidenceScore,
        eventType,
        clipCloudURI
      );
    } catch (dbError) {
      if (dbError.code === "SQLITE_CONSTRAINT_UNIQUE") {
        fs.unlinkSync(filePath);
        return res.status(409).json({
          success: false,
          error: "Evidence already exists in database",
        });
      }
      throw dbError;
    }

    try {
      const tx = await contract.logEvidence(
        videoHash,
        cameraId.trim(),
        timestamp,
        reportHash,
        perceptualHash,
        aiModelVersion,
        confidenceScore,
        eventType,
        clipCloudURI
      );
      console.log(`Transaction submitted: ${tx.hash}`);

      const receipt = await tx.wait();
      console.log(`Transaction confirmed in block ${receipt.blockNumber}`);

      updateEvidenceConfirmed.run(
        receipt.hash,
        receipt.blockNumber,
        wallet.address,
        videoHash
      );

      return res.json({
        success: true,
        videoHash,
        transactionHash: receipt.hash,
        blockNumber: receipt.blockNumber,
        cameraId: cameraId.trim(),
        timestamp,
        filePath,
        fileSize,
        detectionType,
        reportHash,
        perceptualHash,
        aiModelVersion,
        confidenceScore,
        eventType,
        clipCloudURI,
      });
    } catch (blockchainError) {
      console.error("Blockchain error:", blockchainError.message);

      // Polygon Amoy doesn't return revert strings reliably — check chain directly
      // before marking as failed, in case the hash is already there
      try {
        const onChain = await contract.verifyEvidence(videoHash);
        if (onChain.exists) {
          console.log(`Hash already on blockchain — syncing SQL to confirmed`);
          updateEvidenceConfirmed.run(
            null,
            Number(onChain.loggedAt),
            wallet.address,
            videoHash
          );
          return res.status(409).json({
            success: false,
            alreadyOnChain: true,
            error: "Evidence already exists on blockchain",
            videoHash,
            cameraId: onChain.cameraId,
            timestamp: Number(onChain.timestamp),
          });
        }
      } catch (_) {
        /* verifyEvidence failed — fall through */
      }

      updateEvidenceFailed.run(videoHash);
      return res.status(500).json({
        success: false,
        error: "Failed to record on blockchain: " + blockchainError.message,
      });
    }
  } catch (error) {
    console.error("Record error:", error);
    return res.status(500).json({
      success: false,
      error: "Internal server error: " + error.message,
    });
  }
});

/**
 * POST /api/verify
 * Uses memory storage — file never touches disk, no AI pipeline triggered.
 */
app.post("/api/verify", uploadMemory.single("video"), async (req, res) => {
  try {
    if (!req.file) {
      return res
        .status(400)
        .json({ verified: false, error: "No video file provided" });
    }

    // Buffer lives in memory only — no disk write, no clip stored
    const fileBuffer = req.file.buffer;
    const videoHash = calculateHash(fileBuffer);
    const uploadedPerceptualHash = await calculatePerceptualHash(
      fileBuffer,
      req.file.originalname,
      req.file.mimetype
    );
    console.log(`Verifying evidence: hash=${videoHash}`);

    // Call AI service for Gemini forensic report on the uploaded video
    let geminiReport = null;
    try {
      const filename = req.file.originalname || "verify.mp4";
      const mimetype = req.file.mimetype || "video/mp4";
      const formData = new FormData();
      // Use File (Node.js 20+) for correct Content-Disposition with filename
      formData.append(
        "video",
        new File([fileBuffer], filename, { type: mimetype })
      );
      formData.append("camera_id", "VERIFY");
      formData.append("event_type", "verification");
      formData.append("confidence", "1.0");
      console.log(
        `Sending video to AI service for Gemini analysis (${fileBuffer.length} bytes)...`
      );
      const aiRes = await fetch("http://localhost:8000/api/ai/analyze", {
        method: "POST",
        body: formData,
      });
      if (aiRes.ok) {
        geminiReport = await aiRes.json();
        console.log(
          `Gemini report received: severity=${geminiReport.severity}`
        );
      } else {
        const errText = await aiRes.text();
        console.error(`AI service returned ${aiRes.status}: ${errText}`);
      }
    } catch (aiErr) {
      console.error(`AI service unreachable or failed: ${aiErr.message}`);
    }

    const cachedEvidence = getEvidenceByHash.get(videoHash);

    try {
      const result = await contract.verifyEvidence(videoHash);

      if (result.exists) {
        const evidence = await contract.getEvidence(videoHash);

        // K2A perceptual hash comparison for content authenticity
        const storedPerceptualHash = evidence.perceptualHash;
        const hammingDist = k2aHammingDistance(
          storedPerceptualHash,
          uploadedPerceptualHash
        );
        const k2aVerdict =
          hammingDist === null
            ? "no_perceptual_hash"
            : hammingDist <= K2A_AUTHENTIC_THRESHOLD
            ? "content_authentic"
            : "content_modified";

        const responseData = {
          verified: true,
          source: cachedEvidence ? "cache+blockchain" : "blockchain",
          k2a_hamming_distance: hammingDist,
          k2a_verdict: k2aVerdict,
          gemini_report: geminiReport,
          evidence: {
            videoHash: evidence.videoHash,
            cameraId: evidence.cameraId,
            timestamp: Number(evidence.timestamp),
            uploader: evidence.uploader,
            blockNumber: Number(evidence.blockNumber),
            loggedAt: Number(evidence.loggedAt),
            reportHash: evidence.reportHash,
            perceptualHash: evidence.perceptualHash,
            aiModelVersion: evidence.aiModelVersion,
            confidenceScore: Number(evidence.confidenceScore),
            eventType: evidence.eventType,
            clipCloudURI: evidence.clipCloudURI,
          },
        };

        if (!cachedEvidence) {
          try {
            insertEvidence.run(
              videoHash,
              evidence.cameraId,
              Number(evidence.timestamp),
              null,
              null,
              "discovered",
              evidence.reportHash,
              evidence.perceptualHash,
              evidence.aiModelVersion,
              Number(evidence.confidenceScore),
              evidence.eventType,
              evidence.clipCloudURI
            );
            updateEvidenceConfirmed.run(
              null,
              Number(evidence.blockNumber),
              evidence.uploader,
              videoHash
            );
          } catch (e) {
            /* ignore if exists */
          }
        }

        return res.json(responseData);
      } else {
        // Hash not on blockchain — scan SQL for closest perceptual match to classify tampering
        if (cachedEvidence && cachedEvidence.status === "pending") {
          return res.json({
            verified: false,
            videoHash,
            message:
              "Evidence found in cache but not yet confirmed on blockchain",
          });
        }

        const confirmedRecords = db
          .prepare(
            `SELECT video_hash, camera_id, timestamp, perceptual_hash, file_size FROM evidence_log
           WHERE perceptual_hash IS NOT NULL AND status = 'confirmed'`
          )
          .all();

        let closestMatch = null;
        let minHamming = Infinity;
        for (const record of confirmedRecords) {
          const dist = k2aHammingDistance(
            record.perceptual_hash,
            uploadedPerceptualHash
          );
          if (dist !== null && dist < minHamming) {
            minHamming = dist;
            closestMatch = record;
          }
        }

        const tamperAnalysis = closestMatch
          ? analyzeTamperType(
              minHamming,
              fileBuffer.length,
              closestMatch.file_size
            )
          : analyzeTamperType(null, fileBuffer.length, null);

        // The old guard here was `minHamming <= 180`, which is unreachable on a
        // 64-bit hash and therefore always true whenever closestMatch is set,
        // making the "no match" branch dead. Distances above the authenticity
        // threshold cannot be attributed: a brightness attack (mean 27.73 bits)
        // and an entirely unrelated video (mean 28.68) are the same
        // distribution, so claiming "tampered version of THIS record" there is
        // unsupported. When the distance is not attributable we withhold the
        // closest-record identity as well, so that a consumer reading the
        // structured fields cannot draw a link the message disclaims.
        const attributable =
          closestMatch !== null && minHamming <= K2A_AUTHENTIC_THRESHOLD;

        return res.json({
          verified: false,
          videoHash,
          attributable,
          tamperAnalysis,
          gemini_report: geminiReport,
          closestMatch: attributable
            ? {
                videoHash: closestMatch.video_hash,
                cameraId: closestMatch.camera_id,
                timestamp: closestMatch.timestamp,
                hammingDistance: minHamming,
              }
            : null,
          nearestDistance: closestMatch ? minHamming : null,
          message: !closestMatch
            ? "No matching evidence found on blockchain"
            : attributable
            ? `Modified version of recorded evidence detected — ${tamperAnalysis.type}`
            : `No evidence record matches this file. Closest record differs by ${minHamming} of ${K2A_MAX_DISTANCE} bits, which is not close enough to attribute`,
        });
      }
    } catch (blockchainError) {
      console.error("Blockchain verification error:", blockchainError.message);

      if (cachedEvidence && cachedEvidence.status === "confirmed") {
        return res.json({
          verified: true,
          source: "cache-only",
          warning: "Blockchain verification failed, using cached data",
          evidence: {
            videoHash: cachedEvidence.video_hash,
            cameraId: cachedEvidence.camera_id,
            timestamp: cachedEvidence.timestamp,
            uploader: cachedEvidence.uploader,
            blockNumber: cachedEvidence.block_number,
            reportHash: cachedEvidence.report_hash,
            perceptualHash: cachedEvidence.perceptual_hash,
            aiModelVersion: cachedEvidence.ai_model_version,
            confidenceScore: cachedEvidence.confidence_score,
            eventType: cachedEvidence.event_type,
            clipCloudURI: cachedEvidence.clip_cloud_uri,
          },
        });
      }

      return res.status(500).json({
        verified: false,
        error: "Blockchain verification failed: " + blockchainError.message,
      });
    }
  } catch (error) {
    console.error("Verify error:", error);
    return res.status(500).json({
      verified: false,
      error: "Internal server error: " + error.message,
    });
  }
});

/**
 * GET /api/logs
 */
app.get("/api/logs", async (req, res) => {
  try {
    // Serve from SQLite immediately — it is kept in sync with the blockchain.
    // Hitting the blockchain per-record (N RPC calls) is too slow for a dashboard.
    const cachedRecords = getAllEvidence.all();
    return res.json({
      success: true,
      source: "cache",
      count: cachedRecords.length,
      records: cachedRecords.map((r) => ({
        videoHash: r.video_hash,
        cameraId: r.camera_id,
        timestamp: r.timestamp,
        uploader: r.uploader,
        blockNumber: r.block_number,
        filePath: r.file_path,
        fileSize: r.file_size,
        detectionType: r.detection_type,
        reportHash: r.report_hash,
        perceptualHash: r.perceptual_hash,
        aiModelVersion: r.ai_model_version,
        confidenceScore: r.confidence_score,
        eventType: r.event_type,
        clipCloudURI: r.clip_cloud_uri,
        status: r.status,
      })),
    });
  } catch (error) {
    console.error("Logs error:", error);

    try {
      const cachedRecords = getAllEvidence.all();
      return res.json({
        success: true,
        source: "cache",
        warning: "Blockchain unavailable, showing cached records",
        count: cachedRecords.length,
        records: cachedRecords.map((r) => ({
          videoHash: r.video_hash,
          cameraId: r.camera_id,
          timestamp: r.timestamp,
          uploader: r.uploader,
          blockNumber: r.block_number,
          filePath: r.file_path,
          fileSize: r.file_size,
          detectionType: r.detection_type,
          reportHash: r.report_hash,
          perceptualHash: r.perceptual_hash,
          aiModelVersion: r.ai_model_version,
          confidenceScore: r.confidence_score,
          eventType: r.event_type,
          clipCloudURI: r.clip_cloud_uri,
          status: r.status,
        })),
      });
    } catch (dbError) {
      // Report the DB failure, not the outer error. Blaming `error.message`
      // here discarded the actual cause (e.g. a locked or corrupt SQLite file)
      // and pointed at an unrelated, already-logged failure.
      console.error("Failed to fetch logs from SQLite:", dbError.message);
      return res.status(500).json({
        success: false,
        error: "Failed to fetch logs: " + dbError.message,
      });
    }
  }
});

/**
 * POST /api/cleanup
 * Deletes all video and clip files not referenced by any DB record.
 */
app.post("/api/cleanup", (req, res) => {
  try {
    const records = db
      .prepare("SELECT file_path, clip_cloud_uri FROM evidence_log")
      .all();

    const referencedVideos = new Set();
    const referencedClips = new Set();

    for (const r of records) {
      if (r.file_path) referencedVideos.add(path.resolve(r.file_path));
      if (r.clip_cloud_uri) {
        // clip_cloud_uri format: "local://cloud-storage/clips/filename.mp4"
        const relative = r.clip_cloud_uri.replace("local://", "");
        referencedClips.add(path.resolve(__dirname, "..", relative));
      }
    }

    let deletedVideos = 0,
      deletedClips = 0;
    const deletedList = [];

    if (fs.existsSync(VIDEOS_DIR)) {
      for (const file of fs.readdirSync(VIDEOS_DIR)) {
        const fullPath = path.join(VIDEOS_DIR, file);
        if (!referencedVideos.has(fullPath)) {
          fs.unlinkSync(fullPath);
          deletedVideos++;
          deletedList.push(`videos/${file}`);
        }
      }
    }

    if (fs.existsSync(CLIPS_DIR)) {
      for (const file of fs.readdirSync(CLIPS_DIR)) {
        const fullPath = path.join(CLIPS_DIR, file);
        if (!referencedClips.has(fullPath)) {
          fs.unlinkSync(fullPath);
          deletedClips++;
          deletedList.push(`clips/${file}`);
        }
      }
    }

    console.log(
      `Cleanup: deleted ${deletedVideos} videos, ${deletedClips} clips`
    );
    return res.json({
      success: true,
      deleted: {
        videos: deletedVideos,
        clips: deletedClips,
        files: deletedList,
      },
      retained: { videos: referencedVideos.size, clips: referencedClips.size },
    });
  } catch (error) {
    console.error("Cleanup error:", error);
    return res.status(500).json({ success: false, error: error.message });
  }
});

/**
 * GET /api/health
 */
app.get("/api/health", async (req, res) => {
  try {
    const blockNumber = await provider.getBlockNumber();
    return res.json({
      status: "ok",
      blockchain: "connected",
      blockNumber,
      contractAddress: CONTRACT_ADDRESS,
    });
  } catch (error) {
    return res.json({
      status: "degraded",
      blockchain: "disconnected",
      error: error.message,
    });
  }
});

// Start server
async function start() {
  const blockchainReady = await initBlockchain();

  if (!blockchainReady) {
    console.warn("WARNING: Starting server without blockchain connection");
    console.warn("Make sure Hardhat node is running and .env is configured");
  }

  app.listen(PORT, () => {
    console.log(`Backend server running on http://localhost:${PORT}`);
    console.log("Endpoints:");
    console.log(`  POST /api/record - Record new evidence`);
    console.log(`  POST /api/verify - Verify evidence`);
    console.log(`  GET  /api/logs   - Get all evidence logs`);
    console.log(`  GET  /api/health - Health check`);
  });
}

start();
