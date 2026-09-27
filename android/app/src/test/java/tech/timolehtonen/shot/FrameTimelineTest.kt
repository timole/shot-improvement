package tech.timolehtonen.shot
import org.junit.Assert.*
import org.junit.Test

class FrameTimelineTest {
    @Test fun playerSeekUsesMillisecondsAt240Fps() {
        assertEquals(896L, FrameTimeline.playerPositionMs(215.0 / 240))
        assertEquals(1125L, FrameTimeline.playerPositionMs(270.0 / 240))
    }
    @Test fun seeksRealTimestampsIncludingNonuniformPreroll() {
        val times = listOf(0.0, 0.000001, 0.000002, 0.003, 0.007167, 0.011334)
        assertEquals(4, FrameTimeline.indexAt(times, 0.008))
        assertEquals(0.003, FrameTimeline.step(times, 0.0, 1), 1e-9)
        assertEquals(0.011334, FrameTimeline.step(times, 0.007167, 1), 1e-9)
        assertEquals(0.003, FrameTimeline.step(times, 0.007167, -1), 1e-9)
        assertEquals(0.0, FrameTimeline.step(times, 0.0, -1), 0.0)
        assertEquals(0.011334, FrameTimeline.step(times, 99.0, 1), 0.0)
    }
}
