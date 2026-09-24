package tech.timolehtonen.shot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class FpsOptionsTest {
    @Test
    fun onlyThirtyIsSupportedWhenTheOnlyRangeToppedOutAtThirty() {
        val normal = listOf(FpsRange(4, 30))
        assertEquals(listOf(30), FpsOptions.supportedFps(normal, emptyList()))
    }

    @Test
    fun sixtyIsSupportedWhenAnOrdinaryRangeReachesIt() {
        val normal = listOf(FpsRange(4, 30), FpsRange(30, 60))
        assertEquals(listOf(30, 60), FpsOptions.supportedFps(normal, emptyList()))
    }

    @Test
    fun highSpeedRangesUnlockOneTwentyAndTwoForty() {
        val normal = listOf(FpsRange(4, 30))
        val highSpeed = listOf(FpsRange(120, 120), FpsRange(240, 240))
        // "at least N fps": the 120fps high-speed range also satisfies a 60fps ask
        // (bestRange would then run the camera at its actual 120, exceeding it).
        assertEquals(listOf(30, 60, 120, 240), FpsOptions.supportedFps(normal, highSpeed))
    }

    @Test
    fun aHighSpeedRangeDoesNotSatisfyATargetAboveItsOwnUpperBound() {
        val normal = listOf(FpsRange(4, 30))
        val highSpeed = listOf(FpsRange(120, 120))
        assertEquals(listOf(30, 60, 120), FpsOptions.supportedFps(normal, highSpeed))
    }

    @Test
    fun noRangesAtAllSupportsNothing() {
        assertTrue(FpsOptions.supportedFps(emptyList(), emptyList()).isEmpty())
    }

    @Test
    fun bestRangePrefersTheTightestOrdinaryRangeThatReachesTheTarget() {
        val normal = listOf(FpsRange(4, 30), FpsRange(30, 60), FpsRange(30, 90))
        assertEquals(FpsRange(30, 60), FpsOptions.bestRange(60, normal, emptyList()))
    }

    @Test
    fun bestRangeFallsBackToHighSpeedWhenNoOrdinaryRangeReachesTheTarget() {
        val normal = listOf(FpsRange(4, 30))
        val highSpeed = listOf(FpsRange(120, 120), FpsRange(240, 240))
        assertEquals(FpsRange(120, 120), FpsOptions.bestRange(120, normal, highSpeed))
    }

    @Test
    fun bestRangeReturnsTheHighestAvailableWhenNothingReachesTheTarget() {
        val normal = listOf(FpsRange(4, 30))
        assertEquals(FpsRange(4, 30), FpsOptions.bestRange(240, normal, emptyList()))
    }

    @Test
    fun bestRangeIsNullWhenThereAreNoRangesAtAll() {
        assertNull(FpsOptions.bestRange(30, emptyList(), emptyList()))
    }

    @Test
    fun needsHighSpeedSessionIsFalseWhenAnOrdinaryRangeAlreadyReachesTheTarget() {
        val normal = listOf(FpsRange(30, 60))
        assertFalse(FpsOptions.needsHighSpeedSession(60, normal, listOf(FpsRange(120, 120))))
    }

    @Test
    fun needsHighSpeedSessionIsTrueWhenOnlyAHighSpeedRangeReachesTheTarget() {
        val normal = listOf(FpsRange(4, 30))
        assertTrue(FpsOptions.needsHighSpeedSession(120, normal, listOf(FpsRange(120, 120))))
    }

    @Test
    fun needsHighSpeedSessionIsFalseWhenNothingReachesTheTargetEitherWay() {
        val normal = listOf(FpsRange(4, 30))
        assertFalse(FpsOptions.needsHighSpeedSession(240, normal, listOf(FpsRange(120, 120))))
    }

    @Test
    fun targetSizeShrinksAsFpsRises() {
        assertEquals(480, FpsOptions.targetSizePxFor(30))
        assertEquals(480, FpsOptions.targetSizePxFor(60))
        assertEquals(320, FpsOptions.targetSizePxFor(120))
        assertEquals(240, FpsOptions.targetSizePxFor(240))
    }
}
