package tech.timolehtonen.shot

/**
 * Shot geometry and the speed formula. The phone stands next to the shooter
 * on the blue line, so the stick-puck impact is heard at once while the
 * board impact is heard after the puck's flight PLUS the sound's trip back:
 *
 *     dt = d/v + d/c      ->      v = d / (dt - d/c)
 *
 * (The desktop rig has its mic at the goal, where the sign is the opposite
 * and core/claps.py ignores the term altogether.)
 */
object Geometry {
    /** IIHF: blue line to end boards (core/claps.py "blue_line_to_end"). */
    const val DISTANCE_M = 22.5

    /** c = 331.3 + 0.606 * T at ~10 C indoor rink air. 10 C of error moves a 130 km/h reading 0.25 km/h. */
    const val SPEED_OF_SOUND_MS = 337.4

    /** Bounds of the speed band a hit is accepted in (slow youth wrist shot ... above any slapshot). */
    const val MIN_PLAUSIBLE_KMH = 40.0
    const val MAX_PLAUSIBLE_KMH = 170.0

    /** Guards the division: a flight shorter than this is ~270 km/h at 22.5 m. */
    const val MIN_FLIGHT_S = 0.30

    /** (min, max) measured shot-to-hit gap: flight time at the band edges plus the sound's return trip. */
    fun hitDelayWindow(distanceM: Double = DISTANCE_M): Pair<Double, Double> {
        val sound = distanceM / SPEED_OF_SOUND_MS
        return Pair(
            distanceM / (MAX_PLAUSIBLE_KMH / 3.6) + sound,
            distanceM / (MIN_PLAUSIBLE_KMH / 3.6) + sound,
        )
    }

    /** Average puck speed over the flight in km/h, or null when the gap leaves no plausible flight time. */
    fun puckSpeedKmh(shotT: Double, hitT: Double, distanceM: Double = DISTANCE_M): Double? {
        val flight = (hitT - shotT) - distanceM / SPEED_OF_SOUND_MS
        if (flight < MIN_FLIGHT_S) return null
        return distanceM / flight * 3.6
    }
}
