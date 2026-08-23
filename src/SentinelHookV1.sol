// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {BaseHook} from "@openzeppelin/uniswap-hooks/src/base/BaseHook.sol";

import {Hooks} from "@uniswap/v4-core/src/libraries/Hooks.sol";
import {IPoolManager, SwapParams} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "@uniswap/v4-core/src/types/PoolId.sol";
import {BeforeSwapDelta, BeforeSwapDeltaLibrary} from "@uniswap/v4-core/src/types/BeforeSwapDelta.sol";
import {LPFeeLibrary} from "@uniswap/v4-core/src/libraries/LPFeeLibrary.sol";
import {StateLibrary} from "@uniswap/v4-core/src/libraries/StateLibrary.sol";

/// @title SentinelHookV1
/// @notice Continuous toxicity-pricing dynamic fee, calibrated on labeled
///         Unichain mainnet data (see research/GATE1_PREREG.md and the
///         Sentinel v2 calibration artifacts).
///
/// Signal (fully self-contained, no oracle):
///   ema = EMA[half-life 300s] of |sqrtP(t) - sqrtP(t-60s)| / sqrtP(t-60s)
///
/// The hook samples its own pool price into ten-second buckets (packed one
/// word each: timestamp << 160 | sqrtPriceX96), reads the bucket nearest to
/// sixty seconds back, and maintains an exponentially-weighted realized-vol
/// estimate. The fee target is the calibrated linear map
///
///   fee = clamp(k * ema, BASE_FEE, CAP_FEE),  k = 4
///
/// reached through the same rate-limited stepping, hard bounds, and safe
/// fallbacks proven in V0. A trade never pays a fee set by its own price
/// impact: the observation is taken before the swap executes.
///
/// Development evidence (calendar-2025 replay, 2.53M labeled trades):
/// 18/18 swept configurations beat the static-fee baseline out-of-sample;
/// the deployed parameters (half-life 300s, lookback 60s, k=4, cap 100bps)
/// returned +$2.98M net LP improvement vs static on sampled days at 71%
/// targeting precision. Final claims are reserved for the locked holdout.
contract SentinelHookV1 is BaseHook {
    using PoolIdLibrary for PoolKey;
    using LPFeeLibrary for uint24;
    using StateLibrary for IPoolManager;

    // ------------------------------------------------------------------
    // Calibrated parameters (see research/sentinel_data/calibrate_vol_fee_policy.py)
    // ------------------------------------------------------------------
    uint24 public constant MIN_FEE = 100; // 0.01% absolute floor
    uint24 public constant BASE_FEE = 500; // 0.05% normal-conditions fee
    uint24 public constant CAP_FEE = 10_000; // 0.05% -> 1.00% calibrated ceiling
    uint24 public constant MAX_FEE = 10_000; // absolute ceiling (== CAP_FEE)
    uint24 public constant MAX_FEE_STEP = 500; // max change per update

    uint256 public constant K_NUM = 4; // fee target = K_NUM * emaWad / K_DEN (1e6 units)
    uint256 public constant K_DEN = 1e12;
    uint256 public constant WAD = 1e18;
    uint64 public constant HALF_LIFE_SECONDS = 300;
    // WAD * ln(2) / HALF_LIFE, truncated
    uint256 public constant DECAY_PER_SECOND_WAD = 2_310_490_601_866_484;
    uint32 public constant LOOKBACK_SECONDS = 60;
    uint32 public constant SAMPLE_BUCKET_SECONDS = 10;
    uint256 public constant N_SAMPLES = 32; // 32 * 10s = 320s of coverage
    uint32 public constant MAX_SAMPLE_AGE_SECONDS = 240; // 4x lookback guard

    struct PoolState {
        bool initialized;
        uint24 currentFee;
        uint64 lastUpdate;
        uint128 emaRateWad;
    }

    mapping(PoolId => PoolState) public poolState;
    /// @notice packed sample: (timestamp << 160) | sqrtPriceX96, per 10s bucket
    mapping(PoolId => uint256[N_SAMPLES]) public samples;

    event FeeUpdated(PoolId indexed poolId, uint24 oldFee, uint24 newFee, uint128 emaRateWad);

    error NotDynamicFeePool();

    constructor(IPoolManager _poolManager) BaseHook(_poolManager) {}

    function getHookPermissions() public pure override returns (Hooks.Permissions memory) {
        return Hooks.Permissions({
            beforeInitialize: true,
            afterInitialize: true,
            beforeAddLiquidity: false,
            afterAddLiquidity: false,
            beforeRemoveLiquidity: false,
            afterRemoveLiquidity: false,
            beforeSwap: true,
            afterSwap: false,
            beforeDonate: false,
            afterDonate: false,
            beforeSwapReturnDelta: false,
            afterSwapReturnDelta: false,
            afterAddLiquidityReturnDelta: false,
            afterRemoveLiquidityReturnDelta: false
        });
    }

    function _beforeInitialize(address, PoolKey calldata key, uint160) internal pure override returns (bytes4) {
        if (!key.fee.isDynamicFee()) revert NotDynamicFeePool();
        return BaseHook.beforeInitialize.selector;
    }

    function _afterInitialize(address, PoolKey calldata key, uint160 sqrtPriceX96, int24)
        internal
        override
        returns (bytes4)
    {
        PoolId id = key.toId();
        poolState[id] = PoolState({
            initialized: true,
            currentFee: BASE_FEE,
            lastUpdate: uint64(block.timestamp),
            emaRateWad: 0
        });
        _storeSample(id, uint64(block.timestamp), sqrtPriceX96);
        return BaseHook.afterInitialize.selector;
    }

    function _beforeSwap(address, PoolKey calldata key, SwapParams calldata, bytes calldata)
        internal
        override
        returns (bytes4, BeforeSwapDelta, uint24)
    {
        PoolId id = key.toId();
        PoolState memory s = poolState[id];

        // Safe fallback: unknown state charges the conservative base fee and
        // writes nothing.
        if (!s.initialized) {
            return (BaseHook.beforeSwap.selector, BeforeSwapDeltaLibrary.ZERO_DELTA, BASE_FEE | LPFeeLibrary.OVERRIDE_FEE_FLAG);
        }

        (uint160 sqrtPriceX96, , , ) = poolManager.getSlot0(id);
        uint64 nowTs = uint64(block.timestamp);
        uint256 decayWad = DECAY_PER_SECOND_WAD * (nowTs - s.lastUpdate);
        uint128 ema = s.emaRateWad;

        // Time decay of the existing EMA (linear approximation of exp decay,
        // clamped to full replacement).
        if (decayWad >= WAD) {
            ema = 0;
        } else if (decayWad > 0) {
            ema = uint128((uint256(ema) * (WAD - decayWad)) / WAD);
        }

        // Observation: realized move over the lookback horizon, taken BEFORE
        // this swap executes so a trade never sets its own fee.
        uint256 refSample = _findSample(id, nowTs);
        if (refSample != 0 && decayWad > 0) {
            uint160 refSqrtP = uint160(refSample);
            if (refSqrtP > 0) {
                uint256 diff = sqrtPriceX96 > refSqrtP ? sqrtPriceX96 - refSqrtP : refSqrtP - sqrtPriceX96;
                uint256 obs = (diff * WAD) / refSqrtP;
                if (obs > WAD) obs = WAD;
                // symmetric EMA blend (obs may be below or above ema)
                if (obs >= ema) {
                    ema = uint128(ema + ((obs - ema) * decayWad) / WAD);
                } else {
                    ema = uint128(ema - ((ema - obs) * decayWad) / WAD);
                }
            }
        }

        // Calibrated continuous target, then V0 safety rails: rate-limited
        // stepping and hard clamps (clamps run last so no path escapes them).
        uint24 target = uint24(MathMin((uint256(ema) * K_NUM) / K_DEN, CAP_FEE));
        if (target < BASE_FEE) target = BASE_FEE;
        uint24 newFee = _stepToward(s.currentFee, target);
        if (newFee < MIN_FEE) newFee = MIN_FEE;
        if (newFee > MAX_FEE) newFee = MAX_FEE;

        if (newFee != s.currentFee) emit FeeUpdated(id, s.currentFee, newFee, ema);

        poolState[id] = PoolState({
            initialized: true,
            currentFee: newFee,
            lastUpdate: nowTs,
            emaRateWad: ema
        });
        _storeSample(id, nowTs, sqrtPriceX96);

        return (BaseHook.beforeSwap.selector, BeforeSwapDeltaLibrary.ZERO_DELTA, newFee | LPFeeLibrary.OVERRIDE_FEE_FLAG);
    }

    /// @dev Reads the sample nearest to (now - LOOKBACK), walking back a few
    /// buckets; returns 0 when no sufficiently fresh sample exists.
    /// @dev Reads the newest sample whose age is at least half the lookback
    /// (matching the validated offline rule) and no older than the freshness
    /// guard; returns 0 when no such sample exists.
    function _findSample(PoolId id, uint64 nowTs) private view returns (uint256) {
        uint256 minAge = LOOKBACK_SECONDS / 2;
        if (nowTs <= minAge) return 0;
        uint256 currentBucket = nowTs / SAMPLE_BUCKET_SECONDS;
        for (uint256 i = 0; i < 8; i++) {
            if (currentBucket < i) break;
            uint256 idx = (currentBucket - i) % N_SAMPLES;
            uint256 sample = samples[id][idx];
            if (sample == 0) continue;
            uint64 ts = uint64(sample >> 160);
            if (ts > nowTs) continue;
            uint256 age = nowTs - ts;
            if (age >= minAge && age <= MAX_SAMPLE_AGE_SECONDS) {
                return sample;
            }
        }
        return 0;
    }

    function _storeSample(PoolId id, uint64 ts, uint160 sqrtPriceX96) private {
        uint256 idx = (ts / SAMPLE_BUCKET_SECONDS) % N_SAMPLES;
        samples[id][idx] = (uint256(ts) << 160) | uint256(sqrtPriceX96);
    }

    function _stepToward(uint24 current, uint24 target) private pure returns (uint24) {
        if (target > current) {
            uint24 diff = target - current;
            return diff > MAX_FEE_STEP ? current + MAX_FEE_STEP : target;
        }
        if (target < current) {
            uint24 diff = current - target;
            return diff > MAX_FEE_STEP ? current - MAX_FEE_STEP : target;
        }
        return current;
    }

    function MathMin(uint256 a, uint24 b) private pure returns (uint24) {
        return a < b ? uint24(a) : b;
    }

    /// @notice Convenience getter for offchain monitoring.
    function getCurrentFee(PoolId id) external view returns (uint24) {
        PoolState memory s = poolState[id];
        return s.initialized ? s.currentFee : BASE_FEE;
    }

    /// @notice Expose the current EMA for offchain monitoring and tests.
    function getEmaRateWad(PoolId id) external view returns (uint128) {
        PoolState memory s = poolState[id];
        return s.initialized ? s.emaRateWad : 0;
    }
}
