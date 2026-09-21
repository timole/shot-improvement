package tech.timolehtonen.shot

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.Locale
import kotlin.concurrent.thread

const val DURATION_S = 10

sealed interface UiState {
    data object Idle : UiState
    data class Recording(val remainingS: Int) : UiState
    data class Result(val headline: String, val detail: String) : UiState
}

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { MaterialTheme { ShotScreen() } }
    }

    private fun analyse(capture: Capture): Pair<UiState.Result, JSONObject> {
        val claps = Claps.detectClaps(capture.samples, capture.sampleRate)
        val pairs = Claps.pairClaps(claps.map { it.timeS })
        val json = JSONObject()
            .put("appVersion", "0.1.0")
            .put("device", "${Build.MANUFACTURER} ${Build.MODEL}")
            .put("sampleRate", capture.sampleRate)
            .put("audioSource", capture.audioSource)
            .put("effects", capture.effects)
            .put("clippedFraction", capture.clippedFraction)
            .put("overrun", capture.overrun)
            .put("distanceM", Geometry.DISTANCE_M)
            .put("speedOfSoundMs", Geometry.SPEED_OF_SOUND_MS)
            .put("clapTimes", JSONArray(claps.map { it.timeS }))
            .put("groundTruthKmh", JSONObject.NULL)

        val first = pairs.firstOrNull { it.second != null }
        val result = when {
            capture.overrun -> UiState.Result("Tallennus katkesi.", "Ääntä puuttuu - yritä uudelleen.")
            claps.isEmpty() -> UiState.Result("Laukausta ei tunnistettu.", "Ei tarpeeksi kovia ääniä.")
            first == null -> UiState.Result("Osumaa ei kuulunut.", "Tunnistettiin ${claps.size} ääntä, ei paria.")
            else -> {
                val (shotT, hitT) = first
                val speed = Geometry.puckSpeedKmh(shotT, hitT!!)
                if (speed == null) {
                    UiState.Result("Nopeutta ei voitu laskea.", "")
                } else {
                    json.put("shotT", shotT).put("hitT", hitT).put("speedKmh", speed)
                    val dt = String.format(Locale.forLanguageTag("fi"), "%.3f", hitT - shotT)
                    val extra = if (pairs.count { it.second != null } > 1) " · ${pairs.size} laukausta, näytetään ensimmäinen" else ""
                    UiState.Result(
                        "Nopeus: ${Math.round(speed)} km/h",
                        "Ero $dt s · 22,5 m · sinisestä viivasta päätyyn$extra",
                    )
                }
            }
        }
        return result to json
    }

    @Composable
    private fun ShotScreen() {
        var state by remember { mutableStateOf<UiState>(UiState.Idle) }

        fun startRecording() {
            state = UiState.Recording(DURATION_S)
            thread(name = "shot-record") {
                val capture = ShotRecorder.record(this, DURATION_S) { elapsed ->
                    state = UiState.Recording((DURATION_S - elapsed).toInt().coerceAtLeast(0) + 1)
                }
                if (capture == null) {
                    state = UiState.Result("Mikrofonia ei voitu avata.", "")
                    return@thread
                }
                val (result, json) = analyse(capture)
                val stem = "shot-${ShotRecorder.timestamp()}"
                val dir = ShotRecorder.recordingsDir(this)
                ShotRecorder.writeWav(File(dir, "$stem.wav"), capture.samples, capture.sampleRate)
                File(dir, "$stem.json").writeText(json.toString(2))
                Log.i(TAG, "saved $stem: ${result.headline}")
                state = result
            }
        }

        val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (granted) startRecording() else state = UiState.Result("Mikrofonin lupa puuttuu.", "Salli mikrofoni asetuksista.")
        }

        fun onRecordClick() {
            val has = ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
            if (has) startRecording() else permission.launch(Manifest.permission.RECORD_AUDIO)
        }

        Column(
            modifier = Modifier.fillMaxSize().padding(24.dp),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            when (val s = state) {
                is UiState.Idle -> Text("Seiso sinisellä viivalla ja ammu kiekko päätyyn.", textAlign = TextAlign.Center)
                is UiState.Recording -> Text("Tallennetaan… ${s.remainingS} s", fontSize = 28.sp)
                is UiState.Result -> {
                    Text(s.headline, fontSize = 36.sp, textAlign = TextAlign.Center)
                    if (s.detail.isNotEmpty()) Text(s.detail, textAlign = TextAlign.Center, modifier = Modifier.padding(top = 8.dp))
                }
            }
            Button(
                onClick = ::onRecordClick,
                enabled = state !is UiState.Recording,
                modifier = Modifier.fillMaxWidth().padding(top = 32.dp).height(72.dp),
            ) {
                Text("Tallenna $DURATION_S s", fontSize = 22.sp)
            }
        }
    }
}
