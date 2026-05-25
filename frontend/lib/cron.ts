/**
 * Tiny cron utilities — validation (mirrors `backend/models/schedule.py`)
 * and an approximate next-run computation used by the schedule editor's
 * live preview.
 *
 * The validation regex deliberately rejects what the backend rejects:
 *   - empty string
 *   - `@`-shorthand forms (the K8s spec accepts them, but the reconciler
 *     compares schedules with string-equality so we restrict to 5-field).
 *   - named months / weekdays (`MON`, `JAN`).
 *
 * `computeNextRun` walks forward minute-by-minute up to a 7-day horizon
 * and returns the first matching wall-clock time. Fine for an admin
 * preview; not a substitute for a real cron library.
 */
const FIELD = /^(\*|\d+(-\d+)?)(\/\d+)?(,(\*|\d+(-\d+)?)(\/\d+)?)*$/;
const BOUNDS: [number, number][] = [
  [0, 59],
  [0, 23],
  [1, 31],
  [1, 12],
  [0, 6],
];

function checkField(raw: string, [lo, hi]: [number, number]): void {
  if (!FIELD.test(raw)) {
    throw new Error(`cron field '${raw}' invalid`);
  }
  for (const piece of raw.split(",")) {
    const [valuePart, stepPart] = piece.split("/");
    if (stepPart !== undefined) {
      const step = Number(stepPart);
      if (!Number.isInteger(step) || step < 1 || step > hi) {
        throw new Error(`cron field ${raw} step out of range [1,${hi}]`);
      }
    }
    if (valuePart === "*") continue;
    if (valuePart.includes("-")) {
      const [aStr, bStr] = valuePart.split("-");
      const a = Number(aStr);
      const b = Number(bStr);
      if (a < lo || b > hi || a > b) {
        throw new Error(`cron field ${raw} out of range [${lo},${hi}]`);
      }
    } else {
      const n = Number(valuePart);
      if (n < lo || n > hi) {
        throw new Error(`cron field ${raw} out of range [${lo},${hi}]`);
      }
    }
  }
}

export function validateCron(expr: string): string {
  const trimmed = (expr || "").trim();
  if (!trimmed) throw new Error("cron expression cannot be empty");
  if (trimmed.startsWith("@")) {
    throw new Error("@-shorthand cron expressions are not supported");
  }
  const fields = trimmed.split(/\s+/);
  if (fields.length !== 5) {
    throw new Error("cron expression must have exactly 5 fields");
  }
  fields.forEach((f, i) => checkField(f, BOUNDS[i]));
  return trimmed;
}

function expand(field: string, lo: number, hi: number): Set<number> {
  const out = new Set<number>();
  for (const piece of field.split(",")) {
    const [range, stepStr] = piece.split("/");
    const step = stepStr ? Number(stepStr) : 1;
    if (!Number.isInteger(step) || step < 1) {
      throw new Error(`cron field '${field}' has invalid step`);
    }
    let a: number, b: number;
    if (range === "*") {
      a = lo;
      b = hi;
    } else if (range.includes("-")) {
      const [aStr, bStr] = range.split("-");
      a = Number(aStr);
      b = Number(bStr);
    } else {
      a = b = Number(range);
    }
    for (let v = a; v <= b; v += step) out.add(v);
  }
  return out;
}

/**
 * Walk forward minute-by-minute starting `from` until we hit a match, or
 * `null` if no match in the next 7 days. Operates entirely in the local
 * (browser) timezone — the value is for visual preview only; the actual
 * CronJob runs in cluster TZ.
 */
export function computeNextRun(expr: string, from: Date): Date | null {
  const [m, h, dom, mon, dow] = validateCron(expr).split(/\s+/);
  const mins = expand(m, 0, 59);
  const hours = expand(h, 0, 23);
  const doms = expand(dom, 1, 31);
  const mons = expand(mon, 1, 12);
  const dows = expand(dow, 0, 6);
  const domRestricted = dom !== "*";
  const dowRestricted = dow !== "*";

  const candidate = new Date(from.getTime());
  candidate.setSeconds(0, 0);
  candidate.setMinutes(candidate.getMinutes() + 1);

  const horizon = 7 * 24 * 60;
  for (let i = 0; i < horizon; i++) {
    if (
      mins.has(candidate.getMinutes()) &&
      hours.has(candidate.getHours()) &&
      matchesCronDay(
        doms.has(candidate.getDate()),
        dows.has(candidate.getDay()),
        domRestricted,
        dowRestricted,
      ) &&
      mons.has(candidate.getMonth() + 1)
    ) {
      return new Date(candidate.getTime());
    }
    candidate.setMinutes(candidate.getMinutes() + 1);
  }
  return null;
}

function matchesCronDay(
  domMatches: boolean,
  dowMatches: boolean,
  domRestricted: boolean,
  dowRestricted: boolean,
): boolean {
  if (domRestricted && dowRestricted) return domMatches || dowMatches;
  return domMatches && dowMatches;
}
