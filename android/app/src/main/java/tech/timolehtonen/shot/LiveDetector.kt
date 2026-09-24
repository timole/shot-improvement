package tech.timolehtonen.shot

/**
 * Streaming version of Claps.detectClaps/pairClaps (spec 138): consumes
 * audio as it arrives from the microphone, instead of a whole recorded
 * clip, and reports each shot and hit as soon as it can be confirmed -
 * no fixed-duration recording, listening runs until the caller stops it.
 *
 * Pure logic, no AudioRecord/file IO - [feed] takes plain sample chunks
 * and returns [Event]s; the caller (MainActivity) owns the microphone and
 * any file writing, so this class is fully unit-testable by feeding it
 * a WAV's samples in small pieces, the way a real read() loop would.
 *
 * Streaming adaptation of the offline algorithm (see Claps.kt / core/
 * claps.py for the full reasoning behind each constant):
 *  - Detection is a small state machine instead of "non-maximum
 *    suppression over a whole array": a window that crosses the adaptive
 *    threshold opens a CANDIDATE; the candidate's peak is finalised
 *    [CANDIDATE_FINALIZE_S] later (long enough to both find the true
 *    local max and give the spectral gate's window its lookahead), then
 *    a [minSeparationS] refractory period follows - the same neighbour
 *    suppression as the offline NMS, just applied going forward instead
 *    of across a whole clip.
 *  - The adaptive threshold's median and the prominence gate's local
 *    median both come from a rolling window of the last
 *    [MEDIAN_LOOKBACK_S] seconds of RMS values, not the whole clip's.
 *    The prominence window is asymmetric ([-1.0s, +finalize] instead of
 *    the offline ±1.0s) since only the past is available while streaming.
 *  - Pairing is the same greedy rule as core.claps.pair_claps, just
 *    applied to claps one at a time as they are confirmed, plus a
 *    wall-clock timeout: a pending shot with nothing arriving within the
 *    hit window's own upper bound is reported unpaired, so the UI is
 *    never left waiting forever for a hit that didn't happen.
 *  - The "too many events, must be noise" guard drops individual claps
 *    that push the trailing 1 s rate over [Claps.MAX_CLAPS_PER_SECOND]
 *    instead of discarding a whole clip retroactively.
 *
 * A session is capped at [MAX_SESSION_S] (an [Event.SessionCapReached]
 * fires once and the caller should stop listening) so the audio buffer -
 * kept in full, uncompressed, for the whole session, to allow saving a
 * WAV snippet around any shot - cannot grow unbounded.
 */
class LiveDetector(val sampleRate: Int, val distanceM: Double) {

    sealed interface Event {
        /** A shot's stick-puck impact was heard; now listening for its hit. */
        data class Shot(val timeS: Double) : Event

        /** A shot's hit was heard; speedKmh is null if the gap left no plausible flight time. */
        data class Hit(val shotTimeS: Double, val hitTimeS: Double, val speedKmh: Double?) : Event

        /** A shot never got a valid hit (timed out, or superseded by an out-of-window clap). */
        data class Unpaired(val shotTimeS: Double) : Event

        /** Too many events in the last second to be real shots; this one was ignored. */
        data object NoisyEnvironment : Event

        /** [MAX_SESSION_S] reached - the caller should stop listening. */
        data object SessionCapReached : Event
    }

    companion object {
        const val CHUNK_SECONDS = 1.0
        // Long enough to find a transient's true local max and to give the
        // spectral gate window (SHOT_BAND_WINDOW/2 = 92.9 ms) its lookahead,
        // short enough that "live" still feels live.
        const val CANDIDATE_FINALIZE_S = 0.15
        const val MEDIAN_LOOKBACK_S = 3.0
        const val MAX_SESSION_S = 20.0 * 60.0
    }

    private val buffer = GrowableShortBuffer((sampleRate * CHUNK_SECONDS).toInt())
    private var processedSamples = 0L

    // Rolling RMS history for the adaptive threshold and the prominence gate - kept in step, oldest first.
    private val rmsHistory = ArrayDeque<Double>()
    private val timeHistory = ArrayDeque<Double>()

    private var refractoryUntilS = -1.0
    private var candidateStartS: Double? = null
    private var candidateBestS = 0.0
    private var candidateBestIdx = 0L
    private var candidateBestRms = 0.0

    private var pendingShotS: Double? = null
    private val recentAcceptedS = ArrayDeque<Double>() // trailing 1 s, for the noise guard

    private val hitWindow = Geometry.hitDelayWindow(distanceM)

    // Spec 128's shrink, carried over from core/claps.py: a short distance's
    // own hit can arrive sooner than the fixed refractory period would allow.
    private val minSeparationS = minOf(Claps.MIN_SEPARATION_S, 0.8 * hitWindow.first)

