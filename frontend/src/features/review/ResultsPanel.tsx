// The results side: progress, the failure state, and the score, summary and issues (CIS §15.3,
// §15.6). Every server text is rendered as a React text node, never as HTML or Markdown (§15.7).
import { useEffect, useState } from "react";
import type { Category, Issue, Limits, ReviewResult, ScoreBand, ScoreCap, Severity } from "../../api/client";
import { errorMessage } from "../../lib/errorMessages";
import { isBusy, type Phase, type SessionError, type SessionState } from "./useReviewSession";

const BAND_TEXT: Record<ScoreBand, string> = {
  EXCELLENT: "Few or no concerns detected",
  GOOD: "Minor concerns detected",
  FAIR: "Notable concerns detected",
  POOR: "Significant concerns detected",
  VERY_POOR: "Serious problems detected",
};
const CATEGORY_TEXT: Record<Category, string> = {
  CORRECTNESS: "Correctness",
  SECURITY: "Security",
  PERFORMANCE: "Performance",
  READABILITY: "Readability",
  MAINTAINABILITY: "Maintainability",
  BEST_PRACTICE: "Best practice",
};
const CAP_TEXT: Record<ScoreCap, string> = {
  UNPARSEABLE_SOURCE: "The score is limited to 20 because the code could not be parsed.",
  CRITICAL_ISSUE: "The score is limited to 40 because a critical issue was found.",
  HIGH_CORRECTNESS_OR_SECURITY:
    "The score is limited to 70 because a high-severity correctness or security issue was found.",
};
const SEVERITIES: Severity[] = ["CRITICAL", "HIGH", "MEDIUM", "LOW"];
const SEVERITY_TEXT: Record<Severity, string> = { CRITICAL: "Critical", HIGH: "High", MEDIUM: "Medium", LOW: "Low" };
const SEVERITY_STYLE: Record<Severity, string> = {
  CRITICAL: "bg-red-700 text-white",
  HIGH: "bg-orange-600 text-white",
  MEDIUM: "bg-amber-300 text-slate-900",
  LOW: "bg-slate-200 text-slate-900",
};
export const DISCLAIMER =
  "Automated review score based on the configured analysis criteria. It is not a guarantee of correctness or security.";
// The AI covers every category, so a category is unassessed only when the AI produced no result (§13.2).
const NOT_ASSESSED_HINT = "Not assessed: AI analysis, which covers this category, produced no result.";

interface Props {
  state: SessionState;
  limits: Limits;
  onRetry: () => void;
}

export function ResultsPanel({ state, limits, onRetry }: Props) {
  const { phase, review, error } = state;
  if (isBusy(phase)) return <ReviewProgress phase={phase} queued={review?.status === "PENDING"} />;
  if (phase === "failed") return <ReviewError error={error} limits={limits} onRetry={onRetry} />;
  const result = review?.result;
  if (phase === "idle" || !result) {
    return <p className="text-slate-600">Enter code and select Review to see the results here.</p>;
  }
  return (
    <div className="flex flex-col gap-6">
      <ScoreCard result={result} />
      <section aria-label="Summary">
        <h2 className="text-lg font-semibold">Summary</h2>
        {result.summary.source === "GENERATED" && (
          <p className="text-sm text-slate-600">Generated from static analysis</p>
        )}
        <p className="whitespace-pre-line">{result.summary.text}</p>
      </section>
      <IssueList result={result} />
    </div>
  );
}

function useElapsedSeconds(): number {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const start = Date.now();
    const timer = setInterval(() => setSeconds(Math.floor((Date.now() - start) / 1000)), 1000);
    return () => clearInterval(timer);
  }, []);
  return seconds;
}

function ReviewProgress({ phase, queued }: { phase: Phase; queued: boolean }) {
  const elapsed = useElapsedSeconds();
  const improving = phase === "generating_improvement";
  return (
    <section aria-label="Review progress" className="flex flex-col gap-2">
      <h2 className="text-lg font-semibold">Reviewing…</h2>
      {queued && <p>Queued</p>}
      <ol className="list-decimal pl-6">
        <li>Analyzing code: {improving ? "done" : "in progress"}</li>
        <li>Generating improved code: {improving ? "in progress" : "pending"}</li>
      </ol>
      <p className="text-sm text-slate-600">Elapsed: {elapsed} s</p>
    </section>
  );
}

function ReviewError({ error, limits, onRetry }: { error: SessionError | null; limits: Limits; onRetry: () => void }) {
  const code = error?.code ?? null;
  return (
    <section role="alert" aria-label="Review failed" className="flex flex-col items-start gap-2">
      <p>{errorMessage(code ?? "", error?.message ?? "", limits)}</p>
      {code && <p className="text-xs text-slate-500">{code}</p>}
      <button type="button" className="rounded border border-slate-400 px-3 py-1" onClick={onRetry}>
        Try again
      </button>
    </section>
  );
}

