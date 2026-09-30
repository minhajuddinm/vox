package com.minhaj.vox;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.atomic.AtomicInteger;

/** Plain-Java checks for NoteEvents, the "a note was saved" hook. Run by CI, exits non-zero on failure. */
public final class NoteEventsTest {
    private static int checks;

    private static void eq(String name, Object expected, Object actual) {
        checks++;
        if (expected == null ? actual != null : !expected.equals(actual)) {
            System.err.println("FAIL " + name + ":\n  expected <" + expected + ">\n  but got  <" + actual + ">");
            System.exit(1);
        }
    }

    public static void main(String[] args) throws Exception {
        // firing with nobody listening does nothing
        NoteEvents.fireSaved();
        checks++;

        // every listener runs once per fire, in the order they were added
        final List<String> log = new ArrayList<>();
        Runnable a = () -> log.add("a");
        Runnable b = () -> log.add("b");
        NoteEvents.addSavedListener(a);
        NoteEvents.addSavedListener(b);
        NoteEvents.fireSaved();
        eq("order", "[a, b]", log.toString());
        NoteEvents.fireSaved();
        eq("each fire runs them again", "[a, b, a, b]", log.toString());

        // adding the same listener twice does not run it twice
        log.clear();
        NoteEvents.addSavedListener(a);
        NoteEvents.fireSaved();
        eq("same listener added twice", "[a, b]", log.toString());

        // a removed listener stops running; removing one that is not there is harmless
        log.clear();
        NoteEvents.removeSavedListener(a);
        NoteEvents.removeSavedListener(a);
        NoteEvents.fireSaved();
        eq("removed", "[b]", log.toString());

        // a listener that throws does not stop the ones after it, and the failure does not reach the caller
        log.clear();
        Runnable boom = () -> { throw new IllegalStateException("listener failed"); };
        NoteEvents.addSavedListener(boom);
        NoteEvents.addSavedListener(a);
        NoteEvents.fireSaved();
        eq("a failing listener does not block the next", "[b, a]", log.toString());
        NoteEvents.removeSavedListener(boom);
        NoteEvents.removeSavedListener(a);

        // a listener may add another listener while the event is being delivered (no ConcurrentModificationException);
        // the new one is first called on the next fire
        log.clear();
        final Runnable late = () -> log.add("late");
        Runnable adder = () -> { log.add("adder"); NoteEvents.addSavedListener(late); };
        NoteEvents.addSavedListener(adder);
        NoteEvents.fireSaved();
        eq("added during a fire", "[b, adder]", log.toString());
        NoteEvents.fireSaved();
        eq("added listener runs next time", "[b, adder, b, adder, late]", log.toString());
        NoteEvents.removeSavedListener(adder);
        NoteEvents.removeSavedListener(late);
        NoteEvents.removeSavedListener(b);

        // several threads adding and firing at the same time: nothing is lost or corrupted
        final AtomicInteger calls = new AtomicInteger();
        final int threads = 8, perThread = 50;
        final CountDownLatch go = new CountDownLatch(1), done = new CountDownLatch(threads);
        final List<Runnable> mine = new ArrayList<>();
        for (int i = 0; i < threads; i++) {
            final Runnable r = calls::incrementAndGet;
            mine.add(r);
            new Thread(() -> {
                try {
                    go.await();
                    for (int k = 0; k < perThread; k++) {
                        NoteEvents.addSavedListener(r);
                        NoteEvents.fireSaved();
                    }
                } catch (InterruptedException ignored) {
                } finally {
                    done.countDown();
                }
            }).start();
        }
        go.countDown();
        done.await();
        checks++;
        if (calls.get() < threads * perThread) {   // each thread's own fire ran at least its own listener
            System.err.println("FAIL threads: only " + calls.get() + " calls");
            System.exit(1);
        }
        for (Runnable r : mine) NoteEvents.removeSavedListener(r);
        int before = calls.get();
        NoteEvents.fireSaved();
        eq("all removed after the threads", before, calls.get());

        System.out.println("OK: " + checks + " checks passed");
    }
}
