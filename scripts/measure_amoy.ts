/**
 * Re-derive every Polygon Amoy figure the paper quotes, from the live chain.
 *
 *   npx hardhat run scripts/measure_amoy.ts --network amoy
 *
 * Read-only. It needs an RPC endpoint but no private key, so anyone can run it
 * against the published address and check the numbers for themselves. That is
 * the point of it: before this existed, the Amoy deployment gas, the write gas,
 * the verification latency and the bytecode-provenance claim had no artifact in
 * the repository at all, and could not be reproduced by a reader or by us.
 *
 * Nothing here is hardcoded except the deployment record on disk. The address
 * comes from ignition/deployments/chain-80002/deployed_addresses.json, the
 * deployment transaction from that deployment's journal, and the evidence
 * writes from the contract's own EvidenceLogged events.
 */

import fs from "node:fs";
import path from "node:path";
import { ethers, network } from "hardhat";

const DEPLOYMENT_DIR = path.join(
  __dirname,
  "..",
  "ignition",
  "deployments",
  "chain-80002"
);
const RESULTS_CSV = path.join(__dirname, "..", "experiments", "results.csv");
const LATENCY_SAMPLES = 15;

/**
 * The journal is newline-delimited JSON. Some Ignition versions additionally
 * prefix each record with an ASCII record separator, so split on both rather
 * than assuming which one wrote this file.
 */
function readJournal(file: string): any[] {
  return fs
    .readFileSync(file, "utf8")
    .split(/[\x1e\n]+/)
    .map((s) => s.trim())
    .filter(Boolean)
    .map((s) => JSON.parse(s));
}

