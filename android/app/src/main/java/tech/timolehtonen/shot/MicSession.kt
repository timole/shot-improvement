package tech.timolehtonen.shot

import android.annotation.SuppressLint
import android.content.Context
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.AudioEffect
import android.media.audiofx.AutomaticGainControl
import android.media.audiofx.NoiseSuppressor
import android.util.Log
import kotlin.math.abs

const val TAG = "shot"

/**
 * One continuous open microphone stream (spec 138): opened once when
 * listening starts, read from in a loop until the user stops, closed
 * once. Both impacts of every shot heard in between are offsets into
 * this same stream, so no wall clock or A/V sync is ever needed.
 */
class MicSession private constructor(
    private val record: AudioRecord,
    val sampleRate: Int,
    val audioSource: String,
    val effects: String,
    private val fx: List<AudioEffect>,
) {
    companion object {
        private val SOURCES = listOf(
            MediaRecorder.AudioSource.UNPROCESSED to "UNPROCESSED",
            MediaRecorder.AudioSource.VOICE_RECOGNITION to "VOICE_RECOGNITION",
            MediaRecorder.AudioSource.CAMCORDER to "CAMCORDER",
            MediaRecorder.AudioSource.MIC to "MIC",
        )

        private fun nativeRate(context: Context): Int {
            val am = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
            return am.getProperty(AudioManager.PROPERTY_OUTPUT_SAMPLE_RATE)?.toIntOrNull() ?: 44100
        }

        private fun unprocessedSupported(context: Context): Boolean {
            val am = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
            return am.getProperty(AudioManager.PROPERTY_SUPPORT_AUDIO_SOURCE_UNPROCESSED) == "true"
        }

        /** AGC, noise suppression and echo cancellation all deform an impulse; switch off what the device offers. */
        private fun disableEffects(sessionId: Int): Pair<String, List<AudioEffect>> {
            val created = ArrayList<AudioEffect>()
            val states = ArrayList<String>()
            fun handle(name: String, available: Boolean, create: () -> AudioEffect?) {
                if (!available) { states.add("$name=n/a"); return }
                val effect = create()
                if (effect == null) { states.add("$name=?"); return }
                effect.enabled = false
                states.add("$name=${if (effect.enabled) "on" else "off"}")
                created.add(effect)
            }
            handle("agc", AutomaticGainControl.isAvailable()) { AutomaticGainControl.create(sessionId) }
            handle("ns", NoiseSuppressor.isAvailable()) { NoiseSuppressor.create(sessionId) }
            handle("aec", AcousticEchoCanceler.isAvailable()) { AcousticEchoCanceler.create(sessionId) }
            return states.joinToString(",") to created
        }

        /** Opens and starts recording; null if no source/rate combination could be initialised. */
        @SuppressLint("MissingPermission")
        fun open(context: Context): MicSession? {
            val rates = listOf(nativeRate(context), 44100).distinct()
            for ((source, name) in SOURCES) {
                if (source == MediaRecorder.AudioSource.UNPROCESSED && !unprocessedSupported(context)) continue
                for (rate in rates) {
                    val minBuf = AudioRecord.getMinBufferSize(rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
                    if (minBuf <= 0) continue
                    val record = try {
                        AudioRecord(source, rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, minBuf * 4)
                    } catch (e: Exception) {
                        Log.w(TAG, "AudioRecord($name, $rate) threw: $e")
                        continue
                    }
                    if (record.state != AudioRecord.STATE_INITIALIZED) {
                        record.release()
                        continue
                    }
                    val (effects, fx) = disableEffects(record.audioSessionId)
                    record.startRecording()
                    Log.i(TAG, "mic open: source=$name rate=$rate effects=$effects")
                    return MicSession(record, rate, name, effects, fx)
                }
            }
            return null
        }
    }

    private var clippedCount = 0L
    private var totalCount = 0L

    /** Blocks until at least one sample is available; returns the count read into [dest], or <=0 on error. */
    fun read(dest: ShortArray): Int {
        val n = record.read(dest, 0, dest.size)
        if (n > 0) {
            totalCount += n
            for (i in 0 until n) if (abs(dest[i].toInt()) >= 32000) clippedCount++
        }
        return n
    }

    val clippedFraction: Double get() = if (totalCount > 0) clippedCount.toDouble() / totalCount else 0.0

    fun close() {
        record.stop()
        record.release()
        fx.forEach { it.release() }
        Log.i(TAG, "mic closed: source=$audioSource clippedFraction=$clippedFraction")
    }
}
