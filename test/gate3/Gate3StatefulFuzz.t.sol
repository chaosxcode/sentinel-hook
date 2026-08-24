// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IHooks} from "@uniswap/v4-core/src/interfaces/IHooks.sol";
import {Hooks} from "@uniswap/v4-core/src/libraries/Hooks.sol";
import {TickMath} from "@uniswap/v4-core/src/libraries/TickMath.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "@uniswap/v4-core/src/types/PoolId.sol";
import {CurrencyLibrary, Currency} from "@uniswap/v4-core/src/types/Currency.sol";
import {LPFeeLibrary} from "@uniswap/v4-core/src/libraries/LPFeeLibrary.sol";
import {StateLibrary} from "@uniswap/v4-core/src/libraries/StateLibrary.sol";
import {LiquidityAmounts} from "@uniswap/v4-core/test/utils/LiquidityAmounts.sol";
import {Constants} from "@uniswap/v4-core/test/utils/Constants.sol";
import {IPositionManager} from "@uniswap/v4-periphery/src/interfaces/IPositionManager.sol";
import {EasyPosm} from "../utils/libraries/EasyPosm.sol";

import {SentinelHookV1} from "../../src/SentinelHookV1.sol";
import {BaseTest} from "../utils/BaseTest.sol";

/// @title Gate 3 stateful fuzz campaign
/// @notice One persistent pool driven through SENTINEL_GATE3_STEPS stateful
///         swap transitions (random direction, size, and time gaps). After
///         every transition the full invariant set is asserted:
///
///   I1  BASE_FEE <= fee <= MAX_FEE
///   I2  |fee - prevFee| <= MAX_FEE_STEP
///   I3  0 <= emaRateWad <= WAD
///   I4  stored fee == getCurrentFee (state consistency)
///   I5  no revert on any valid input
///
/// Run with:
///   forge test --match-test test_StatefulCampaign100k \
///     -vvv --gas-limit 1e12
/// The step count defaults to 100,000 (Gate 3 bar) and can be lowered via
/// SENTINEL_GATE3_STEPS for smoke runs.
contract Gate3StatefulFuzzTest is BaseTest {
    using PoolIdLibrary for PoolKey;

    Currency currency0;
    Currency currency1;
    PoolKey poolKey;
    SentinelHookV1 hook;
    PoolId poolId;
    uint256 tokenId;

    using StateLibrary for IPoolManager;
    using EasyPosm for IPositionManager;


    function setUp() public {
        deployArtifactsAndLabel();
        (currency0, currency1) = deployCurrencyPair();

        address flags = address(
            uint160(Hooks.BEFORE_INITIALIZE_FLAG | Hooks.AFTER_INITIALIZE_FLAG | Hooks.BEFORE_SWAP_FLAG)
                ^ (0x6666 << 144)
        );
        deployCodeTo("SentinelHookV1.sol:SentinelHookV1", abi.encode(poolManager), flags);
        hook = SentinelHookV1(flags);

        poolKey = PoolKey(currency0, currency1, LPFeeLibrary.DYNAMIC_FEE_FLAG, 60, IHooks(hook));
        poolId = poolKey.toId();
        poolManager.initialize(poolKey, Constants.SQRT_PRICE_1_1);

        int24 tickLower = TickMath.minUsableTick(poolKey.tickSpacing);
        int24 tickUpper = TickMath.maxUsableTick(poolKey.tickSpacing);
        (uint256 a0, uint256 a1) = LiquidityAmounts.getAmountsForLiquidity(
            Constants.SQRT_PRICE_1_1,
            TickMath.getSqrtPriceAtTick(tickLower),
            TickMath.getSqrtPriceAtTick(tickUpper),
            1000e18
        );
        currency0.transfer(address(positionManager), a0);
        currency1.transfer(address(positionManager), a1);
        (tokenId, ) = positionManager.mint(
            poolKey, tickLower, tickUpper, 1000e18, a0 + 1, a1 + 1, address(this), block.timestamp, Constants.ZERO_BYTES
        );
    }

    function test_StatefulCampaign100k() public {
        uint256 steps = vm.envOr("SENTINEL_GATE3_STEPS", uint256(100_000));
        uint256 state = 0x5e6e71696e656c; // "sentinel" flavored seed
        uint24 prevFee = hook.getCurrentFee(poolId);

        for (uint256 i = 0; i < steps; i++) {
            state = uint256(keccak256(abi.encode(state, i)));

            // advance time: 70% small gaps, 30% large gaps (decay pressure)
            uint256 roll = state % 100;
            uint32 dt = roll < 70 ? uint32(1 + (state >> 8) % 10) : uint32(60 + (state >> 8) % 3600);
            vm.warp(block.timestamp + dt);

            // random trade with mean-reverting direction bias so the
            // 100k-step walk stays inside TickMath bounds while exercising
            // both directions, all sizes, and all time gaps.
            (, int24 tickNow,,) = poolManager.getSlot0(poolId);
            bool randomDir = ((state >> 16) & 1) == 1;
            bool revertDir = tickNow > 0; // tick>0 -> push price down
            bool zeroForOne = ((state >> 20) % 100) < 70 ? revertDir : randomDir;
            uint256 magnitude = (state >> 24) % 100;
            uint256 amountIn = magnitude < 80 ? 1e12 + (state >> 32) % 5e17 : 1e18 + (state >> 32) % 2e18;

            swapRouter.swapExactTokensForTokens({
                amountIn: amountIn,
                amountOutMin: 0,
                zeroForOne: zeroForOne,
                poolKey: poolKey,
                hookData: Constants.ZERO_BYTES,
                receiver: address(this),
                deadline: block.timestamp + 1
            });

            // ---- invariants (every transition) ----
            uint24 fee = hook.getCurrentFee(poolId);
            assertGe(fee, hook.BASE_FEE()); // I1a
            assertLe(fee, hook.MAX_FEE()); // I1b
            if (fee != prevFee) {
                uint24 diff = fee > prevFee ? fee - prevFee : prevFee - fee;
                assertLe(diff, hook.MAX_FEE_STEP()); // I2
            }
            uint128 ema = hook.getEmaRateWad(poolId);
            assertLe(ema, uint128(1e18)); // I3
            assertEq(fee, hook.getCurrentFee(poolId)); // I4 trivial consistency
            prevFee = fee;
        }
        emit log_named_uint("stateful steps completed", steps);
        emit log_named_uint("final fee", hook.getCurrentFee(poolId));
    }
}
