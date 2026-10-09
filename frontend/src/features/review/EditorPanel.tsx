import { useEffect, useState } from "react";
import type { Capabilities } from "../../api/client";
import { CodeEditor } from "../../editor/CodeEditor";
import { errorMessage } from "../../lib/errorMessages";
import { byteLength, lineCount } from "../../lib/inputMetrics";
import type { SessionError } from "./useReviewSession";

interface Props {
  capabilities: Capabilities;
  language: string;
  onLanguageChange: (language: string) => void;
  code: string;
  onCodeChange: (code: string) => void;
  busy: boolean;
  rejection: SessionError | null; // a server 4xx while idle (§15.5 step 6)
  onReview: () => void;
}

const measure = (code: string) => ({ bytes: byteLength(code), lines: lineCount(code) });

/** LanguageSelect, CodeEditor, InputMeter, ValidationMessage and the Review button (§15.3). */
export function EditorPanel({ capabilities, language, onLanguageChange, code, onCodeChange, busy, rejection, onReview }: Props) {
  const { limits, languages } = capabilities;
  const [meter, setMeter] = useState(() => measure(code));
  useEffect(() => {
    const timer = setTimeout(() => setMeter(measure(code)), 150);
    return () => clearTimeout(timer);
  }, [code]);

  const now = measure(code);
  const tooLarge = now.bytes > limits.max_source_bytes || now.lines > limits.max_source_lines;
  const invalid = code.trim() === "" || tooLarge || language === "";
  const message = tooLarge
    ? errorMessage("INPUT_TOO_LARGE", "", limits)
    : rejection && errorMessage(rejection.code ?? "", rejection.message, limits);
  const monacoLanguage = languages.find((option) => option.id === language)?.monaco_language ?? "plaintext";

  return (
    <section aria-label="Code" className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-4">
        <label className="flex items-center gap-2">
          Language
          <select
            className="rounded border border-slate-300 bg-white px-2 py-1"
            value={language}
            onChange={(event) => onLanguageChange(event.target.value)}
          >
            {languages.map((option) => (
              <option key={option.id} value={option.id}>
                {option.display_name}
              </option>
            ))}
          </select>
        </label>
        <p className="text-sm text-slate-600">
          {meter.bytes} / {limits.max_source_bytes} bytes · {meter.lines} / {limits.max_source_lines} lines
        </p>
      </div>
      <div className="overflow-hidden rounded border border-slate-300 bg-white">
        <CodeEditor language={monacoLanguage} value={code} onChange={onCodeChange} />
      </div>
      {message && (
        <p role="alert" className="text-sm text-red-700">
          {message}
        </p>
      )}
      <button
        type="button"
        className="self-start rounded bg-slate-900 px-4 py-2 text-white disabled:cursor-not-allowed disabled:bg-slate-400"
        disabled={invalid || busy}
        onClick={onReview}
      >
        {busy ? "Reviewing…" : "Review"}
      </button>
    </section>
  );
}
