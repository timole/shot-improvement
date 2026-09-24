package tech.timolehtonen.shot

import android.content.Context

/** Small persisted app settings (spec 141) - so far just the requested
 * camera capture fps, set from the Settings screen and read back the
 * next time the app starts. */
object Prefs {
    private const val NAME = "shot_prefs"
    private const val KEY_TARGET_FPS = "target_fps"

    fun targetFps(context: Context): Int =
        context.getSharedPreferences(NAME, Context.MODE_PRIVATE).getInt(KEY_TARGET_FPS, FpsOptions.DEFAULT_FPS)

    fun setTargetFps(context: Context, fps: Int) {
        context.getSharedPreferences(NAME, Context.MODE_PRIVATE).edit().putInt(KEY_TARGET_FPS, fps).apply()
    }
}