function median(xs: number[]): number {
  const s = [...xs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

function stdev(xs: number[]): number {
  if (xs.length < 2) return 0;
  const mean = xs.reduce((a, b) => a + b, 0) / xs.length;
  const v =
    xs.reduce((a, b) => a + (b - mean) * (b - mean), 0) / (xs.length - 1);
  return Math.sqrt(v);
}

/**
 * Solidity appends a CBOR metadata blob to the runtime code, with its length in
 * the final two bytes. Identical metadata means identical source and compiler
 * settings, so it is the part of the comparison that carries the provenance.
 */
function splitMetadata(hexNo0x: string): { code: string; metadata: string } {
  const bytes = Buffer.from(hexNo0x, "hex");
  const len = bytes.readUInt16BE(bytes.length - 2);
  const start = bytes.length - 2 - len;
  if (start <= 0 || len === 0) return { code: hexNo0x, metadata: "" };
  return {
    code: bytes.subarray(0, start).toString("hex"),
    metadata: bytes.subarray(start).toString("hex"),
  };
}

/** Byte offsets at which two equal-length hex strings differ. */
function differingByteOffsets(a: string, b: string): number[] {
  const out: number[] = [];
  for (let i = 0; i < a.length; i += 2) {
    if (a.slice(i, i + 2) !== b.slice(i, i + 2)) out.push(i / 2);
  }
  return out;
}

/** Contiguous runs, so a 20-byte inlined address reads as one span. */
function toRuns(offsets: number[]): Array<[number, number]> {
  const runs: Array<[number, number]> = [];
  for (const o of offsets) {
    const last = runs[runs.length - 1];
    if (last && o === last[1] + 1) last[1] = o;
    else runs.push([o, o]);
  }
  return runs;
}

async function main() {
  if (network.config.chainId !== 80002) {
    throw new Error(
      `Expected Polygon Amoy (chainId 80002), got ${network.config.chainId}. ` +
        `Run with --network amoy.`
    );
  }

  const address = JSON.parse(
    fs.readFileSync(
      path.join(DEPLOYMENT_DIR, "deployed_addresses.json"),
      "utf8"
    )
  )["EvidenceLogModule#EvidenceLog"];

  console.log(`Contract        ${address}`);
  console.log(`Network         ${network.name} (chainId 80002)\n`);

  const provider = ethers.provider;

  // ---- 1. Deployment gas, from the deployment transaction receipt ----------
  const journal = readJournal(path.join(DEPLOYMENT_DIR, "journal.jsonl"));
  const deployHash = journal.find((r) => r.receipt?.contractAddress)?.hash;
  if (!deployHash) throw new Error("No deployment receipt found in journal");

  const deployReceipt = await provider.getTransactionReceipt(deployHash);
  if (!deployReceipt) {
    throw new Error(
      `Deployment tx ${deployHash} not found. The RPC endpoint may not retain ` +
        `state this old; try an archive endpoint via POLYGON_AMOY_RPC_URL.`
    );
  }
  console.log("-- Deployment --");
  console.log(`  tx            ${deployHash}`);
  console.log(`  block         ${deployReceipt.blockNumber.toLocaleString()}`);
  console.log(`  gas used      ${deployReceipt.gasUsed.toLocaleString()}`);

  // ---- 2. Evidence writes, from the contract's own events ------------------
  // Public Amoy endpoints cap eth_getLogs at a 10,000-block range, so page from
  // the deployment block rather than asking for the whole chain at once. There
  // can be no EvidenceLogged event before the contract existed.
  const contract = await ethers.getContractAt("EvidenceLog", address);
  const filter = contract.filters.EvidenceLogged();
  const latest = await provider.getBlockNumber();
  const WINDOW = 10_000;

  const logged: Awaited<ReturnType<typeof contract.queryFilter>> = [];
  for (let from = deployReceipt.blockNumber; from <= latest; from += WINDOW) {
    const to = Math.min(from + WINDOW - 1, latest);
    logged.push(...(await contract.queryFilter(filter, from, to)));
  }

  console.log(`\n-- Evidence writes (${logged.length}) --`);
  const writeGas: number[] = [];
  for (const ev of logged) {
    const r = await provider.getTransactionReceipt(ev.transactionHash);
    if (!r) continue;
    writeGas.push(Number(r.gasUsed));
    const block = await provider.getBlock(r.blockNumber);
    console.log(`  videoHash     ${ev.args.videoHash}`);
    console.log(`  perceptual    ${ev.args.perceptualHash}`);
    console.log(`  gas used      ${r.gasUsed.toLocaleString()}`);
    console.log(`  block         ${r.blockNumber.toLocaleString()}`);
    if (block) {
      console.log(
        `  mined         ${new Date(block.timestamp * 1000).toISOString()}`
      );
    }
  }

  // ---- 3. Verification latency --------------------------------------------
  if (logged.length > 0) {
    const target = logged[0].args.videoHash;
    console.log(`\n-- verifyEvidence() latency, ${LATENCY_SAMPLES} calls --`);
    // One untimed call so DNS, TLS and the connection pool are warm and we time
    // the read rather than the handshake.
    await contract.verifyEvidence(target);

    const times: number[] = [];
    for (let i = 0; i < LATENCY_SAMPLES; i++) {
      const t0 = performance.now();
      await contract.verifyEvidence(target);
      times.push(performance.now() - t0);
    }
    const mean = times.reduce((a, b) => a + b, 0) / times.length;
    console.log(`  median        ${median(times).toFixed(0)} ms`);
    console.log(`  mean          ${mean.toFixed(0)} ms`);
    console.log(`  s.d.          ${stdev(times).toFixed(0)} ms`);
    console.log(
      `  min/max       ${Math.min(...times).toFixed(0)} / ${Math.max(
        ...times
      ).toFixed(0)} ms`
    );

    // ---- 4. Stored hash against the published results.csv ------------------
    const stored = (await contract.getEvidence(target)).perceptualHash;
    if (fs.existsSync(RESULTS_CSV)) {
      const lines = fs.readFileSync(RESULTS_CSV, "utf8").trim().split("\n");
      const header = lines[0].split(",");
      const kCol = header.indexOf("k2a_original");
      const nCol = header.indexOf("filename");
      const hit = lines
        .slice(1)
        .map((l) => l.split(","))
        .find((c) => c[kCol]?.toLowerCase() === stored.toLowerCase());
      console.log(`\n-- Stored perceptual hash vs results.csv --`);
      console.log(`  on-chain      ${stored}`);
      console.log(
        hit
          ? `  MATCH         ${hit[nCol]}`
          : `  NO MATCH      not present in results.csv`
      );
    }
  }

  // ---- 5. Bytecode provenance ---------------------------------------------
  const artifact = JSON.parse(
    fs.readFileSync(
      path.join(
        DEPLOYMENT_DIR,
        "artifacts",
        "EvidenceLogModule#EvidenceLog.json"
      ),
      "utf8"
    )
  );
  const onChain = (await provider.getCode(address)).slice(2).toLowerCase();
  const local = artifact.deployedBytecode.slice(2).toLowerCase();

  console.log(`\n-- Bytecode provenance --`);
  console.log(`  on-chain      ${onChain.length / 2} bytes`);
  console.log(`  artifact      ${local.length / 2} bytes`);

  if (onChain.length !== local.length) {
    console.log(`  RESULT        LENGTH MISMATCH - not the same contract`);
    process.exitCode = 1;
    return;
  }

  const a = splitMetadata(onChain);
  const b = splitMetadata(local);
  console.log(
    `  metadata      ${a.metadata === b.metadata ? "IDENTICAL" : "DIFFERS"}` +
      ` (same source and compiler settings)`
  );

  const runs = toRuns(differingByteOffsets(a.code, b.code));
  if (runs.length === 0) {
    console.log(`  code          IDENTICAL`);
  } else {
    const owner = (await contract.owner()).slice(2).toLowerCase();
    console.log(`  code          ${runs.length} differing span(s):`);
    for (const [s, e] of runs) {
      const span = a.code.slice(s * 2, (e + 1) * 2);
      const isOwner = span.includes(owner);
      console.log(
        `    bytes ${s}-${e} (${e - s + 1})  ` +
          (isOwner
            ? `immutable owner, inlined at construction - expected`
            : `UNEXPECTED: 0x${span}`)
      );
    }
  }
}

main().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
