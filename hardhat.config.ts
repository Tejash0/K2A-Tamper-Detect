import { HardhatUserConfig } from "hardhat/config";
import "@nomicfoundation/hardhat-toolbox";
import "dotenv/config";

const DEPLOYER_PRIVATE_KEY = process.env.DEPLOYER_PRIVATE_KEY || "";

// Amoy deliberately does NOT fall back to DEPLOYER_PRIVATE_KEY. dev.sh writes
// Hardhat's deterministic account #0 there for local work, and that key is
// public. logEvidence() is onlyOwner over an immutable owner with no transfer
// function, so deploying publicly with it would let anyone write evidence to
// the contract, permanently.
const AMOY_DEPLOYER_PRIVATE_KEY = process.env.AMOY_DEPLOYER_PRIVATE_KEY || "";

// rpc-amoy.polygon.technology no longer resolves.
const POLYGON_AMOY_RPC_URL =
  process.env.POLYGON_AMOY_RPC_URL ||
  "https://polygon-amoy-bor-rpc.publicnode.com";

const config: HardhatUserConfig = {
  solidity: {
    version: "0.8.28",
    settings: {
      viaIR: true,
      optimizer: {
        enabled: true,
        runs: 200,
      },
    },
  },
  networks: {
    localhost: {
      url: "http://127.0.0.1:8545",
    },
    hardhat: {
      chainId: 31337,
    },
    amoy: {
      url: POLYGON_AMOY_RPC_URL,
      accounts: AMOY_DEPLOYER_PRIVATE_KEY ? [AMOY_DEPLOYER_PRIVATE_KEY] : [],
      chainId: 80002,
    },
  },
};

export default config;
