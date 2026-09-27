package tech.timolehtonen.shot
import org.junit.Assert.*
import org.junit.Test

class GeometryTest {
    @Test fun correctsSoundTravelForEveryPresetAndSpeed() {
        for (place in Rink.PLACES) for (speed in listOf(40.0, 100.0, 150.0, 170.0)) {
            val gap = place.distanceM / (speed / 3.6) + place.distanceM / 337.4
            assertEquals(speed, Geometry.puckSpeedKmh(1.0, 1.0 + gap, place.distanceM)!!, 1e-8)
        }
    }
    @Test fun longShotSubtractsReturnDelay() {
        assertEquals(100.0, Geometry.puckSpeedKmh(0.0, 2.1819751037344397, 56.0)!!, 1e-6)
    }
    @Test fun invalidMeasurementsDoNotProduceSpeeds() {
        for (d in listOf(0.0, -1.0, Double.NaN, Double.POSITIVE_INFINITY))
            assertNull(Geometry.puckSpeedKmh(0.0, 1.0, d))
        assertNull(Geometry.puckSpeedKmh(Double.NaN, 1.0, 10.0))
        assertNull(Geometry.puckSpeedKmh(0.0, Double.POSITIVE_INFINITY, 10.0))
        assertNull(Geometry.puckSpeedKmh(1.0, 0.0, 10.0))
        assertNull(Geometry.puckSpeedKmh(0.0, 10.0 / 337.4, 10.0))
        assertNull(Geometry.puckSpeedKmh(0.0, 0.04, 10.0))
    }
    @Test fun explanationUsesSelectedDistanceAndFinnishDecimals() {
        val explanation = Geometry.speedExplanation(0.0, 0.27 + 10.0 / 337.4, 10.0)
        assertTrue(explanation.contains("s = 10 m"))
        assertTrue(explanation.contains("0,270 s"))
        assertTrue(explanation.contains("133,333 km/h"))
        assertTrue(Geometry.speedExplanation(0.0, 1.0, 22.5).contains("s = 22,5 m"))
    }
}
