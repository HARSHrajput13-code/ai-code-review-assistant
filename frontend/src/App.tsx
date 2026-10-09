import { useEffect, useState } from "react";
import { createApi, type Api, type Capabilities } from "./api/client";
import { EditorPanel } from "./features/review/EditorPanel";
import { ResultsPanel } from "./features/review/ResultsPanel";
import { isBusy, useReviewSession } from "./features/review/useReviewSession";

const defaultApi = createApi();

/** Loads the capabilities once; without them the backend is unreachable (CIS §15.3). */
export default function App({ api = defaultApi }: { api?: Api }) {
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [unreachable, setUnreachable] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    api.capabilities(controller.signal).then(
      (result) => (result.ok ? setCapabilities(result.data) : setUnreachable(true)),
      () => {
        if (!controller.signal.aborted) setUnreachable(true);
      },
    );
    return () => controller.abort();
  }, [api, attempt]);

  let content = <p className="text-slate-600">Loading…</p>;
  if (unreachable) {
    content = (
      <section role="alert" className="flex flex-col items-start gap-2">
        <h2 className="text-lg font-semibold">Backend unreachable</h2>
        <p>The review service could not be reached.</p>
        <button
          type="button"
          className="rounded border border-slate-400 px-3 py-1"
          onClick={() => {
            setUnreachable(false);
            setAttempt((n) => n + 1);
          }}
        >
          Retry
        </button>
      </section>
    );
  } else if (capabilities) {
    content = <Workspace api={api} capabilities={capabilities} />;
  }
  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white px-6 py-3">
        <h1 className="text-xl font-semibold">AI Code Review Assistant</h1>
      </header>
      <main className="p-6">{content}</main>
    </div>
  );
}

/** The editor first, then the results; side by side from 1024 px (§15.2). */
function Workspace({ api, capabilities }: { api: Api; capabilities: Capabilities }) {
  const [language, setLanguage] = useState<string>(capabilities.languages[0]?.id ?? "");
  const [code, setCode] = useState("");
  const { state, submit } = useReviewSession(api, capabilities.review_timeout_seconds);
  const { snapshot } = state;
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <EditorPanel
        capabilities={capabilities}
        language={language}
        onLanguageChange={setLanguage}
        code={code}
        onCodeChange={setCode}
        busy={isBusy(state.phase)}
        rejection={state.phase === "idle" ? state.error : null}
        onReview={() => submit({ language, source_code: code })}
      />
      <ResultsPanel
        state={state}
        limits={capabilities.limits}
        onRetry={() => {
          if (snapshot) submit(snapshot);
        }}
      />
    </div>
  );
}
