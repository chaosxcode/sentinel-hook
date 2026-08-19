// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IHooks} from "@uniswap/v4-core/src/interfaces/IHooks.sol";
import {Hooks} from "@uniswap/v4-core/src/libraries/Hooks.sol";
import {TickMath} from "@uniswap/v4-core/src/libraries/TickMath.sol";
import {IPoolManager, SwapParams} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "@uniswap/v4-core/src/types/PoolId.sol";
import {CurrencyLibrary, Currency} from "@uniswap/v4-core/src/types/Currency.sol";
import {StateLibrary} from "@uniswap/v4-core/src/libraries/StateLibrary.sol";
import {LPFeeLibrary} from "@uniswap/v4-core/src/libraries/LPFeeLibrary.sol";
import {LiquidityAmounts} from "@uniswap/v4-core/test/utils/LiquidityAmounts.sol";
import {IPositionManager} from "@uniswap/v4-periphery/src/interfaces/IPositionManager.sol";
import {Constants} from "@uniswap/v4-core/test/utils/Constants.sol";
import {BeforeSwapDelta} from "@uniswap/v4-core/src/types/BeforeSwapDelta.sol";

import {EasyPosm} from "./utils/libraries/EasyPosm.sol";

import {SentinelHookV0} from "../src/SentinelHookV0.sol";
import {BaseTest} from "./utils/BaseTest.sol";

