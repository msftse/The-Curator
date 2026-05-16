import type { SkillStatus, ClassifierStatus } from "@/lib/api/types";

type Tone = {
  classes: string;
  dot: string;
};

const STATUS_TONES: Record<SkillStatus, Tone> = {
  pending: {
    classes: "bg-warning-bg text-warning-fg border border-warning-border",
    dot: "bg-ms-yellow",
  },
  classified: {
    classes: "bg-info-bg text-info-fg border border-info-border",
    dot: "bg-ms-blue",
  },
  approved: {
    classes: "bg-success-bg text-success-fg border border-success-border",
    dot: "bg-ms-green",
  },
  rejected: {
    classes: "bg-danger-bg text-danger-fg border border-danger-border",
    dot: "bg-ms-red",
  },
  stale: {
    classes: "bg-bg-2 text-muted border border-line-2",
    dot: "bg-muted",
  },
  archived: {
    classes: "bg-bg-2 text-ink-2 border border-line-2",
    dot: "bg-ink-2",
  },
};

const CLASSIFIER_TONES: Record<ClassifierStatus, Tone> = {
  queued: STATUS_TONES.pending,
  running: STATUS_TONES.classified,
  done: STATUS_TONES.approved,
  failed: STATUS_TONES.rejected,
};

function Badge({ tone, label }: { tone: Tone; label: string }) {
  return (
    <span className={"ms-badge " + tone.classes}>
      <span
        aria-hidden
        className={"h-1.5 w-1.5 rounded-full " + tone.dot}
      />
      {label}
    </span>
  );
}

export function StatusBadge({ status }: { status: SkillStatus }) {
  return <Badge tone={STATUS_TONES[status]} label={status} />;
}

export function ClassifierBadge({ status }: { status: ClassifierStatus }) {
  return (
    <Badge tone={CLASSIFIER_TONES[status]} label={`classifier: ${status}`} />
  );
}
