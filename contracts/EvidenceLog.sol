// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

/**
 * @title EvidenceLog
 * @dev Stores cryptographic hashes of CCTV video footage for tamper-proof verification
 * @notice Dual-hash verification: SHA-256 (byte-level file integrity) + K2A-Hash
 *         (perceptual, content-level similarity) with on-chain forensic report hash
 *         for VLM-based analysis provenance
 */
contract EvidenceLog {
    /// @dev Deployer address; the only account permitted to log evidence.
    ///      Immutable, so it occupies no storage slot and adds no SLOAD per write.
    address public immutable owner;

    modifier onlyOwner() {
        require(msg.sender == owner, "Caller is not the owner");
        _;
    }

    constructor() {
        owner = msg.sender;
    }

    struct Evidence {
        bytes32 videoHash;        // SHA-256 hash of the video file
        string cameraId;          // Unique identifier for the camera
        uint256 timestamp;        // Unix timestamp when footage was hashed
        address uploader;         // Address that logged the evidence
        uint256 blockNumber;      // Block number when evidence was logged
        uint256 loggedAt;         // Block timestamp when logged on-chain
        bytes32 reportHash;       // SHA-256 of forensic report JSON
        bytes32 perceptualHash;   // K2A-Hash of video (perceptual hash)
        string aiModelVersion;    // e.g. "gemini-1.5-flash"
        uint256 confidenceScore;  // 0-10000 (0.00%-100.00%)
        string eventType;         // "violence", "theft", etc.
        string clipCloudURI;      // local storage reference
    }

    // Mapping from video hash to Evidence struct
    mapping(bytes32 => Evidence) public evidenceRecords;

    // Array to track all evidence hashes for enumeration
    bytes32[] public evidenceHashes;

    // Events
    event EvidenceLogged(
        bytes32 indexed videoHash,
        string cameraId,
        uint256 timestamp,
        address indexed uploader,
        uint256 blockNumber,
        bytes32 reportHash,
        bytes32 perceptualHash,
        string eventType,
        uint256 confidenceScore
    );

    /**
     * @dev Logs a new piece of evidence on-chain
     * @param _videoHash SHA-256 hash of the video file (must be unique)
     * @param _cameraId Identifier of the camera that recorded the footage
     * @param _timestamp Unix timestamp when the footage was hashed
     * @param _reportHash SHA-256 hash of the forensic report (bytes32(0) if not available)
     * @param _perceptualHash K2A-Hash of video (bytes32(0) if not available)
     * @param _aiModelVersion AI model used for analysis (empty string if N/A)
     * @param _confidenceScore Detection confidence 0-10000
     * @param _eventType Type of detected event (empty string if N/A)
     * @param _clipCloudURI Cloud storage URI for the clip
     */
    function logEvidence(
        bytes32 _videoHash,
        string calldata _cameraId,
        uint256 _timestamp,
        bytes32 _reportHash,
        bytes32 _perceptualHash,
        string calldata _aiModelVersion,
        uint256 _confidenceScore,
        string calldata _eventType,
        string calldata _clipCloudURI
    ) external onlyOwner {
        require(_videoHash != bytes32(0), "Video hash cannot be empty");
        require(
            evidenceRecords[_videoHash].videoHash == bytes32(0),
            "Evidence already exists"
        );
        require(bytes(_cameraId).length > 0, "Camera ID cannot be empty");
        require(_timestamp > 0, "Timestamp must be positive");
        require(_confidenceScore <= 10000, "Confidence score must be <= 10000");

        evidenceRecords[_videoHash] = Evidence({
            videoHash: _videoHash,
            cameraId: _cameraId,
            timestamp: _timestamp,
            uploader: msg.sender,
            blockNumber: block.number,
            loggedAt: block.timestamp,
            reportHash: _reportHash,
            perceptualHash: _perceptualHash,
            aiModelVersion: _aiModelVersion,
            confidenceScore: _confidenceScore,
            eventType: _eventType,
            clipCloudURI: _clipCloudURI
        });

        evidenceHashes.push(_videoHash);

        emit EvidenceLogged(
            _videoHash,
            _cameraId,
            _timestamp,
            msg.sender,
            block.number,
            _reportHash,
            _perceptualHash,
            _eventType,
            _confidenceScore
        );
    }

    /**
     * @dev Verifies if a video hash exists on-chain and returns key details
     * @param _videoHash SHA-256 hash to verify
     * @return exists True if the hash exists
     * @return timestamp Unix timestamp when the footage was hashed
     * @return loggedAt Block timestamp when evidence was logged on-chain
     * @return cameraId The camera that recorded the footage
     * @return reportHash Hash of the forensic report
     * @return perceptualHash Perceptual hash of the video
     * @return eventType Type of detected event
     * @return confidenceScore Detection confidence
     */
    function verifyEvidence(bytes32 _videoHash) external view returns (
        bool exists,
        uint256 timestamp,
        uint256 loggedAt,
        string memory cameraId,
        bytes32 reportHash,
        bytes32 perceptualHash,
        string memory eventType,
        uint256 confidenceScore
    ) {
        Evidence memory evidence = evidenceRecords[_videoHash];
        if (evidence.videoHash == bytes32(0)) {
            return (false, 0, 0, "", bytes32(0), bytes32(0), "", 0);
        }
        return (
            true,
            evidence.timestamp,
            evidence.loggedAt,
            evidence.cameraId,
            evidence.reportHash,
            evidence.perceptualHash,
            evidence.eventType,
            evidence.confidenceScore
        );
    }

    /**
     * @dev Retrieves the full evidence record for a given hash
     * @param _videoHash SHA-256 hash to look up
     * @return evidence The complete Evidence struct
     */
    function getEvidence(bytes32 _videoHash) external view returns (Evidence memory evidence) {
        require(
            evidenceRecords[_videoHash].videoHash != bytes32(0),
            "Evidence does not exist"
        );
        return evidenceRecords[_videoHash];
    }

    /**
     * @dev Returns the total number of evidence records
     * @return count Total number of logged evidence
     */
    function getEvidenceCount() external view returns (uint256 count) {
        return evidenceHashes.length;
    }

    /**
     * @dev Returns evidence hash at a specific index
     * @param _index Index in the evidenceHashes array
     * @return hash The video hash at that index
     */
    function getEvidenceHashAtIndex(uint256 _index) external view returns (bytes32 hash) {
        require(_index < evidenceHashes.length, "Index out of bounds");
        return evidenceHashes[_index];
    }
}
