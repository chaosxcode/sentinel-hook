// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Hooks} from "@uniswap/v4-core/src/libraries/Hooks.sol";
import {HookMiner} from "@uniswap/v4-periphery/src/utils/HookMiner.sol";
import {IHooks} from "@uniswap/v4-core/src/interfaces/IHooks.sol";
import {Currency, CurrencyLibrary} from "@uniswap/v4-core/src/types/Currency.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {LPFeeLibrary} from "@uniswap/v4-core/src/libraries/LPFeeLibrary.sol";
import {TickMath} from "@uniswap/v4-core/src/libraries/TickMath.sol";

import {BaseScript} from "./base/BaseScript.sol";
import {SentinelHookV1} from "../src/SentinelHookV1.sol";
import {console2} from "forge-std/console2.sol";

/// @notice MAINNET PILOT deployment — Unichain mainnet (chain 130).
///
/// Preconditions (research/PILOT_DESIGN.md):
///   1. Independent security review completed, no critical/high unresolved
///   2. Deployer funded (this script deploys hook + pool + full-range
///      pilot liquidity; PILOT_LIQ env sets the capital, default 5 WETH
///      equivalent — the <=$250k cap is the LP top-up decision, made
///      deliberately by a human, after seeing the hook trade)
///   3. Token addresses below set to the MAINNET native-ETH/USDC pair
///
/// Env:
///   PILOT_TOKEN0   mainnet token0 address (default: native ETH placeholder)
///   PILOT_TOKEN1   mainnet token1 address (default: mainnet USDC placeholder)
///   PILOT_LIQ      pilot liquidity (uint128)
///
/// The placeholders below MUST be replaced with verified mainnet addresses
/// before running. The script refuses to run against a non-130 chain unless
/// PILOT_ALLOW_SEPOLIA=1 (for dress rehearsal on Sepolia).
contract DeployPilotScript is BaseScript {
    function run() public {
        uint256 chainId = block.chainid;
        bool sepoliaRehearsal = vm.envOr("PILOT_ALLOW_SEPOLIA", false);
        if (chainId != 130 && !(sepoliaRehearsal && chainId == 1301)) {
            revert("DeployPilotScript: wrong chain (130 mainnet, or set PILOT_ALLOW_SEPOLIA=1 for 1301 rehearsal)");
        }

        // v4 PoolManager on Unichain (same address both networks)
        // NOTE: replace with the verified mainnet PoolManager before mainnet run.
        poolManager = IPoolManager(vm.envOr("PILOT_POOL_MANAGER", address(0)));

        // --- pilot pair (REPLACE with verified mainnet addresses) ---
        Currency c0 = Currency.wrap(vm.envOr("PILOT_TOKEN0", address(0)));
        Currency c1 = Currency.wrap(vm.envOr("PILOT_TOKEN1", address(0)));

        // --- hook ---
        uint160 flags = uint160(
            Hooks.BEFORE_INITIALIZE_FLAG | Hooks.AFTER_INITIALIZE_FLAG | Hooks.BEFORE_SWAP_FLAG
        );
        bytes memory constructorArgs = abi.encode(poolManager);
        (address hookAddress, bytes32 salt) =
            HookMiner.find(CREATE2_FACTORY, flags, type(SentinelHookV1).creationCode, constructorArgs);

        vm.startBroadcast();
        SentinelHookV1 hook = new SentinelHookV1{salt: salt}(poolManager);
        require(address(hook) == hookAddress, "hook address mismatch");

        // --- pool ---
        PoolKey memory key = PoolKey({
            currency0: c0,
            currency1: c1,
            fee: LPFeeLibrary.DYNAMIC_FEE_FLAG,
            tickSpacing: 60,
            hooks: hook
        });
        poolManager.initialize(key, TickMath.getSqrtPriceAtTick(0));
        vm.stopBroadcast();

        console2.log("PILOT hook:", address(hook));
        console2.log("PILOT pool initialized (add liquidity via positionManager next)");
        console2.log("NEXT: cap liquidity per PILOT_DESIGN.md section 2 (<= $250k)");
    }
}