contract SentinelHookV0Test is BaseTest {
    using EasyPosm for IPositionManager;
    using PoolIdLibrary for PoolKey;
    using CurrencyLibrary for Currency;
    using StateLibrary for IPoolManager;

    Currency currency0;
    Currency currency1;

    PoolKey poolKey;

    SentinelHookV0 hook;
    PoolId poolId;

    uint256 tokenId;

    function setUp() public {
        deployArtifactsAndLabel();

        (currency0, currency1) = deployCurrencyPair();

        address flags = address(
            uint160(Hooks.BEFORE_INITIALIZE_FLAG | Hooks.AFTER_INITIALIZE_FLAG | Hooks.BEFORE_SWAP_FLAG)
                ^ (0x4444 << 144) // Namespace the hook to avoid collisions
        );
        bytes memory constructorArgs = abi.encode(poolManager);
        deployCodeTo("SentinelHookV0.sol:SentinelHookV0", constructorArgs, flags);
        hook = SentinelHookV0(flags);

        poolKey = PoolKey(currency0, currency1, LPFeeLibrary.DYNAMIC_FEE_FLAG, 60, IHooks(hook));
        poolId = poolKey.toId();
        poolManager.initialize(poolKey, Constants.SQRT_PRICE_1_1);

        int24 tickLower = TickMath.minUsableTick(poolKey.tickSpacing);
        int24 tickUpper = TickMath.maxUsableTick(poolKey.tickSpacing);
        uint128 liquidityAmount = 100e18;

        (uint256 amount0Expected, uint256 amount1Expected) = LiquidityAmounts.getAmountsForLiquidity(
            Constants.SQRT_PRICE_1_1,
            TickMath.getSqrtPriceAtTick(tickLower),
            TickMath.getSqrtPriceAtTick(tickUpper),
            liquidityAmount
        );

        (tokenId,) = positionManager.mint(
            poolKey,
            tickLower,
            tickUpper,
            liquidityAmount,
            amount0Expected + 1,
            amount1Expected + 1,
            address(this),
            block.timestamp,
            Constants.ZERO_BYTES
        );
    }

    // ------------------------------------------------------------------
    // helpers
    // ------------------------------------------------------------------

    function _swap(bool zeroForOne, uint256 amountIn) internal {
        swapRouter.swapExactTokensForTokens({
            amountIn: amountIn,
            amountOutMin: 0,
            zeroForOne: zeroForOne,
            poolKey: poolKey,
            hookData: Constants.ZERO_BYTES,
            receiver: address(this),
            deadline: block.timestamp + 1
        });
    }

    function _currentFee() internal view returns (uint24) {
        return hook.getCurrentFee(poolId);
    }

    // ------------------------------------------------------------------
    // initialization guards
    // ------------------------------------------------------------------

    function test_RevertsOnStaticFeePool() public {
        PoolKey memory staticKey = PoolKey(currency0, currency1, 3000, 60, IHooks(hook));
        vm.expectRevert();
        poolManager.initialize(staticKey, Constants.SQRT_PRICE_1_1);
    }

    function test_InitialStateIsBaseFee() public view {
        (bool initialized,,,, uint24 fee) = hook.poolState(poolId);
        assertTrue(initialized);
        assertEq(fee, hook.BASE_FEE());
    }

    // ------------------------------------------------------------------
    // fee behavior
    // ------------------------------------------------------------------

    function test_CalmSwapsStayAtBaseFee() public {
        _swap(true, 0.01e18); // ~2 ticks of movement
        _swap(false, 0.01e18);
        assertEq(_currentFee(), hook.BASE_FEE());
    }

    function test_BigMoveElevatesFee_RateLimited() public {
        // The big swap itself pays the base fee: its own movement cannot set
        // its own fee (pre-swap observation only).
        _swap(true, 1e18); // ~200 ticks of movement
        assertEq(_currentFee(), hook.BASE_FEE());

        // Subsequent swaps see the movement and ramp the fee up, at most
        // MAX_FEE_STEP per update, toward ELEVATED_FEE.
        uint24 prev = _currentFee();
        for (uint256 i = 0; i < 5; i++) {
            _swap(false, 0.005e18);
            uint24 fee = _currentFee();
            assertLe(fee - prev, hook.MAX_FEE_STEP());
            assertGe(fee, prev);
            prev = fee;
        }
        assertEq(prev, hook.ELEVATED_FEE());
    }

    function test_CooldownAndHysteresisEaseBack() public {
        // Elevate fully.
        _swap(true, 1e18);
        for (uint256 i = 0; i < 5; i++) {
            _swap(false, 0.005e18);
        }
        assertEq(_currentFee(), hook.ELEVATED_FEE());

        // Calm swap before the cooldown expires: fee must hold, not bounce.
        _swap(true, 0.005e18);
        assertEq(_currentFee(), hook.ELEVATED_FEE());

        // After the cooldown, calm conditions ease the fee back down, rate
        // limited on every step.
        vm.warp(block.timestamp + hook.COOLDOWN() + 1);
        uint24 prev = _currentFee();
        for (uint256 i = 0; i < 6; i++) {
            _swap(i % 2 == 0, 0.005e18);
            uint24 fee = _currentFee();
            assertLe(prev - fee, hook.MAX_FEE_STEP());
            assertLe(fee, prev);
            prev = fee;
        }
        assertEq(prev, hook.BASE_FEE());
    }

    function test_StaleStateRecalibratesTowardBase() public {
        _swap(true, 1e18);
        _swap(false, 0.005e18);
        assertGt(_currentFee(), hook.BASE_FEE());

        // Long quiet period: the movement signal is stale and distrusted, so
        // the hook recalibrates toward the conservative base fee.
        vm.warp(block.timestamp + hook.STALE_AFTER() + 1);
        for (uint256 i = 0; i < 6; i++) {
            _swap(i % 2 == 0, 0.005e18);
        }
        assertEq(_currentFee(), hook.BASE_FEE());
    }

    function test_UnknownPoolFallsBackToBaseFee() public {
        // A direct call for a pool this hook never initialized must return the
        // safe base fee (with the override flag) and write no state.
        PoolKey memory unknownKey =
            PoolKey(currency0, currency1, LPFeeLibrary.DYNAMIC_FEE_FLAG, 120, IHooks(hook));
        SwapParams memory params = SwapParams({zeroForOne: true, amountSpecified: -1e18, sqrtPriceLimitX96: 0});

        vm.prank(address(poolManager));
        (,, uint24 fee) = hook.beforeSwap(address(this), unknownKey, params, Constants.ZERO_BYTES);

        assertEq(fee, hook.BASE_FEE() | LPFeeLibrary.OVERRIDE_FEE_FLAG);
        (bool initialized,,,,) = hook.poolState(unknownKey.toId());
        assertFalse(initialized);
    }

    function test_GasOverheadVsNoHookPool() public {
        // Identical pool with no hook, as the baseline.
        PoolKey memory plainKey = PoolKey(currency0, currency1, 3000, 60, IHooks(address(0)));
        poolManager.initialize(plainKey, Constants.SQRT_PRICE_1_1);
        int24 tickLower = TickMath.minUsableTick(plainKey.tickSpacing);
        int24 tickUpper = TickMath.maxUsableTick(plainKey.tickSpacing);
        (uint256 a0, uint256 a1) = LiquidityAmounts.getAmountsForLiquidity(
            Constants.SQRT_PRICE_1_1,
            TickMath.getSqrtPriceAtTick(tickLower),
            TickMath.getSqrtPriceAtTick(tickUpper),
            100e18
        );
        positionManager.mint(
            plainKey, tickLower, tickUpper, 100e18, a0 + 1, a1 + 1, address(this), block.timestamp, Constants.ZERO_BYTES
        );

        // Warm both pools once so storage-access costs are comparable.
        _swap(true, 0.01e18);
        swapRouter.swapExactTokensForTokens(
            0.01e18, 0, true, plainKey, Constants.ZERO_BYTES, address(this), block.timestamp + 1
        );

        uint256 g0 = gasleft();
        _swap(true, 0.01e18);
        uint256 hooked = g0 - gasleft();

        g0 = gasleft();
        swapRouter.swapExactTokensForTokens(
            0.01e18, 0, true, plainKey, Constants.ZERO_BYTES, address(this), block.timestamp + 1
        );
        uint256 plain = g0 - gasleft();

        emit log_named_uint("hooked swap gas", hooked);
        emit log_named_uint("no-hook swap gas", plain);
        emit log_named_uint("hook overhead", hooked - plain);

        // The grant's Gate 3 target is <= 40k gas overhead; V0's calm path
        // should already sit comfortably inside it.
        assertLt(hooked - plain, 40_000);
    }

    // ------------------------------------------------------------------
    // invariants (the Gate 3 promises, in miniature)
    // ------------------------------------------------------------------

    /// @notice For any pseudo-random swap sequence: the fee never leaves
    /// [MIN_FEE, MAX_FEE] and never moves more than MAX_FEE_STEP per update.
    function testFuzz_FeeBoundsAndRateLimitHold(uint256 seed) public {
        uint24 prev = _currentFee();
        for (uint256 i = 0; i < 25; i++) {
            uint256 roll = uint256(keccak256(abi.encode(seed, i)));
            bool zeroForOne = roll % 2 == 0;
            uint256 amountIn = bound(roll >> 8, 0.001e18, 2e18);
            if (roll % 7 == 0) vm.warp(block.timestamp + (roll % 2 hours));

            _swap(zeroForOne, amountIn);

            uint24 fee = _currentFee();
            assertGe(fee, hook.MIN_FEE());
            assertLe(fee, hook.MAX_FEE());
            uint24 change = fee > prev ? fee - prev : prev - fee;
            assertLe(change, hook.MAX_FEE_STEP());
            prev = fee;
        }
    }
}
