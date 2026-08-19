// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "@uniswap/v4-core/src/types/PoolId.sol";
import {LPFeeLibrary} from "@uniswap/v4-core/src/libraries/LPFeeLibrary.sol";

import {BaseScript} from "./base/BaseScript.sol";
import {SentinelHookV0} from "../src/SentinelHookV0.sol";

/// @notice Demo choreography for the live SentinelHookV0 pool:
///   1. a calm swap that pays the base fee,
///   2. a large swap that moves the pool > ENTER_TICKS (it still pays the
///      pre-swap fee — a trade cannot set its own fee),
///   3. five small swaps during which the fee ramps toward ELEVATED_FEE,
///      rate-limited to MAX_FEE_STEP per update, emitting FeeUpdated events.
contract DemoSwapsScript is BaseScript {
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
        SentinelHookV0 hook = SentinelHookV0(address(hookContract));

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

        // 2. Large swap — moves the pool well past ENTER_TICKS. Still pays
        //    the pre-swap fee: its own movement cannot price itself.
        swapRouter.swapExactTokensForTokens({
            amountIn: 2e18,
            amountOutMin: 0,
            zeroForOne: true,
            poolKey: poolKey,
            hookData: hookData,
            receiver: deployerAddress,
            deadline: block.timestamp + 300
        });

        // 3. Five small swaps — the fee ramps up, one bounded step at a time.
        for (uint256 i = 0; i < 5; i++) {
            swapRouter.swapExactTokensForTokens({
                amountIn: 0.02e18,
                amountOutMin: 0,
                zeroForOne: i % 2 == 1,
                poolKey: poolKey,
                hookData: hookData,
                receiver: deployerAddress,
                deadline: block.timestamp + 300
            });
        }

        vm.stopBroadcast();

        // Sanity check: after the ramp the pool must sit at ELEVATED_FEE.
        require(hook.getCurrentFee(poolKey.toId()) == hook.ELEVATED_FEE(), "demo: fee did not reach elevated tier");
    }
}
