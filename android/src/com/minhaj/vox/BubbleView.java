package com.minhaj.vox;

import android.animation.ValueAnimator;
import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.RectF;
import android.os.SystemClock;
import android.view.View;

/**
 * Round mic bubble. Grey when idle, red with a level ring while recording, amber spinner while processing.
 * The voice note bubble (note = true) is the same bubble with a blue idle colour and a note page instead of the mic;
 * while a note records, the page gives way to the recording time (m:ss).
 * After a dictation ends it flashes a green check (SENT) or a red ! (ERROR) for a moment, then shows idle again.
 */
public class BubbleView extends View {
    /** Kinds for {@link #flash}. */
    public static final int SENT = 1, ERROR = 2;
    private static final long SENT_MS = 700, ERROR_MS = 1800;   // keep equal to FLASH_SECONDS in windows/engine.py (tests/test_flash_constants.py checks it)

    private final boolean note;
    private final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint ring = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint glyph = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint clock = new Paint(Paint.ANTI_ALIAS_FLAG);   // the recording time of the note bubble
    private final RectF r = new RectF();
    private int state = DictationService.IDLE;
    private float level;
    private float spin;
    private final ValueAnimator spinner;
    private int flashKind;        // 0 = none, else SENT or ERROR
    private long flashStart;      // SystemClock.uptimeMillis() when the flash began
    private long recStart;        // SystemClock.uptimeMillis() when the recording began
    private long clockSec = -1;   // the second clockText shows, so the text and its size are made once a second, not on every frame
    private String clockText = "";
    private final float d;

    public BubbleView(Context c) {
        this(c, false);
    }

    /** @param note true for the voice note bubble (its own colour and icon) */
    public BubbleView(Context c, boolean note) {
        super(c);
        this.note = note;
        d = getResources().getDisplayMetrics().density;
        ring.setStyle(Paint.Style.STROKE);
        ring.setStrokeWidth(3 * d);
        ring.setStrokeCap(Paint.Cap.ROUND);
        glyph.setColor(0xFFFFFFFF);
        glyph.setStrokeWidth(2.2f * d);
        glyph.setStrokeCap(Paint.Cap.ROUND);
        clock.setColor(0xFFFFFFFF);
        clock.setTextAlign(Paint.Align.CENTER);
        clock.setFakeBoldText(true);
        spinner = ValueAnimator.ofFloat(0, 360);
        spinner.setDuration(900);
        spinner.setRepeatCount(ValueAnimator.INFINITE);
        spinner.addUpdateListener(a -> { spin = (float) a.getAnimatedValue(); invalidate(); });
        setContentDescription(note ? "Vox voice note" : "Vox dictation");
    }

    public void setState(int s) {
        if (s == DictationService.RECORDING && state != s) recStart = SystemClock.uptimeMillis();
        state = s;
        level = 0;
        if (s != DictationService.IDLE) flashKind = 0;   // a new recording or send replaces the flash
        if (s == DictationService.PROCESSING) { if (!spinner.isStarted()) spinner.start(); }
        else spinner.cancel();
        invalidate();
    }

    /**
     * Shows a green check (SENT, 0.7 s) or a red ! (ERROR, 1.8 s), then the real state again. It only shows while the
     * bubble is idle, so it can be called just before the service goes back to idle; a new recording replaces it.
     */
    public void flash(int kind) {
        if (kind != SENT && kind != ERROR) return;
        flashKind = kind;
        flashStart = SystemClock.uptimeMillis();
        invalidate();
    }

    /** How long {@link #flash} shows a kind of flash (milliseconds), so the owner can keep the bubble up that long. */
    public static long flashMs(int kind) {
        return kind == SENT ? SENT_MS : ERROR_MS;
    }

    /** Milliseconds into the flash that is showing, or -1 when none is (never started, over, or the bubble is busy). */
    private long flashAge() {
        if (flashKind == 0 || state != DictationService.IDLE) return -1;
        long age = SystemClock.uptimeMillis() - flashStart;
        if (age >= flashMs(flashKind)) { flashKind = 0; return -1; }
        return Math.max(0, age);
    }

    public void setLevel(float l) {
        level = l > level ? level * 0.35f + l * 0.65f : level * 0.8f + l * 0.2f;   // quick to rise, slow to fall
        invalidate();
    }

