package tech.timolehtonen.shot

import java.util.Locale
import kotlin.math.abs
import kotlin.math.round

/**
 * Geometry of the IIHF ("euro") rink map used to pick the shooting place
 * (spec 138): where the named places sit on the rink, and how a tap turns
 * into a distance in metres. Mirrors core/rink.py, but with the target
 * fixed to the end boards (RINK_LENGTH_M) - the phone stands at the
 * shooter and measures to the far end, never to a goal (see Geometry.kt).
 *
 * Coordinates: x runs along the rink from the shooter's end boards (0 m)
 * to the far end boards (60 m, RINK_LENGTH_M), y across it (0..30 m).
 */
object Rink {
    const val RINK_LENGTH_M = 60.0
    const val RINK_WIDTH_M = 30.0
    const val CORNER_RADIUS_M = 8.5
    const val GOAL_LINE_OFFSET_M = 4.0
    const val BLUE_LINE_NEAR_X_M = 22.5
    const val BLUE_LINE_FAR_X_M = 37.5
    const val CENTER_LINE_X_M = 30.0
    const val FACEOFF_CIRCLE_RADIUS_M = 4.5
    val FACEOFF_SPOT_X_M = doubleArrayOf(10.0, 50.0) // 6 m from each goal line
    val FACEOFF_SPOT_Y_M = doubleArrayOf(RINK_WIDTH_M / 2 - 7.0, RINK_WIDTH_M / 2 + 7.0)
    const val GOAL_WIDTH_M = 1.83
    const val GOAL_DEPTH_M = 1.12
    const val FAR_GOAL_LINE_X_M = RINK_LENGTH_M - GOAL_LINE_OFFSET_M

    const val MIN_DISTANCE_M = 2.0
    const val MAX_DISTANCE_M = 60.0
    // Bigger than the desktop's 1.5 m - a touch target needs more slack than a mouse click.
    const val SNAP_M = 2.0

    data class Place(val key: String, val label: String, val xM: Double) {
        val distanceM: Double get() = RINK_LENGTH_M - xM
    }

    /** Same x-positions as core.rink.MAP_PLACES; distance is always "to the end boards" here. */
    val PLACES: List<Place> = listOf(
        Place("end_to_end", "Päätyviivalta päätyyn", GOAL_LINE_OFFSET_M),
        Place("faceoff_dots", "Oman alueen aloituspisteiden linjalta päätyyn", FACEOFF_SPOT_X_M[0]),
        Place("other_blue_line", "Toisesta sinisestä viivasta päätyyn", BLUE_LINE_NEAR_X_M),
        Place("red_line", "Keskiviivalta päätyyn", CENTER_LINE_X_M),
        Place("blue_line", "Sinisestä viivasta päätyyn", BLUE_LINE_FAR_X_M),
        Place("attack_dots", "Hyökkäysalueen aloituspisteiden välistä päätyyn", FACEOFF_SPOT_X_M[1]),
    )
    val DEFAULT_PLACE: Place = PLACES.first { it.key == "blue_line" } // 22.5 m, matches the original v1 preset

    fun placeAt(xM: Double, snapM: Double = SNAP_M): Place? =
        PLACES.filter { abs(it.xM - xM) <= snapM }.minByOrNull { abs(it.xM - xM) }

    /** The shot distance for a tap at xM along the rink: snapped to a named
     * place when close to its line, else the metres to the end boards
     * rounded to 0.1 m. null when that leaves no room for a shot. */
    fun distanceForClick(xM: Double, snapM: Double = SNAP_M): Double? {
        placeAt(xM, snapM)?.let { return it.distanceM }
        val d = round((RINK_LENGTH_M - xM) * 10.0) / 10.0
        return if (d in MIN_DISTANCE_M..MAX_DISTANCE_M) d else null
    }

    /** Where to mark a chosen distance on the rink; may be outside [0, RINK_LENGTH_M]. */
    fun xForDistance(distanceM: Double): Double {
        PLACES.firstOrNull { abs(it.distanceM - distanceM) < 0.005 }?.let { return it.xM }
        return RINK_LENGTH_M - distanceM
    }

    fun identifyPlace(distanceM: Double): Place? = PLACES.firstOrNull { abs(it.distanceM - distanceM) < 0.005 }

    /** "Sinisestä viivasta päätyyn (18,5 m)" for a named place, "11 m päätyyn" otherwise; "—" when unknown. */
    fun describe(distanceM: Double?): String {
        if (distanceM == null) return "—"
        val place = identifyPlace(distanceM)
        return if (place != null) "${place.label} (${formatDistance(distanceM)} m)" else "${formatDistance(distanceM)} m päätyyn"
    }

    /** 18.5 -> "18,5", 6.0 -> "6" (Finnish decimal comma, no trailing zero). */
    fun formatDistance(distanceM: Double): String {
        val s = String.format(Locale.ROOT, "%.2f", distanceM).trimEnd('0').trimEnd('.')
        return s.replace('.', ',')
    }
}
