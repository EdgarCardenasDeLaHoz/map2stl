/**
 * Pure extraction of interpolateCurve from ui/curve-editor.js.
 * The original closes over `curvePoints`; here we accept it as a parameter
 * so the function is testable without DOM or module state.
 *
 * Source: app/client/static/js/modules/ui/curve-editor.js:439
 */
export function interpolateCurve(x, curvePoints) {
    if (curvePoints.length < 2) return x;
    let left  = curvePoints[0];
    let right = curvePoints[curvePoints.length - 1];
    for (let i = 0; i < curvePoints.length - 1; i++) {
        if (curvePoints[i].x <= x && curvePoints[i + 1].x >= x) {
            left  = curvePoints[i];
            right = curvePoints[i + 1];
            break;
        }
    }
    const t = (x - left.x) / (right.x - left.x || 1);
    return left.y + t * (right.y - left.y);
}
