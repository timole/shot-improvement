package tech.timolehtonen.shot

import android.util.Log
import java.io.File
import java.io.OutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID

/**
 * Uploads one shot's video/audio/metadata to snapshot.timolehtonen.tech
 * (spec 147 - "upload the videos and audio to Azure... the user can
 * download videos there"). A plain multipart/form-data POST via
 * `HttpURLConnection`, hand-rolled rather than pulling in an HTTP
 * library - this app's established style (see `ShotHistory`'s own
 * hand-rolled JSON, for the same "one small thing doesn't justify a new
 * dependency" reasoning).
 *
 * Auth is a fixed shared-secret Bearer token, not a per-user login -
 * this app has exactly one user and never does the browser-based
 * Microsoft sign-in the web gallery itself uses (server/main.py's
 * `_check_upload_token`, checked against `SHOT_ANDROID_UPLOAD_TOKEN`).
 * Rotating it means changing both [UPLOAD_TOKEN] here and the Container
 * App secret, then rebuilding and reinstalling the app - accepted as a
 * real limitation, not solved further in this spec (see spec 147's "Not
 * covered": embedding a long-lived secret in a distributed APK is only
 * ever obscurity, not strong security, which is fine for a personal,
 * side-loaded tool but wouldn't be for a publicly distributed app).
 *
 * Best-effort only: a failure (no network, server down, timeout) is
 * logged and otherwise ignored - nothing saved locally depends on the
 * upload succeeding, and there's no retry queue in this version.
 */
object CloudUploader {
    private const val UPLOAD_URL = "https://snapshot.timolehtonen.tech/api/android/upload"
    private const val UPLOAD_TOKEN = "JsdKQbKoy2vncezuPBeHDjJMGmvAKNdjHbSRqvdoCsQ"
    private const val CONNECT_TIMEOUT_MS = 15_000
    private const val READ_TIMEOUT_MS = 30_000

    /**
     * Uploads whichever of [videoFile]/[audioFile]/[metadataFile] actually
     * exist under [stem] - a shot from a camera-less session (spec 140)
     * has no video, but its audio still uploads. Returns whether the
     * server accepted it; runs entirely on the calling thread, so callers
     * that don't want to block do their own dispatching (see
     * MainActivity.uploadPendingShots).
     */
    fun uploadShot(stem: String, videoFile: File?, audioFile: File?, metadataFile: File?): Boolean {
        if (videoFile == null && audioFile == null && metadataFile == null) return false
        val boundary = "----shot-improvement-${UUID.randomUUID()}"
        var connection: HttpURLConnection? = null
        return try {
            connection = (URL(UPLOAD_URL).openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"
                doOutput = true
                connectTimeout = CONNECT_TIMEOUT_MS
                readTimeout = READ_TIMEOUT_MS
                setRequestProperty("Authorization", "Bearer $UPLOAD_TOKEN")
                setRequestProperty("Content-Type", "multipart/form-data; boundary=$boundary")
            }
            connection.outputStream.use { out ->
                writeFormField(out, boundary, "stem", stem)
                videoFile?.let { writeFileField(out, boundary, "video", it, "video/mp4") }
                audioFile?.let { writeFileField(out, boundary, "audio", it, "audio/wav") }
                metadataFile?.let { writeFileField(out, boundary, "metadata", it, "application/json") }
                out.write("--$boundary--\r\n".toByteArray(Charsets.US_ASCII))
            }
            val code = connection.responseCode
            if (code in 200..299) {
                true
            } else {
                Log.w(TAG, "CloudUploader: upload of $stem rejected, HTTP $code")
                false
            }
        } catch (e: Exception) {
            Log.w(TAG, "CloudUploader: upload of $stem failed", e)
            false
        } finally {
            connection?.disconnect()
        }
    }

    private fun writeFormField(out: OutputStream, boundary: String, name: String, value: String) {
        out.write("--$boundary\r\n".toByteArray(Charsets.US_ASCII))
        out.write("Content-Disposition: form-data; name=\"$name\"\r\n\r\n".toByteArray(Charsets.US_ASCII))
        out.write(value.toByteArray(Charsets.UTF_8))
        out.write("\r\n".toByteArray(Charsets.US_ASCII))
    }

    private fun writeFileField(out: OutputStream, boundary: String, name: String, file: File, contentType: String) {
        out.write("--$boundary\r\n".toByteArray(Charsets.US_ASCII))
        out.write("Content-Disposition: form-data; name=\"$name\"; filename=\"${file.name}\"\r\n".toByteArray(Charsets.US_ASCII))
        out.write("Content-Type: $contentType\r\n\r\n".toByteArray(Charsets.US_ASCII))
        file.inputStream().use { it.copyTo(out) }
        out.write("\r\n".toByteArray(Charsets.US_ASCII))
    }
}
