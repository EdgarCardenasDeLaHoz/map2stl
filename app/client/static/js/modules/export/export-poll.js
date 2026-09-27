/**
 * modules/export/export-poll.js — when to stop polling an export task, pure.
 *
 * No DOM, no window: export-handlers.js drives it from its poll loop, and
 * tests/js/exportPoll.test.js exercises it directly.
 *
 * A first city build can take over 10 minutes (Granada: 611 s), and a single
 * step ("Building model...") can hold one progress value for minutes. So
 * there is no wall-clock limit: the client keeps polling while the status
 * changes or the server reports the worker alive (`alive`, the heartbeat), and
 * gives up only after EXPORT_STALL_TIMEOUT_MS with neither, or when the task
 * is gone (HTTP 404).
 */

/** Give up after this long without a status change or a heartbeat. */
export const EXPORT_STALL_TIMEOUT_MS = 3 * 60 * 1000;

/**
 * Track poll results and report a stall.
 *
 * `observe(status, now)` takes one poll's JSON (or null when the poll itself
 * failed, e.g. a network blip) and returns `{ stalled, idleMs }`: `idleMs` is
 * the time since the last sign of life (a changed progress/message/status, or
 * `alive: true`), and `stalled` is true once that reaches `stallMs`.
 *
 * @param {number} startMs  Time the export started (Date.now())
 * @param {number} [stallMs=EXPORT_STALL_TIMEOUT_MS]
 */
export function createStallWatch(startMs, stallMs = EXPORT_STALL_TIMEOUT_MS) {
    let lastSig = null;
    let lastLife = startMs;
    return {
        observe(status, now) {
            if (status) {
                const sig = `${status.status}|${status.progress}|${status.message}`;
                if (sig !== lastSig || status.alive === true) lastLife = now;
                lastSig = sig;
            }
            const idleMs = now - lastLife;
            return { stalled: idleMs >= stallMs, idleMs };
        },
    };
}

/** "45 s", "4:05", "1:02:03" — elapsed time for the progress text. */
export function formatElapsed(ms) {
    const total = Math.max(0, Math.floor((Number(ms) || 0) / 1000));
    if (total < 60) return `${total} s`;
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = String(total % 60).padStart(2, '0');
    return h ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`;
}

/** Progress line: "Building model... (4:05)". */
export function exportProgressText(message, elapsedMs) {
    return `${message || 'Working...'} (${formatElapsed(elapsedMs)})`;
}