    /** Feeds [length] samples from [chunk] (from index 0); returns whatever became confirmed as a result. */
    fun feed(chunk: ShortArray, length: Int): List<Event> {
        buffer.append(chunk, length)
        val events = ArrayList<Event>()
        while (processedSamples + Claps.RMS_WINDOW <= buffer.totalSamples) {
            processWindow(events)
            processedSamples += Claps.RMS_WINDOW
        }
        checkPendingTimeout(events)
        if (buffer.totalSamples.toDouble() / sampleRate >= MAX_SESSION_S) events.add(Event.SessionCapReached)
        return events
    }

    /** A copy of the audio between [fromS] and [toS] (clamped to what has been captured) - for saving a snippet. */
    fun snapshotSeconds(fromS: Double, toS: Double): ShortArray =
        buffer.snapshot((fromS * sampleRate).toLong(), (toS * sampleRate).toLong())

    private fun processWindow(events: MutableList<Event>) {
        val windowTimeS = processedSamples.toDouble() / sampleRate
        if (windowTimeS < Claps.STARTUP_SKIP_S) return // mic-startup pop, at the very start of the session only

        val rms = buffer.rmsAt(processedSamples, Claps.RMS_WINDOW)
        rmsHistory.addLast(rms)
        timeHistory.addLast(windowTimeS)
        while (timeHistory.isNotEmpty() && timeHistory.first() < windowTimeS - MEDIAN_LOOKBACK_S) {
            timeHistory.removeFirst()
            rmsHistory.removeFirst()
        }

        if (windowTimeS < refractoryUntilS) return

        val threshold = maxOf(Claps.floorRms(), Claps.median(rmsHistory.toDoubleArray()) * Claps.THRESHOLD_MEDIAN_RATIO)
        val start = candidateStartS
        if (start == null) {
            if (rms > threshold) {
                candidateStartS = windowTimeS
                candidateBestS = windowTimeS
                candidateBestIdx = processedSamples
                candidateBestRms = rms
            }
            return
        }
        if (rms > candidateBestRms) {
            candidateBestS = windowTimeS
            candidateBestIdx = processedSamples
            candidateBestRms = rms
        }
        if (windowTimeS - start >= CANDIDATE_FINALIZE_S) finalizeCandidate(events)
    }

    private fun finalizeCandidate(events: MutableList<Event>) {
        val half = Claps.SHOT_BAND_WINDOW / 2
        val snapStart = (candidateBestIdx - half).coerceAtLeast(0)
        val snapEnd = (candidateBestIdx + half).coerceAtMost(buffer.totalSamples)
        val snapshot = buffer.snapshot(snapStart, snapEnd)
        val localCenterT = (candidateBestIdx - snapStart).toDouble() / sampleRate
        val passBand = Claps.shotBandFraction(snapshot, sampleRate, localCenterT) >= Claps.SHOT_BAND_FRACTION_MIN

        val localRms = ArrayList<Double>()
        for (i in timeHistory.indices) {
            val t = timeHistory[i]
            if (t >= candidateBestS - Claps.PROMINENCE_HALF_SPAN_S && t <= candidateBestS + CANDIDATE_FINALIZE_S) {
                localRms.add(rmsHistory[i])
            }
        }
        val passProminence = candidateBestRms >= Claps.PROMINENCE_RATIO * Claps.median(localRms.toDoubleArray())

        val acceptedTimeS = candidateBestS
        refractoryUntilS = acceptedTimeS + minSeparationS
        candidateStartS = null

        if (!passBand || !passProminence) return

        recentAcceptedS.addLast(acceptedTimeS)
        while (recentAcceptedS.isNotEmpty() && recentAcceptedS.first() < acceptedTimeS - 1.0) recentAcceptedS.removeFirst()
        if (recentAcceptedS.size > Claps.MAX_CLAPS_PER_SECOND) {
            events.add(Event.NoisyEnvironment)
            return
        }
        acceptClap(acceptedTimeS, events)
    }

    private fun acceptClap(t: Double, events: MutableList<Event>) {
        val pending = pendingShotS
        if (pending == null) {
            pendingShotS = t
            events.add(Event.Shot(t))
            return
        }
        val gap = t - pending
        if (gap in hitWindow.first..hitWindow.second) {
            events.add(Event.Hit(pending, t, Geometry.puckSpeedKmh(pending, t, distanceM)))
            pendingShotS = null
        } else {
            events.add(Event.Unpaired(pending))
            pendingShotS = t
            events.add(Event.Shot(t))
        }
    }

    private fun checkPendingTimeout(events: MutableList<Event>) {
        val pending = pendingShotS ?: return
        val nowS = processedSamples.toDouble() / sampleRate
        if (nowS - pending > hitWindow.second) {
            events.add(Event.Unpaired(pending))
            pendingShotS = null
        }
    }
}
