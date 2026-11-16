// Compiles contracts/RPSArena.sol with solcjs and writes artifacts/RPSArena.json.
// Usage: npm install solc@0.8.24 && node scripts/compile.js
const fs = require("fs");
const path = require("path");
const solc = require("solc");

const root = path.join(__dirname, "..");
const source = fs.readFileSync(path.join(root, "contracts", "RPSArena.sol"), "utf8");

const input = {
  language: "Solidity",
  sources: { "RPSArena.sol": { content: source } },
  settings: {
    optimizer: { enabled: true, runs: 200 },
    outputSelection: { "*": { "*": ["abi", "evm.bytecode.object"] } },
  },
};

const output = JSON.parse(solc.compile(JSON.stringify(input)));
const errors = (output.errors || []).filter((e) => e.severity === "error");
(output.errors || []).forEach((e) => console.error(e.formattedMessage));
if (errors.length) process.exit(1);

const c = output.contracts["RPSArena.sol"].RPSArena;
fs.mkdirSync(path.join(root, "artifacts"), { recursive: true });
fs.writeFileSync(
  path.join(root, "artifacts", "RPSArena.json"),
  JSON.stringify({ abi: c.abi, bytecode: "0x" + c.evm.bytecode.object }, null, 2)
);
console.log("Wrote artifacts/RPSArena.json (solc " + solc.version() + ")");