function ScoreCard({ result }: { result: ReviewResult }) {
  const { score } = result;
  return (
    <section aria-label="Score" className="flex flex-col gap-2">
      <h2 className="text-lg font-semibold">Score</h2>
      <p>
        <span className="text-3xl font-semibold">{score.overall}</span> / 100 · {BAND_TEXT[score.band]}
      </p>
      {score.provisional && <p>Provisional: based on {score.assessed_weight}% of the scoring criteria</p>}
      <ul className="flex flex-col gap-1">
        {score.categories.map((row) => (
          <li key={row.category} className="flex items-center gap-3">
            <span className="w-36">{CATEGORY_TEXT[row.category]}</span>
            {row.assessed && row.score !== null ? (
              <>
                <span aria-hidden="true" data-testid="score-bar" className="h-2 w-32 rounded bg-slate-200">
                  <span className="block h-2 rounded bg-slate-700" style={{ width: `${row.score}%` }} />
                </span>
                <span>{row.score}</span>
              </>
            ) : (
              <span title={NOT_ASSESSED_HINT}>Not assessed</span>
            )}
          </li>
        ))}
      </ul>
      {score.caps_applied.map((cap) => (
        <p key={cap}>{CAP_TEXT[cap]}</p>
      ))}
      <p className="text-sm text-slate-600">{DISCLAIMER}</p>
    </section>
  );
}

const locationText = (location: { start_line: number; end_line: number } | null): string =>
  location === null
    ? "Location not determined"
    : location.start_line === location.end_line
      ? `Line ${location.start_line}`
      : `Lines ${location.start_line}–${location.end_line}`;

function IssueList({ result }: { result: ReviewResult }) {
  const { issues, total_issue_count, severity_counts } = result;
  return (
    <section aria-label="Issues" className="flex flex-col gap-2">
      <h2 className="text-lg font-semibold">Issues ({total_issue_count})</h2>
      <p className="text-sm">{SEVERITIES.map((s) => `${SEVERITY_TEXT[s]} ${severity_counts[s]}`).join(" · ")}</p>
      {result.issues_truncated && (
        <p>
          Showing {issues.length} of {total_issue_count}
        </p>
      )}
      {issues.length === 0 ? (
        <p>
          No issues were detected by the configured analysis.
          {result.score.provisional && " Some analysis did not run, so this result is incomplete."}
        </p>
      ) : (
        <ol className="flex flex-col gap-2">
          {issues.map((issue, index) => (
            <IssueCard key={issue.issue_id} issue={issue} open={index === 0} />
          ))}
        </ol>
      )}
    </section>
  );
}

function IssueCard({ issue, open }: { issue: Issue; open: boolean }) {
  return (
    <li className="rounded border border-slate-300 bg-white">
      <details open={open}>
        <summary className="flex cursor-pointer flex-wrap items-center gap-2 p-3">
          <span className={`rounded px-2 text-sm ${SEVERITY_STYLE[issue.severity]}`}>
            {SEVERITY_TEXT[issue.severity]}
          </span>
          <span className="text-sm text-slate-600">{CATEGORY_TEXT[issue.category]}</span>
          <span className="font-medium">{issue.title}</span>
          <span className="text-sm text-slate-600">{locationText(issue.location)}</span>
          {issue.occurrence_count > 1 && (
            <span className="text-sm text-slate-600">{issue.occurrence_count} occurrences</span>
          )}
        </summary>
        <div className="flex flex-col gap-2 px-3 pb-3">
          <h3 className="font-medium">Problem</h3>
          <p>{issue.summary}</p>
          <h3 className="font-medium">Why it matters</h3>
          <p>{issue.impact}</p>
          <h3 className="font-medium">Recommendation</h3>
          <p>{issue.recommendation}</p>
          {issue.additional_locations.length > 0 && (
            <p className="text-sm">Also at: {issue.additional_locations.map(locationText).join(", ")}</p>
          )}
          <details>
            <summary className="cursor-pointer text-sm text-slate-600">Details</summary>
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 text-sm">
              <dt>Provenance</dt>
              <dd>{issue.provenance}</dd>
              <dt>Confidence</dt>
              <dd>{issue.confidence}</dd>
              <dt>Severity source</dt>
              <dd>{issue.severity_source}</dd>
              <dt>Sources</dt>
              <dd>{issue.sources.map((s) => (s.rule_key ? `${s.origin} (${s.rule_key})` : s.origin)).join(", ")}</dd>
            </dl>
          </details>
        </div>
      </details>
    </li>
  );
}
