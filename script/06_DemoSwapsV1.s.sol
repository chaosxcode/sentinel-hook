// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "@uniswap/v4-core/src/types/PoolId.sol";
import {LPFeeLibrary} from "@uniswap/v4-core/src/libraries/LPFeeLibrary.sol";
import {console2} from "forge-std/console2.sol";

import {BaseScript} from "./base/BaseScript.sol";
import {SentinelHookV1} from "../src/SentinelHookV1.sol";

/// @notice Demo choreography for the live SentinelHookV1 pool:
///   1. a calm swap — fee stays at the 0.05% base,
///   2. a sustained one-directional trend — realized volatility over the
///      60-second lookback feeds the EMA and the fee ramps toward the 1.00%
///      cap, rate-limited to 0.05% per update, one FeeUpdated event per step.
///   3. quiet — the EMA decays and the fee eases back toward base.
contract DemoSwapsV1Script is BaseScript {
    using PoolIdLibrary for PoolKey;

    function run() external {
        PoolKey memory poolKey = PoolKey({
            currency0: currency0,
            currency1: currency1,
            fee: LPFeeLibrary.DYNAMIC_FEE_FLAG,
            tickSpacing: 60,
            hooks: hookContract
        });
        bytes memory hookData = new bytes(0);
        SentinelHookV1 hook = SentinelHookV1(address(hookContract));
        PoolId poolId = poolKey.toId();

        vm.startBroadcast();

        token0.approve(address(swapRouter), type(uint256).max);
        token1.approve(address(swapRouter), type(uint256).max);

        // 1. Calm swap — pays the base fee.
        swapRouter.swapExactTokensForTokens({
            amountIn: 0.02e18,
            amountOutMin: 0,
            zeroForOne: true,
            poolKey: poolKey,
            hookData: hookData,
            receiver: deployerAddress,
            deadline: block.timestamp + 300
        });
        console2.log("1) calm swap fee:", hook.getCurrentFee(poolId));

        // 2. Sustained trend: repeated same-direction swaps. Each pays the
        // fee set BEFORE it executes; the EMA feeds on realized moves.
        uint24 prev = hook.getCurrentFee(poolId);
        for (uint256 i = 0; i < 45; i++) {
            vm.warp(block.timestamp + 3);
            swapRouter.swapExactTokensForTokens({
                amountIn: 0.1e18,
                amountOutMin: 0,
                zeroForOne: false,
                poolKey: poolKey,
                hookData: hookData,
                receiver: deployerAddress,
                deadline: block.timestamp + 300
            });
            uint24 fee = hook.getCurrentFee(poolId);
            if (fee != prev) {
                console2.log("2) ramp step:", fee);
                prev = fee;
            }
        }
        require(prev > hook.BASE_FEE(), "demo: fee did not ramp");
        require(prev <= hook.CAP_FEE(), "demo: fee above cap");

        // 3. Quiet period: EMA decays; fee steps back down.
        vm.warp(block.timestamp + 2 hours);
        for (uint256 i = 0; i < 45; i++) {
            vm.warp(block.timestamp + 3);
            swapRouter.swapExactTokensForTokens({
                amountIn: 0.001e18,
                amountOutMin: 0,
                zeroForOne: true,
                poolKey: poolKey,
                hookData: hookData,
                receiver: deployerAddress,
                deadline: block.timestamp + 300
            });
            uint24 fee = hook.getCurrentFee(poolId);
            if (fee != prev) {
                console2.log("3) decay step:", fee);
                prev = fee;
            }
        }
        console2.log("final fee:", prev);
        console2.log("ema (wad):", hook.getEmaRateWad(poolId));

        vm.stopBroadcast();
    }
}