    @Override
    protected void onDraw(Canvas c) {
        float w = getWidth(), h = getHeight();
        float cx = w / 2f, cy = h / 2f;
        float base = Math.min(w, h) / 2f - 5 * d;

        long age = flashAge();
        if (age >= 0) {
            drawFlash(c, age, cx, cy, base);
            postInvalidateOnAnimation();   // next frame; the one after the flash is over draws the idle bubble
            return;
        }

        int color;
        switch (state) {
            case DictationService.RECORDING: color = 0xFFE53935; break;
            case DictationService.PROCESSING: color = 0xFFF59E0B; break;
            default: color = note ? 0xE01F4E79 : 0xE0303338;
        }
        fill.setColor(color);
        float rad = state == DictationService.RECORDING ? base * (0.86f + 0.14f * level) : base * 0.86f;
        c.drawCircle(cx, cy, rad, fill);

        if (state == DictationService.RECORDING) {
            ring.setColor(0x66E53935);
            c.drawCircle(cx, cy, base * (0.92f + 0.08f * level) + 2 * d, ring);
        } else if (state == DictationService.PROCESSING) {
            ring.setColor(0xFFFFFFFF);
            r.set(cx - base * 0.95f, cy - base * 0.95f, cx + base * 0.95f, cy + base * 0.95f);
            c.drawArc(r, spin, 90, false, ring);
        }

        float s = base * 0.42f;
        if (note && state == DictationService.RECORDING) {
            drawClock(c, cx, cy, base);
            return;
        }
        if (note) {
            // Note glyph: a page with three lines of text
            glyph.setStyle(Paint.Style.STROKE);
            r.set(cx - s * 0.7f, cy - s * 0.95f, cx + s * 0.7f, cy + s * 0.95f);
            c.drawRoundRect(r, s * 0.18f, s * 0.18f, glyph);
            for (int i = -1; i <= 1; i++) {
                float y = cy + i * s * 0.42f;
                c.drawLine(cx - s * 0.32f, y, cx + s * 0.32f, y, glyph);
            }
            return;
        }
        // Mic glyph
        glyph.setStyle(Paint.Style.FILL);
        r.set(cx - s * 0.38f, cy - s * 0.95f, cx + s * 0.38f, cy + s * 0.25f);
        c.drawRoundRect(r, s * 0.38f, s * 0.38f, glyph);
        glyph.setStyle(Paint.Style.STROKE);
        r.set(cx - s * 0.68f, cy - s * 0.55f, cx + s * 0.68f, cy + s * 0.55f);
        c.drawArc(r, 20, 140, false, glyph);
        c.drawLine(cx, cy + s * 0.55f, cx, cy + s * 0.9f, glyph);
    }

    /** The time the note has been recording, in the middle of the bubble; text and size are redone only when the second changes. */
    private void drawClock(Canvas c, float cx, float cy, float base) {
        long ms = SystemClock.uptimeMillis() - recStart;
        if (ms / 1000 != clockSec) {
            clockSec = ms / 1000;
            clockText = NoteBubbleLogic.timer(ms);
            clock.setTextSize(base * 0.62f);
            float w = clock.measureText(clockText), room = base * 1.5f;
            if (w > room) clock.setTextSize(base * 0.62f * room / w);   // 1:02:03 is wider than 12:05
        }
        c.drawText(clockText, cx, cy - (clock.ascent() + clock.descent()) / 2f, clock);
    }

    /** SENT: a green circle and a check that draws itself. ERROR: a red circle that shakes once, with a "!". */
    private void drawFlash(Canvas c, long age, float cx, float cy, float base) {
        boolean sent = flashKind == SENT;
        fill.setColor(sent ? 0xFF2E9E5B : 0xFFD32F2F);
        if (!sent) cx += (float) Math.sin(age / 40.0) * 3 * d * Math.max(0f, 1 - age / 400f);
        c.drawCircle(cx, cy, base * (0.78f + 0.08f * Math.min(1f, age / 150f)), fill);   // grows into place

        float s = base * 0.42f;
        glyph.setStyle(Paint.Style.STROKE);
        if (sent) {
            float x1 = cx - s * 0.7f, y1 = cy + s * 0.05f;   // the check: down to the elbow, then up to the right
            float x2 = cx - s * 0.2f, y2 = cy + s * 0.55f;
            float x3 = cx + s * 0.75f, y3 = cy - s * 0.5f;
            float l1 = (float) Math.hypot(x2 - x1, y2 - y1), l2 = (float) Math.hypot(x3 - x2, y3 - y2);
            float drawn = Math.min(1f, age / 250f) * (l1 + l2);
            float t1 = Math.min(1f, drawn / l1);
            c.drawLine(x1, y1, x1 + (x2 - x1) * t1, y1 + (y2 - y1) * t1, glyph);
            if (drawn > l1) {
                float t2 = (drawn - l1) / l2;
                c.drawLine(x2, y2, x2 + (x3 - x2) * t2, y2 + (y3 - y2) * t2, glyph);
            }
        } else {
            c.drawLine(cx, cy - s * 0.9f, cx, cy + s * 0.2f, glyph);
            glyph.setStyle(Paint.Style.FILL);
            c.drawCircle(cx, cy + s * 0.75f, 1.4f * d, glyph);
        }
    }
}
