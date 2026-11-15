"""Compile contracts/RPSArena.sol with solc 0.8.24 (same settings as compile.js) -> artifacts/RPSArena.json.

Use this when Node.js isn't available:
    pip install py-solc-x && python -m scripts.compile
"""
import json

import solcx

from backend.chain import ROOT

VERSION = "0.8.24"


def main():
    if VERSION not in [str(v) for v in solcx.get_installed_solc_versions()]:
        solcx.install_solc(VERSION)
    source = (ROOT / "contracts" / "RPSArena.sol").read_text()
    out = solcx.compile_standard(
        {
            "language": "Solidity",
            "sources": {"RPSArena.sol": {"content": source}},
            "settings": {
                "optimizer": {"enabled": True, "runs": 200},
                "outputSelection": {"*": {"*": ["abi", "evm.bytecode.object", "evm.deployedBytecode.object"]}},
            },
        },
        solc_version=VERSION,
    )
    c = out["contracts"]["RPSArena.sol"]["RPSArena"]
    (ROOT / "artifacts").mkdir(exist_ok=True)
    (ROOT / "artifacts" / "RPSArena.json").write_text(
        json.dumps({"abi": c["abi"], "bytecode": "0x" + c["evm"]["bytecode"]["object"]}, indent=2)
    )
    size = len(c["evm"]["deployedBytecode"]["object"]) // 2
    print(f"Wrote artifacts/RPSArena.json (solc {VERSION}, runtime size {size} bytes of 24576)")


if __name__ == "__main__":
    main()
