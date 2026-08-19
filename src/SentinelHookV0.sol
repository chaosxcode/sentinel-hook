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

/// @title SentinelHookV0
/// @notice V0 skeleton for the Sentinel dynamic-fee research project.
///
/// This contract intentionally contains NO trained risk model and makes NO
/// performance claims. Its placeholder rule (single tick-movement signal,
/// two fee tiers) exists only to prove out the safety scaffolding that the
/// full five-signal model will run inside:
///
///   - hard fee bounds (absolute floor and ceiling)
///   - rate-limited fee changes (max step per update)
///   - hysteresis + cooldown (no threshold bouncing)
///   - pre-swap observations only (a trade cannot set its own fee from
///     price movement it causes)
///   - safe base-fee fallback on unknown state
///   - fixed-size O(1) state per pool, no external calls in the swap path
///
/// See the grant application for the funded research plan and gates.
contract SentinelHookV0 is BaseHook {
    using PoolIdLibrary for PoolKey;
    using LPFeeLibrary for uint24;
    using StateLibrary for IPoolManager;

    // ------------------------------------------------------------------
    // Hard parameters (fee values in hundredths of a bip, 1e6 = 100%)
    // ------------------------------------------------------------------
    uint24 public constant MIN_FEE = 100; // 0.01% absolute floor
    uint24 public constant BASE_FEE = 500; // 0.05% normal-conditions fee
    uint24 public constant ELEVATED_FEE = 3000; // 0.30% elevated tier
    uint24 public constant MAX_FEE = 10_000; // 1.00% absolute ceiling
    uint24 public constant MAX_FEE_STEP = 500; // max change per update

    int24 public constant ENTER_TICKS = 50; // movement that enters the elevated tier
    int24 public constant EXIT_TICKS = 25; // movement must be below this to exit
    uint64 public constant COOLDOWN = 5 minutes; // min time elevated before easing back
    uint64 public constant STALE_AFTER = 1 hours; // observation age beyond which the signal is distrusted

    struct PoolState {
        bool initialized;
        int24 lastTick; // pre-swap tick observed at the previous update
        uint64 lastUpdate; // timestamp of the previous update
        uint64 elevatedSince; // 0 when not in the elevated tier
        uint24 currentFee;
    }

    mapping(PoolId => PoolState) public poolState;

    event FeeUpdated(PoolId indexed poolId, uint24 oldFee, uint24 newFee, int24 tickMove);

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

    /// @dev This hook only serves dynamic-fee pools; refusing anything else at
    /// initialization is cheaper and safer than special-casing it per swap.
    function _beforeInitialize(address, PoolKey calldata key, uint160) internal pure override returns (bytes4) {
        if (!key.fee.isDynamicFee()) revert NotDynamicFeePool();
        return BaseHook.beforeInitialize.selector;
    }

    function _afterInitialize(address, PoolKey calldata key, uint160, int24 tick) internal override returns (bytes4) {
        poolState[key.toId()] = PoolState({
            initialized: true,
            lastTick: tick,
            lastUpdate: uint64(block.timestamp),
            elevatedSince: 0,
            currentFee: BASE_FEE
        });
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

        // Pre-swap observation: the pool tick before this swap executes. The
        // fee this trade pays is decided by movement caused by *previous*
        // trades, never by itself.
        (, int24 tick,,) = poolManager.getSlot0(id);
        uint64 nowTs = uint64(block.timestamp);

        int24 move = tick - s.lastTick;
        uint256 absMove = move < 0 ? uint256(uint24(-move)) : uint256(uint24(move));

        uint24 target;
        if (nowTs - s.lastUpdate > STALE_AFTER) {
            // Observation too old to trust: recalibrate at base.
            // V0 known limitation: an attacker can wait out the window; see
            // README. The funded model replaces this with windowed signals.
            target = BASE_FEE;
            s.elevatedSince = 0;
        } else if (absMove >= uint256(uint24(ENTER_TICKS))) {
            target = ELEVATED_FEE;
            if (s.elevatedSince == 0) s.elevatedSince = nowTs;
        } else if (s.elevatedSince != 0) {
            // Hysteresis + cooldown: exit the elevated tier only once movement
            // is well below the entry threshold AND the cooldown has passed.
            if (absMove < uint256(uint24(EXIT_TICKS)) && nowTs - s.elevatedSince >= COOLDOWN) {
                target = BASE_FEE;
                s.elevatedSince = 0;
            } else {
                target = ELEVATED_FEE;
            }
        } else {
            target = BASE_FEE;
        }

        // Rate limit toward the target, then clamp to the hard bounds. The
        // clamp runs last so no code path can escape it.
        uint24 newFee = _stepToward(s.currentFee, target);
        if (newFee < MIN_FEE) newFee = MIN_FEE;
        if (newFee > MAX_FEE) newFee = MAX_FEE;

        if (newFee != s.currentFee) emit FeeUpdated(id, s.currentFee, newFee, move);

        poolState[id] =
            PoolState({initialized: true, lastTick: tick, lastUpdate: nowTs, elevatedSince: s.elevatedSince, currentFee: newFee});

        return (BaseHook.beforeSwap.selector, BeforeSwapDeltaLibrary.ZERO_DELTA, newFee | LPFeeLibrary.OVERRIDE_FEE_FLAG);
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

    /// @notice Convenience getter for offchain monitoring.
    function getCurrentFee(PoolId id) external view returns (uint24) {
        PoolState memory s = poolState[id];
        return s.initialized ? s.currentFee : BASE_FEE;
    }
}
