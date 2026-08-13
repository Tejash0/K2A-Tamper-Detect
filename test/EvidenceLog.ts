import { expect } from "chai";
import { ethers } from "hardhat";
import { EvidenceLog } from "../typechain-types";
import { HardhatEthersSigner } from "@nomicfoundation/hardhat-ethers/signers";

describe("EvidenceLog", function () {
  let evidenceLog: EvidenceLog;
  let owner: HardhatEthersSigner;
  let addr1: HardhatEthersSigner;

  // Sample test data
  const sampleHash = ethers.keccak256(
    ethers.toUtf8Bytes("sample_video_content")
  );
  const sampleCameraId = "CAM-001";
  const sampleTimestamp = Math.floor(Date.now() / 1000) - 3600;
  const sampleReportHash = ethers.keccak256(
    ethers.toUtf8Bytes("sample_report")
  );
  const samplePerceptualHash = ethers.keccak256(
    ethers.toUtf8Bytes("sample_phash")
  );
  const sampleAiModel = "gemini-1.5-flash";
  const sampleConfidence = 8500; // 85.00%
  const sampleEventType = "violence";
  const sampleClipURI = "local://cloud-storage/clips/evidence_001.mp4";

  // Helper to call logEvidence with all params
  function logEvidenceFull(
    contract: EvidenceLog,
    videoHash = sampleHash,
    cameraId = sampleCameraId,
    timestamp = sampleTimestamp,
    reportHash = sampleReportHash,
    perceptualHash = samplePerceptualHash,
    aiModelVersion = sampleAiModel,
    confidenceScore = sampleConfidence,
    eventType = sampleEventType,
    clipCloudURI = sampleClipURI
  ) {
    return contract.logEvidence(
      videoHash,
      cameraId,
      timestamp,
      reportHash,
      perceptualHash,
      aiModelVersion,
      confidenceScore,
      eventType,
      clipCloudURI
    );
  }

  beforeEach(async function () {
    [owner, addr1] = await ethers.getSigners();
    const EvidenceLogFactory = await ethers.getContractFactory("EvidenceLog");
    evidenceLog = await EvidenceLogFactory.deploy();
    await evidenceLog.waitForDeployment();
  });

  describe("Deployment", function () {
    it("Should deploy successfully", async function () {
      expect(await evidenceLog.getAddress()).to.be.properAddress;
    });

    it("Should start with zero evidence count", async function () {
      expect(await evidenceLog.getEvidenceCount()).to.equal(0);
    });

    it("Should set the deployer as owner", async function () {
      expect(await evidenceLog.owner()).to.equal(owner.address);
    });
  });

  describe("logEvidence", function () {
    it("Should log evidence with all new fields", async function () {
      await logEvidenceFull(evidenceLog);
      expect(await evidenceLog.getEvidenceCount()).to.equal(1);

      const evidence = await evidenceLog.getEvidence(sampleHash);
      expect(evidence.videoHash).to.equal(sampleHash);
      expect(evidence.cameraId).to.equal(sampleCameraId);
      expect(evidence.timestamp).to.equal(sampleTimestamp);
      expect(evidence.uploader).to.equal(owner.address);
      expect(evidence.reportHash).to.equal(sampleReportHash);
      expect(evidence.perceptualHash).to.equal(samplePerceptualHash);
      expect(evidence.aiModelVersion).to.equal(sampleAiModel);
      expect(evidence.confidenceScore).to.equal(sampleConfidence);
      expect(evidence.eventType).to.equal(sampleEventType);
      expect(evidence.clipCloudURI).to.equal(sampleClipURI);
    });

    it("Should emit EvidenceLogged event with new fields", async function () {
      await expect(logEvidenceFull(evidenceLog))
        .to.emit(evidenceLog, "EvidenceLogged")
        .withArgs(
          sampleHash,
          sampleCameraId,
          sampleTimestamp,
          owner.address,
          (await ethers.provider.getBlockNumber()) + 1,
          sampleReportHash,
          samplePerceptualHash,
          sampleEventType,
          sampleConfidence
        );
    });

    it("Should allow zero report and perceptual hashes (Phase 1 defaults)", async function () {
      await logEvidenceFull(
        evidenceLog,
        sampleHash,
        sampleCameraId,
        sampleTimestamp,
        ethers.ZeroHash,
        ethers.ZeroHash,
        "",
        0,
        "",
        ""
      );

      const evidence = await evidenceLog.getEvidence(sampleHash);
      expect(evidence.reportHash).to.equal(ethers.ZeroHash);
      expect(evidence.perceptualHash).to.equal(ethers.ZeroHash);
      expect(evidence.aiModelVersion).to.equal("");
      expect(evidence.confidenceScore).to.equal(0);
      expect(evidence.eventType).to.equal("");
      expect(evidence.clipCloudURI).to.equal("");
    });

    it("Should reject confidence score > 10000", async function () {
      await expect(
        logEvidenceFull(
          evidenceLog,
          sampleHash,
          sampleCameraId,
          sampleTimestamp,
          sampleReportHash,
          samplePerceptualHash,
          sampleAiModel,
          10001,
          sampleEventType,
          sampleClipURI
        )
      ).to.be.revertedWith("Confidence score must be <= 10000");
    });

    it("Should accept confidence score of exactly 10000", async function () {
      await logEvidenceFull(
        evidenceLog,
        sampleHash,
        sampleCameraId,
        sampleTimestamp,
        sampleReportHash,
        samplePerceptualHash,
        sampleAiModel,
        10000,
        sampleEventType,
        sampleClipURI
      );
      const evidence = await evidenceLog.getEvidence(sampleHash);
      expect(evidence.confidenceScore).to.equal(10000);
    });

    it("Should prevent duplicate hashes", async function () {
      await logEvidenceFull(evidenceLog);
      await expect(logEvidenceFull(evidenceLog)).to.be.revertedWith(
        "Evidence already exists"
      );
    });

    it("Should reject empty video hash", async function () {
      await expect(
        logEvidenceFull(evidenceLog, ethers.ZeroHash)
      ).to.be.revertedWith("Video hash cannot be empty");
    });

    it("Should reject empty camera ID", async function () {
      await expect(
        logEvidenceFull(evidenceLog, sampleHash, "")
      ).to.be.revertedWith("Camera ID cannot be empty");
    });

    it("Should reject evidence from a non-owner", async function () {
      const hash = ethers.keccak256(ethers.toUtf8Bytes("video-from-stranger"));

      await expect(
        logEvidenceFull(evidenceLog.connect(addr1), hash)
      ).to.be.revertedWith("Caller is not the owner");
    });

    it("Should allow the owner to log multiple records", async function () {
      const hash1 = ethers.keccak256(ethers.toUtf8Bytes("video1"));
      const hash2 = ethers.keccak256(ethers.toUtf8Bytes("video2"));

      await logEvidenceFull(evidenceLog.connect(owner), hash1);
      await logEvidenceFull(evidenceLog.connect(owner), hash2);

      const evidence1 = await evidenceLog.getEvidence(hash1);
      const evidence2 = await evidenceLog.getEvidence(hash2);
      expect(evidence1.uploader).to.equal(owner.address);
      expect(evidence2.uploader).to.equal(owner.address);
    });
  });

  describe("verifyEvidence", function () {
    it("Should return full details for existing evidence", async function () {
      await logEvidenceFull(evidenceLog);

      const result = await evidenceLog.verifyEvidence(sampleHash);
      expect(result.exists).to.equal(true);
      expect(result.timestamp).to.equal(sampleTimestamp);
      expect(result.cameraId).to.equal(sampleCameraId);
      expect(result.reportHash).to.equal(sampleReportHash);
      expect(result.perceptualHash).to.equal(samplePerceptualHash);
      expect(result.eventType).to.equal(sampleEventType);
      expect(result.confidenceScore).to.equal(sampleConfidence);
    });

    it("Should return false with zeros for non-existent evidence", async function () {
      const nonExistent = ethers.keccak256(ethers.toUtf8Bytes("non_existent"));
      const result = await evidenceLog.verifyEvidence(nonExistent);
      expect(result.exists).to.equal(false);
      expect(result.timestamp).to.equal(0);
      expect(result.reportHash).to.equal(ethers.ZeroHash);
      expect(result.perceptualHash).to.equal(ethers.ZeroHash);
    });
  });

  describe("getEvidence", function () {
    it("Should return complete evidence record with all fields", async function () {
      const tx = await logEvidenceFull(evidenceLog);
      const receipt = await tx.wait();
      const block = await ethers.provider.getBlock(receipt!.blockNumber);

      const evidence = await evidenceLog.getEvidence(sampleHash);
      expect(evidence.videoHash).to.equal(sampleHash);
      expect(evidence.cameraId).to.equal(sampleCameraId);
      expect(evidence.timestamp).to.equal(sampleTimestamp);
      expect(evidence.uploader).to.equal(owner.address);
      expect(evidence.blockNumber).to.equal(receipt!.blockNumber);
      expect(evidence.loggedAt).to.equal(block!.timestamp);
      expect(evidence.reportHash).to.equal(sampleReportHash);
      expect(evidence.perceptualHash).to.equal(samplePerceptualHash);
      expect(evidence.aiModelVersion).to.equal(sampleAiModel);
      expect(evidence.confidenceScore).to.equal(sampleConfidence);
      expect(evidence.eventType).to.equal(sampleEventType);
      expect(evidence.clipCloudURI).to.equal(sampleClipURI);
    });

    it("Should revert for non-existent evidence", async function () {
      const nonExistent = ethers.keccak256(ethers.toUtf8Bytes("non_existent"));
      await expect(evidenceLog.getEvidence(nonExistent)).to.be.revertedWith(
        "Evidence does not exist"
      );
    });
  });

  describe("Evidence enumeration", function () {
    it("Should track evidence count correctly", async function () {
      expect(await evidenceLog.getEvidenceCount()).to.equal(0);

      await logEvidenceFull(
        evidenceLog,
        ethers.keccak256(ethers.toUtf8Bytes("video1"))
      );
      expect(await evidenceLog.getEvidenceCount()).to.equal(1);

      await logEvidenceFull(
        evidenceLog,
        ethers.keccak256(ethers.toUtf8Bytes("video2")),
        "CAM-002"
      );
      expect(await evidenceLog.getEvidenceCount()).to.equal(2);
    });

    it("Should return correct hash at index", async function () {
      const hash1 = ethers.keccak256(ethers.toUtf8Bytes("video1"));
      const hash2 = ethers.keccak256(ethers.toUtf8Bytes("video2"));

      await logEvidenceFull(evidenceLog, hash1);
      await logEvidenceFull(evidenceLog, hash2, "CAM-002");

      expect(await evidenceLog.getEvidenceHashAtIndex(0)).to.equal(hash1);
      expect(await evidenceLog.getEvidenceHashAtIndex(1)).to.equal(hash2);
    });

    it("Should revert for out of bounds index", async function () {
      await expect(evidenceLog.getEvidenceHashAtIndex(0)).to.be.revertedWith(
        "Index out of bounds"
      );
    });
  });
});
