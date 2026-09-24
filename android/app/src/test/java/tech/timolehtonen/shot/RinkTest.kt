package tech.timolehtonen.shot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class RinkTest {
    @Test
    fun placeDistancesAreToTheEndBoards() {
        val byKey = Rink.PLACES.associateBy { it.key }
        assertEquals(56.0, byKey.getValue("end_to_end").distanceM, 1e-9)
        assertEquals(50.0, byKey.getValue("faceoff_dots").distanceM, 1e-9)
        assertEquals(37.5, byKey.getValue("other_blue_line").distanceM, 1e-9)
        assertEquals(30.0, byKey.getValue("red_line").distanceM, 1e-9)
        assertEquals(22.5, byKey.getValue("blue_line").distanceM, 1e-9)
        assertEquals(10.0, byKey.getValue("attack_dots").distanceM, 1e-9)
    }

    @Test
    fun defaultPlaceIsTheBlueLine() = assertEquals(22.5, Rink.DEFAULT_PLACE.distanceM, 1e-9)

    @Test
    fun formatDistanceUsesADecimalCommaAndNoTrailingZeros() {
        assertEquals("18,5", Rink.formatDistance(18.5))
        assertEquals("6", Rink.formatDistance(6.0))
        assertEquals("19,62", Rink.formatDistance(19.62))
    }

    @Test
    fun describeNamesAKnownPlaceOrFallsBackToMetres() {
        assertEquals("Sinisestä viivasta päätyyn (22,5 m)", Rink.describe(22.5))
        assertEquals("11 m päätyyn", Rink.describe(11.0))
        assertEquals("—", Rink.describe(null))
    }

    @Test
    fun tappingNearAPlaceSnapsToItsDistance() {
        assertEquals(22.5, Rink.distanceForClick(38.0)!!, 1e-9) // 0.5 m from the blue line
        assertEquals(30.0, Rink.distanceForClick(31.5)!!, 1e-9) // near the red line
        assertEquals(10.0, Rink.distanceForClick(51.5)!!, 1e-9)
    }

    @Test
    fun tappingElsewhereGivesTheMetresToTheEndBoards() {
        assertEquals(15.0, Rink.distanceForClick(45.0)!!, 1e-9)
        assertEquals(44.7, Rink.distanceForClick(15.33)!!, 1e-9)
    }

    @Test
    fun tappingBehindOrTooCloseToTheEndBoardsGivesNothing() {
        assertNull(Rink.distanceForClick(59.0)) // 1 m: no shot fits
        assertNull(Rink.distanceForClick(61.0)) // off the rink
    }

    @Test
    fun xForDistancePutsANamedPlaceExactlyOnItsLine() {
        assertEquals(37.5, Rink.xForDistance(22.5), 1e-9)
        assertEquals(45.0, Rink.xForDistance(15.0), 1e-9)
    }
}
