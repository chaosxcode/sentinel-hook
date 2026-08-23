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

import {EasyPosm} from "./utils/libraries/EasyPosm.sol";

import {SentinelHookV1} from "../src/SentinelHookV1.sol";
import {BaseTest} from "./utils/BaseTest.sol";

contract SentinelHookV1Test is BaseTest {
    using EasyPosm for IPositionManager;
    using PoolIdLibrary for PoolKey;
    using CurrencyLibrary for Currency;
    using StateLibrary for IPoolManager;

    Currency currency0;
    Currency currency1;

    PoolKey poolKey;

    SentinelHookV1 hook;
    PoolId poolId;

    uint256 tokenId;

    function setUp() public {
        deployArtifactsAndLabel();

        (currency0, currency1) = deployCurrencyPair();

        address flags = address(
            uint160(Hooks.BEFORE_INITIALIZE_FLAG | Hooks.AFTER_INITIALIZE_FLAG | Hooks.BEFORE_SWAP_FLAG)
                ^ (0x5555 << 144) // distinct namespace from V0
        );
        bytes memory constructorArgs = abi.encode(poolManager);
        deployCodeTo("SentinelHookV1.sol:SentinelHookV1", constructorArgs, flags);
        hook = SentinelHookV1(flags);

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
        currency0.transfer(address(positionManager), amount0Expected);
        currency1.transfer(address(positionManager), amount1Expected);
        (tokenId, ) = positionManager.mint(
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

    function test_CalmSwapsStayAtBaseFee() public {
        _swap(true, 1e15);
        assertEq(_currentFee(), hook.BASE_FEE());
        for (uint256 i = 0; i < 5; i++) {
            vm.warp(block.timestamp + 2);
            _swap(false, 1e14);
            assertEq(_currentFee(), hook.BASE_FEE());
        }
    }

    function test_VolatilityRampsFee_RateLimited_AndCapped() public {
        // A large swap moves the price far; it pays the base fee itself.
        _swap(true, 1e18);
        assertEq(_currentFee(), hook.BASE_FEE());

        // Subsequent swaps keep trending the same direction (sustained
        // informed flow), so realized vol over the lookback stays elevated.
        // The fee steps toward the calibrated target, at most MAX_FEE_STEP
        // per update, never past the cap.
        uint24 prev = _currentFee();
        for (uint256 i = 0; i < 80; i++) {
            vm.warp(block.timestamp + 3);
            _swap(false, 0.1e18);
            uint24 fee = _currentFee();
            assertLe(fee - prev > 0 ? fee - prev : 0, hook.MAX_FEE_STEP());
            assertLe(fee, hook.CAP_FEE());
            assertGe(fee, hook.BASE_FEE());
            prev = fee;
        }
        assertEq(prev, hook.CAP_FEE());
    }

    function test_FeeDecaysBackTowardBaseAfterQuiet() public {
        _swap(true, 1e18);
        for (uint256 i = 0; i < 80; i++) {
            vm.warp(block.timestamp + 3);
            _swap(false, 0.1e18);
        }
        assertEq(_currentFee(), hook.CAP_FEE());

        // Long quiet period: the EMA decays to zero, fee steps back to base.
        vm.warp(block.timestamp + 2 hours);
        for (uint256 i = 0; i < 25; i++) {
            vm.warp(block.timestamp + 2);
            _swap(false, 1e14);
        }
        assertEq(_currentFee(), hook.BASE_FEE());
    }

    function test_SwapCannotSetItsOwnFee() public {
        // Pre-swap observation: whatever the size, the fee charged was decided
        // before the swap executed.
        uint24 before = _currentFee();
        _swap(true, 5e18);
        assertEq(_currentFee(), before);
    }

    function test_SmallSwapsAcrossTimeProduceSignal() public {
        // Tiny swaps build the sample ring over a minute; a later sustained
        // move must find its lookback reference and raise the fee.
        for (uint256 i = 0; i < 12; i++) {
            vm.warp(block.timestamp + 5);
            _swap(i % 2 == 0 ? true : false, 1e14);
        }
        for (uint256 i = 0; i < 30; i++) {
            vm.warp(block.timestamp + 3);
            _swap(true, 0.05e18);
        }
        assertGt(_currentFee(), hook.BASE_FEE());
    }

    function test_Fuzz_FeeStaysWithinBoundsAndRateLimited(uint256 seed) public {
        uint256 state = seed == 0 ? 1 : seed;
        uint24 prev = _currentFee();
        for (uint256 i = 0; i < 40; i++) {
            state = uint256(keccak256(abi.encode(state)));
            bool zeroForOne = state % 2 == 0;
            uint256 amount = 1e12 + (state >> 8) % 2e18;
            uint32 dt = 1 + uint32((state >> 40) % 120);
            vm.warp(block.timestamp + dt);
            _swap(zeroForOne, amount);
            uint24 fee = _currentFee();
            assertGe(fee, hook.BASE_FEE());
            assertLe(fee, hook.MAX_FEE());
            if (fee != prev) {
                uint24 diff = fee > prev ? fee - prev : prev - fee;
                assertLe(diff, hook.MAX_FEE_STEP());
            }
            prev = fee;
        }
    }

    function test_GasOverheadPerSwap() public {
        _swap(true, 1e15); // warm-up
        vm.warp(block.timestamp + 2);
        uint256 gasBefore = gasleft();
        _swap(false, 1e14);
        uint256 gasUsed = gasBefore - gasleft();
        // Informational bound: keep the swap-path overhead in sight (Gate 3
        // budget is <= 40k vs a no-hook pool).
        emit assert_gas("swap gas", gasUsed);
        assertLt(gasUsed, 400_000);
    }

    event assert_gas(string, uint256);
}

