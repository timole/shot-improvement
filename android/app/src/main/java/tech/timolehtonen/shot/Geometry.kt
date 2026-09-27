package tech.timolehtonen.shot

/**
 * Shot geometry and the speed formula. The phone stands next to the shooter
 * at the selected shooting position, so the stick-puck impact is heard at once while the
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

    /** c = 331.3 + 0.606 * T at ~10 C indoor rink air. Assumes indoor rink air; actual temperature affects the estimate. */
    const val SPEED_OF_SOUND_MS = 337.4

    /** Bounds of the speed band a hit is accepted in (slow youth wrist shot ... above any slapshot). */
    const val MIN_PLAUSIBLE_KMH = 40.0
    const val MAX_PLAUSIBLE_KMH = 170.0

    /** (min, max) measured shot-to-hit gap: flight time at the band edges plus the sound's return trip. */
    fun hitDelayWindow(distanceM: Double = DISTANCE_M): Pair<Double, Double> {
        val sound = distanceM / SPEED_OF_SOUND_MS
        return Pair(
            distanceM / (MAX_PLAUSIBLE_KMH / 3.6) + sound,
            distanceM / (MIN_PLAUSIBLE_KMH / 3.6) + sound,
        )
    }

    /** Uses exactly the same inputs and correction as the detector. */
    fun speedExplanation(shotT: Double, hitT: Double, distanceM: Double): String {
        fun f(value: Double) = String.format(java.util.Locale.forLanguageTag("fi"), "%.3f", value)
        val distance = Rink.formatDistance(distanceM)
        val gap = hitT - shotT
        val sound = distanceM / SPEED_OF_SOUND_MS
        val speed = puckSpeedKmh(shotT, hitT, distanceM)
        return "Puhelin ampujan vieressä. Matka päätyyn s = $distance m.\n" +
            "Äänten aikaväli Δt = ${f(gap)} s. Äänen nopeus c = 337,4 m/s (noin 10 °C).\n" +
            "Äänen paluuviive s/c = ${f(sound)} s.\n" +
            "Kiekon lentoaika t = Δt − s/c = ${f(gap - sound)} s.\n" +
            (if (speed != null) "Keskinopeus v = s/t × 3,6 = $distance / ${f(gap - sound)} × 3,6 ≈ ${f(speed)} km/h."
             else "Nopeutta ei voida laskea luotettavasti näistä ajoista.")
    }

    /** Average puck speed over the flight in km/h, or null when the gap leaves no plausible flight time. */
    fun puckSpeedKmh(shotT: Double, hitT: Double, distanceM: Double = DISTANCE_M): Double? {
        if (!shotT.isFinite() || !hitT.isFinite() || !distanceM.isFinite() || distanceM <= 0.0) return null
        val flight = (hitT - shotT) - distanceM / SPEED_OF_SOUND_MS
        if (!flight.isFinite() || flight <= 0.0) return null
        val speed = distanceM / flight * 3.6
        return speed.takeIf { it.isFinite() && it <= MAX_PLAUSIBLE_KMH + 1e-9 }
    }
}
