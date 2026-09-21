package tech.timolehtonen.shot

import android.annotation.SuppressLint
import android.content.Context
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.AutomaticGainControl
import android.media.audiofx.NoiseSuppressor
import android.os.Process
import android.util.Log
import java.io.File
import java.io.RandomAccessFile
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

const val TAG = "shot"

/** One finished capture: raw PCM exactly as AudioRecord delivered it, plus how it was made. */
class Capture(
    val samples: ShortArray,
    val sampleRate: Int,
    val audioSource: String,
    val effects: String,
    val clippedFraction: Double,
    val overrun: Boolean,
)

/**
 * Records one continuous mono 16-bit stream. Both impacts of a shot are in
 * the same stream, so only sample offsets matter - no wall clock is involved.
 */
object ShotRecorder {
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

    @SuppressLint("MissingPermission")
    private fun open(context: Context): Triple<AudioRecord, Int, String>? {
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
                if (record.state == AudioRecord.STATE_INITIALIZED) return Triple(record, rate, name)
                record.release()
            }
        }
        return null
    }

    /** AGC, noise suppression and echo cancellation all deform an impulse; switch off what the device offers. */
    private fun disableEffects(sessionId: Int): Pair<String, List<android.media.audiofx.AudioEffect>> {
        val created = ArrayList<android.media.audiofx.AudioEffect>()
        val states = ArrayList<String>()
        fun handle(name: String, available: Boolean, create: () -> android.media.audiofx.AudioEffect?) {
            if (!available) { states.add("$name=n/a"); return }
            val fx = create()
            if (fx == null) { states.add("$name=?"); return }
            fx.enabled = false
            states.add("$name=${if (fx.enabled) "on" else "off"}")
            created.add(fx)
        }
        handle("agc", AutomaticGainControl.isAvailable()) { AutomaticGainControl.create(sessionId) }
        handle("ns", NoiseSuppressor.isAvailable()) { NoiseSuppressor.create(sessionId) }
        handle("aec", AcousticEchoCanceler.isAvailable()) { AcousticEchoCanceler.create(sessionId) }
        return states.joinToString(",") to created
    }

    /** Blocks for [durationS]; call from a background thread. [onProgress] gets elapsed seconds. */
    fun record(context: Context, durationS: Int, onProgress: (Double) -> Unit): Capture? {
        Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_AUDIO)
        val (record, rate, sourceName) = open(context) ?: return null
        val (effects, fx) = disableEffects(record.audioSessionId)
        val total = durationS * rate
        val samples = ShortArray(total)
        var filled = 0
        var overrun = false
        try {
            record.startRecording()
            while (filled < total) {
                val n = record.read(samples, filled, min(total - filled, 4096))
                if (n < 0) { overrun = true; break }
                filled += n
                onProgress(filled.toDouble() / rate)
            }
        } finally {
            record.stop()
            record.release()
            fx.forEach { it.release() }
        }
        if (filled < total) overrun = true
        val clipped = samples.count { kotlin.math.abs(it.toInt()) >= 32000 }.toDouble() / samples.size
        Log.i(TAG, "captured source=$sourceName rate=$rate effects=$effects clipped=$clipped overrun=$overrun")
        return Capture(samples, rate, sourceName, effects, clipped, overrun)
    }

    private fun min(a: Int, b: Int) = if (a < b) a else b

    /** recordings dir under the app's external files - visible over USB/MTP, no permission needed. */
    fun recordingsDir(context: Context): File {
        val base = context.getExternalFilesDir(android.os.Environment.DIRECTORY_MUSIC) ?: context.filesDir
        return File(base, "recordings").also { it.mkdirs() }
    }

    fun timestamp(): String = SimpleDateFormat("yyyyMMddHHmmss", Locale.US).format(Date())

    /** Uncompressed PCM16 mono WAV - exactly the bytes AudioRecord returned, no normalisation. */
    fun writeWav(file: File, samples: ShortArray, sampleRate: Int) {
        val dataLen = samples.size * 2
        RandomAccessFile(file, "rw").use { f ->
            f.setLength(0)
            val header = java.nio.ByteBuffer.allocate(44).order(java.nio.ByteOrder.LITTLE_ENDIAN)
            header.put("RIFF".toByteArray()).putInt(36 + dataLen).put("WAVE".toByteArray())
            header.put("fmt ".toByteArray()).putInt(16).putShort(1).putShort(1)
            header.putInt(sampleRate).putInt(sampleRate * 2).putShort(2).putShort(16)
            header.put("data".toByteArray()).putInt(dataLen)
            f.write(header.array())
            val body = java.nio.ByteBuffer.allocate(dataLen).order(java.nio.ByteOrder.LITTLE_ENDIAN)
            body.asShortBuffer().put(samples)
            f.write(body.array())
        }
    }
}
